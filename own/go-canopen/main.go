// SPDX-License-Identifier: MIT

// Command canopen-interop is go-canopen, OT Fabric's own CANopen stack,
// behind the canopen-interop container contract (docs/CONTAINER_CONTRACT.md):
// the image canopen-interop-otfabric. It is not an independent
// implementation (README.md). The CANopen work of every operation is done
// by go-canopen's public API; this file is the command line, the documents
// and the exit codes.
package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/otfabric/go-canbus"
	"github.com/otfabric/go-canbus/socketcan"
	"github.com/otfabric/go-canopen"
)

// Set by the build (-ldflags -X).
var (
	adapterVersion   = "dev"
	upstreamVersion  = "unknown"
	upstreamRevision = "0000000000000000000000000000000000000000"
)

const (
	schemaVersion = "1.0"
	adapterName   = "otfabric"
	fixtureRoot   = "/fixtures"
)

// Exit codes of the contract.
const (
	exitOK       = 0
	exitInternal = 1
	exitUsage    = 2
	exitTimeout  = 3
	exitEnv      = 4
	exitProtocol = 5
)

// operations in the contract's order; every one is implemented.
var operations = []string{
	"device", "sdo-upload", "sdo-download", "nmt", "listen",
	"pdo-configure", "pdo-receive", "sync", "eds-check", "cia402",
	"lss", "rpdo-send", "mpdo-send", "mpdo-receive", "boot", "cdcf",
}

// features: what go-canopen's public API offers. Every one is true: the
// library has each service (see README.md).
var features = map[string]bool{
	"sdoExpedited": true, "sdoSegmented": true, "sdoBlock": true,
	"deviceSegmented": true, "deviceBlock": true,
	"heartbeatProducer": true, "emcy": true, "tick": true,
	"deviceTpdo": true, "deviceRpdo": true, "deviceLimits": true,
	"listenBootup": true, "listenHeartbeat": true, "listenEmcy": true, "listenAllNodes": true,
	"pdoConfigure": true, "pdoReceive": true, "sync": true, "edsCheck": true, "cia402": true,
	"lssMaster": true, "lssBitTiming": true, "lssFastscan": true, "lssAssignAll": true,
	"lssSlave": true, "lssSlaveBitTiming": true, "lssSlaveStore": true,
	"rpdoSend": true, "mpdoDam": true, "mpdoSam": true, "boot": true,
	"cdcfDcfgen": true, "cdcfObjects": true, "syncWindowRpdo": true,
}

// ── errors ──────────────────────────────────────────────────────────────────

// adapterError ends an operation with an error document and an exit code.
type adapterError struct {
	exit  int
	code  string
	msg   string
	extra map[string]any
}

func (e *adapterError) Error() string { return e.msg }

func usagef(format string, a ...any) error {
	return &adapterError{exit: exitUsage, code: "usage", msg: fmt.Sprintf(format, a...)}
}

func unsupportedf(format string, a ...any) error {
	return &adapterError{exit: exitUsage, code: "unsupported", msg: fmt.Sprintf(format, a...)}
}

func timeoutf(format string, a ...any) error {
	return &adapterError{exit: exitTimeout, code: "timeout", msg: fmt.Sprintf(format, a...)}
}

func protocolf(format string, a ...any) error {
	return &adapterError{exit: exitProtocol, code: "protocol", msg: fmt.Sprintf(format, a...)}
}

func interfacef(format string, a ...any) error {
	return &adapterError{exit: exitEnv, code: "interface", msg: fmt.Sprintf(format, a...)}
}

// with adds fields to the error document.
func with(err error, kv ...any) error {
	var ae *adapterError
	if !errors.As(err, &ae) {
		return err
	}
	if ae.extra == nil {
		ae.extra = map[string]any{}
	}
	for i := 0; i+1 < len(kv); i += 2 {
		ae.extra[kv[i].(string)] = kv[i+1]
	}
	return ae
}

// classify turns an error of go-canopen into the contract's: an SDO abort
// (exit 5, abortCode), a timeout (exit 3; an SDO client timeout keeps its
// abort code 0x05040000), else exit 5 with the message.
func classify(what string, err error) error {
	if err == nil {
		return nil
	}
	var ae *adapterError
	if errors.As(err, &ae) {
		return err
	}
	var abort *canopen.AbortError
	timedOut := errors.Is(err, canopen.ErrTimeout) || errors.Is(err, context.DeadlineExceeded)
	if timedOut {
		e := timeoutf("%s: %v", what, err)
		if errors.As(err, &abort) {
			e = with(e, "abortCode", hexU32(uint32(abort.Code)))
		}
		return e
	}
	if errors.As(err, &abort) {
		return with(protocolf("%s: %v", what, err), "abortCode", hexU32(uint32(abort.Code)))
	}
	return protocolf("%s: %v", what, err)
}

// ── documents ───────────────────────────────────────────────────────────────

var outMu sync.Mutex

// emit writes one JSON document as one line on stdout.
func emit(doc map[string]any) {
	b, err := json.Marshal(doc)
	if err != nil {
		b = []byte(fmt.Sprintf(`{"schemaVersion":"1.0","type":"error","code":"internal","message":%q}`, err.Error()))
	}
	outMu.Lock()
	defer outMu.Unlock()
	_, _ = os.Stdout.Write(append(b, '\n'))
}

func event(kind string, kv ...any) map[string]any {
	doc := map[string]any{"schemaVersion": schemaVersion, "type": kind}
	for i := 0; i+1 < len(kv); i += 2 {
		doc[kv[i].(string)] = kv[i+1]
	}
	return doc
}

func result(op string, kv ...any) map[string]any {
	return event("result", append([]any{"operation", op}, kv...)...)
}

func logf(format string, a ...any) { fmt.Fprintf(os.Stderr, "otfabric: "+format+"\n", a...) }

func hexU32(v uint32) string            { return fmt.Sprintf("0x%08X", v) }
func hexIndex(i uint16) string          { return fmt.Sprintf("0x%04X", i) }
func hexKey(i uint16, sub uint8) string { return fmt.Sprintf("0x%04X:%02X", i, sub) }
func hexData(b []byte) string           { return hex.EncodeToString(b) }

func capabilitiesDoc() map[string]any {
	return map[string]any{
		"schemaVersion": schemaVersion,
		"adapter":       adapterName,
		"operations":    operations,
		"features":      features,
		"upstream": map[string]any{
			"name":              "go-canopen",
			"version":           upstreamVersion,
			"revision":          upstreamRevision,
			"independentEngine": false,
		},
	}
}

func versionDoc() map[string]any {
	return map[string]any{
		"schemaVersion":  schemaVersion,
		"adapter":        adapterName,
		"upstream":       "go-canopen",
		"repository":     "https://github.com/otfabric/go-canopen",
		"version":        upstreamVersion,
		"revision":       upstreamRevision,
		"license":        "MIT",
		"stack":          "go-canopen " + upstreamVersion + " on go-canbus socketcan (OT Fabric's own stack, not independent)",
		"adapterVersion": adapterVersion,
	}
}

// ── command line ────────────────────────────────────────────────────────────

// multi is a repeatable string flag.
type multi []string

func (m *multi) String() string     { return strings.Join(*m, ",") }
func (m *multi) Set(s string) error { *m = append(*m, s); return nil }

// opts are the flags of one operation, parsed.
type opts struct {
	fs        *flag.FlagSet
	iface     string
	timeoutMs int
	set       map[string]bool
}

func newOpts(name string, bus bool) *opts {
	o := &opts{fs: flag.NewFlagSet(name, flag.ContinueOnError)}
	o.fs.SetOutput(io.Discard)
	if bus {
		o.fs.StringVar(&o.iface, "interface", "vcan0", "")
		o.fs.IntVar(&o.timeoutMs, "timeout-ms", 5000, "")
	}
	return o
}

func (o *opts) parse(args []string) error {
	if err := o.fs.Parse(args); err != nil {
		return usagef("%s: %v", o.fs.Name(), err)
	}
	if o.fs.NArg() > 0 {
		return usagef("%s: unexpected argument %q", o.fs.Name(), o.fs.Arg(0))
	}
	o.set = map[string]bool{}
	o.fs.Visit(func(f *flag.Flag) { o.set[f.Name] = true })
	return nil
}

func (o *opts) require(names ...string) error {
	for _, n := range names {
		if !o.set[n] {
			return usagef("%s: --%s is required", o.fs.Name(), n)
		}
	}
	return nil
}

func (o *opts) timeout() time.Duration { return time.Duration(o.timeoutMs) * time.Millisecond }

// checkBus validates the common flags and the interface (exit 4).
func (o *opts) checkBus() error {
	if o.timeoutMs <= 0 {
		return usagef("--timeout-ms must be positive")
	}
	return checkInterface(o.iface)
}

func checkInterface(name string) error {
	if name == "" || strings.ContainsAny(name, "/.") && name != filepath.Base(name) {
		return interfacef("interface %q is not valid", name)
	}
	base := filepath.Join("/sys/class/net", name)
	if st, err := os.Stat(base); err != nil || !st.IsDir() {
		return interfacef("interface %s does not exist", name)
	}
	flags, err := os.ReadFile(filepath.Join(base, "flags"))
	if err != nil {
		return interfacef("interface %s: %v", name, err)
	}
	v, err := strconv.ParseUint(strings.TrimPrefix(strings.TrimSpace(string(flags)), "0x"), 16, 32)
	if err != nil || v&1 == 0 {
		return interfacef("interface %s is down", name)
	}
	state, _ := os.ReadFile(filepath.Join(base, "operstate"))
	if strings.TrimSpace(string(state)) == "down" {
		return interfacef("interface %s is down", name)
	}
	return nil
}

// openBus opens a SocketCAN socket on the interface: go-canbus's transport.
func openBus(name string) (*canbus.Bus, error) {
	c, err := socketcan.Open(name, socketcan.WithReceiveBuffer(1<<20))
	if err != nil {
		return nil, interfacef("socketcan %s: %v", name, err)
	}
	b, err := canbus.NewBus(c)
	if err != nil {
		_ = c.Close()
		return nil, interfacef("bus %s: %v", name, err)
	}
	return b, nil
}

// ── values ──────────────────────────────────────────────────────────────────

func parseIndex(s string) (uint16, error) {
	v, err := strconv.ParseUint(strings.TrimPrefix(strings.TrimPrefix(s, "0x"), "0X"), 16, 16)
	if err != nil {
		return 0, usagef("index %q is not hex 0..0xFFFF", s)
	}
	return uint16(v), nil
}

func parseSub(s string) (uint8, error) {
	v, err := strconv.ParseUint(s, 0, 8)
	if err != nil {
		return 0, usagef("sub-index %q is not a number 0..255", s)
	}
	return uint8(v), nil
}

// parseNode: 1..127; zero allows 0 (nmt); lssUnconf allows 255 (device).
func parseNode(s string, zero, lssUnconf bool) (canopen.NodeID, error) {
	v, err := strconv.ParseUint(s, 0, 8)
	if err == nil && lssUnconf && v == 0xFF {
		return 0xFF, nil
	}
	if err != nil || v > 127 || (v == 0 && !zero) {
		return 0, usagef("node-ID %q out of range", s)
	}
	return canopen.NodeID(v), nil
}

func parseU32(s string) (uint32, error) {
	v, err := strconv.ParseUint(s, 0, 32)
	if err != nil {
		return 0, usagef("%q is not a 32-bit number", s)
	}
	return uint32(v), nil
}

func parseHex(s string) ([]byte, error) {
	b, err := hex.DecodeString(s)
	if err != nil {
		return nil, usagef("data %q is not hex", s)
	}
	return b, nil
}

var (
	reKey      = regexp.MustCompile(`^(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2})$`)
	reMapEntry = regexp.MustCompile(`^(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2}):(\d+)$`)
	reSet      = regexp.MustCompile(`^(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2})=([0-9A-Fa-f]+)$`)
	reNodeVal  = regexp.MustCompile(`^(?:(\d+):)?(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2})=(-?(?:0x[0-9A-Fa-f]+|\d+))$`)
	reEMCY     = regexp.MustCompile(`^(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]*)$`)
	reDispatch = regexp.MustCompile(`^(\d+):(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2}):(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2})$`)
	reRange    = regexp.MustCompile(`^(?:0x)?([0-9A-Fa-f]{1,4})-(?:0x)?([0-9A-Fa-f]{1,4})$`)
)

func hex16(s string) uint16 { v, _ := strconv.ParseUint(s, 16, 16); return uint16(v) }
func hex8(s string) uint8   { v, _ := strconv.ParseUint(s, 16, 8); return uint8(v) }

// objKey is IDX:SUB (--dump).
type objKey struct {
	index uint16
	sub   uint8
}

func parseKey(s string) (objKey, error) {
	m := reKey.FindStringSubmatch(s)
	if m == nil {
		return objKey{}, usagef("%q: want IDX:SUB in hex, e.g. 0x2000:00", s)
	}
	return objKey{hex16(m[1]), hex8(m[2])}, nil
}

// nodeValue is [NODE:]IDX:SUB=VALUE (boot --sdo, cdcf --sdo).
type nodeValue struct {
	node  canopen.NodeID // 0: none given
	index uint16
	sub   uint8
	value string
}

func parseNodeValue(s string) (nodeValue, error) {
	m := reNodeVal.FindStringSubmatch(s)
	if m == nil {
		return nodeValue{}, usagef("%q: want [NODE:]IDX:SUB=VALUE, e.g. 5:0x2001:00=0x4455", s)
	}
	nv := nodeValue{index: hex16(m[2]), sub: hex8(m[3]), value: m[4]}
	if m[1] != "" {
		n, err := strconv.Atoi(m[1])
		if err != nil || n < 1 || n > 127 {
			return nodeValue{}, usagef("%q: node-ID 1..127", s)
		}
		nv.node = canopen.NodeID(n)
	}
	return nv, nil
}

// ── main ────────────────────────────────────────────────────────────────────

type handler func(args []string) error

func handlers() map[string]handler {
	return map[string]handler{
		"device":        cmdDevice,
		"sdo-upload":    cmdSDOUpload,
		"sdo-download":  cmdSDODownload,
		"nmt":           cmdNMT,
		"listen":        cmdListen,
		"pdo-configure": cmdPDOConfigure,
		"pdo-receive":   cmdPDOReceive,
		"sync":          cmdSync,
		"eds-check":     cmdEDSCheck,
		"cia402":        cmdCiA402,
		"lss":           cmdLSS,
		"rpdo-send":     cmdRPDOSend,
		"mpdo-send":     cmdMPDOSend,
		"mpdo-receive":  cmdMPDOReceive,
		"boot":          cmdBoot,
		"cdcf":          cmdCDCF,
	}
}

func run(args []string) error {
	if len(args) == 0 {
		args = []string{"print-capabilities"}
	}
	cmd, rest := args[0], args[1:]
	switch cmd {
	case "print-capabilities":
		emit(capabilitiesDoc())
		return nil
	case "print-version":
		emit(versionDoc())
		return nil
	case "print-fixture":
		if len(rest) != 1 {
			return usagef("print-fixture <path>")
		}
		return printFixture(rest[0])
	}
	h, ok := handlers()[cmd]
	if !ok {
		return usagef("unknown command %q", cmd)
	}
	return h(rest)
}

func printFixture(rel string) error {
	p := filepath.Clean(filepath.Join(fixtureRoot, rel))
	if !strings.HasPrefix(p, fixtureRoot+"/") {
		return usagef("no fixture %s", rel)
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return usagef("no fixture %s", rel)
	}
	_, err = os.Stdout.Write(b)
	return err
}

func main() {
	err := run(os.Args[1:])
	if err == nil {
		os.Exit(exitOK)
	}
	var ae *adapterError
	if !errors.As(err, &ae) {
		logf("%v", err)
		ae = &adapterError{exit: exitInternal, code: "internal", msg: err.Error()}
	}
	doc := event("error", "code", ae.code, "message", ae.msg)
	for k, v := range ae.extra {
		doc[k] = v
	}
	emit(doc)
	os.Exit(ae.exit)
}

// signalContext ends on SIGTERM/SIGINT or after d.
func signalContext(d time.Duration) (context.Context, func()) {
	ctx, cancel := context.WithTimeout(context.Background(), d)
	sctx, stop := signal.NotifyContext(ctx, syscall.SIGTERM, syscall.SIGINT)
	return sctx, func() { stop(); cancel() }
}
