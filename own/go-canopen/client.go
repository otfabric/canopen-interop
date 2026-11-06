// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/otfabric/go-canbus"
	"github.com/otfabric/go-canopen"
	"github.com/otfabric/go-canopen/cia402"
	"github.com/otfabric/go-canopen/emcy"
	"github.com/otfabric/go-canopen/master"
	"github.com/otfabric/go-canopen/nmt"
	"github.com/otfabric/go-canopen/od"
	"github.com/otfabric/go-canopen/pdo"
	"github.com/otfabric/go-canopen/sdo"
	syncobj "github.com/otfabric/go-canopen/sync"
)

// sharedEDS describes the mapped objects of a remote node's PDOs
// (pdo-receive, rpdo-send): the contract's shared test device.
const sharedEDS = "/fixtures/eds/interop-device.eds"

// sdoAdapter gives an *sdo.Client the method set of pdo.SDO.
type sdoAdapter struct{ c *sdo.Client }

func (a sdoAdapter) Upload(ctx context.Context, index uint16, sub uint8) ([]byte, error) {
	return a.c.Upload(ctx, index, sub)
}

func (a sdoAdapter) Download(ctx context.Context, index uint16, sub uint8, data []byte) error {
	return a.c.Download(ctx, index, sub, data)
}

// sdoTimeout is the client's response timeout: at most a second, within
// the operation's time limit.
func sdoTimeout(o *opts) time.Duration {
	if d := o.timeout(); d < time.Second {
		return d
	}
	return time.Second
}

// ── sdo-upload / sdo-download ───────────────────────────────────────────────

func cmdSDOUpload(args []string) error   { return sdoTransfer("sdo-upload", args) }
func cmdSDODownload(args []string) error { return sdoTransfer("sdo-download", args) }

func sdoTransfer(op string, args []string) error {
	o := newOpts(op, true)
	var nodeS, indexS, dataS string
	subS := "0"
	var block bool
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.StringVar(&indexS, "index", "", "")
	o.fs.StringVar(&subS, "subindex", "0", "")
	o.fs.BoolVar(&block, "block", false, "")
	if op == "sdo-download" {
		o.fs.StringVar(&dataS, "data", "", "")
	}
	if err := o.parse(args); err != nil {
		return err
	}
	id, err := nodeArg(o, nodeS)
	if err != nil {
		return err
	}
	if err := o.require("index"); err != nil {
		return err
	}
	index, err := parseIndex(indexS)
	if err != nil {
		return err
	}
	sub, err := parseSub(subS)
	if err != nil {
		return err
	}
	var data []byte
	if op == "sdo-download" {
		if err := o.require("data"); err != nil {
			return err
		}
		if data, err = parseHex(dataS); err != nil {
			return err
		}
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	c, err := sdo.NewClient(bus, id, sdo.WithTimeout(sdoTimeout(o)))
	if err != nil {
		return fmt.Errorf("sdo client: %w", err)
	}
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	mode := sdo.UseBlockMode(sdo.BlockNever)
	if block {
		mode = sdo.UseBlockMode(sdo.BlockAlways)
	}
	fields := []any{"node", int(id), "index", hexIndex(index), "subindex", int(sub), "block", block}
	if op == "sdo-upload" {
		b, err := c.Upload(ctx, index, sub, mode)
		if err != nil {
			return classify("SDO upload", err)
		}
		emit(result(op, append(fields, "size", len(b), "data", hexData(b))...))
		return nil
	}
	if err := c.Download(ctx, index, sub, data, mode); err != nil {
		return classify("SDO download", err)
	}
	emit(result(op, append(fields, "size", len(data))...))
	return nil
}

// ── nmt ─────────────────────────────────────────────────────────────────────

var nmtCommands = map[string]canopen.NMTCommand{
	"start":               canopen.NMTStart,
	"stop":                canopen.NMTStop,
	"pre-operational":     canopen.NMTEnterPreOperational,
	"reset-node":          canopen.NMTResetNode,
	"reset-communication": canopen.NMTResetCommunication,
}

func cmdNMT(args []string) error {
	o := newOpts("nmt", true)
	var nodeS, cmdS string
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.StringVar(&cmdS, "command", "", "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("node-id", "command"); err != nil {
		return err
	}
	id, err := parseNode(nodeS, true, false)
	if err != nil {
		return err
	}
	cmd, ok := nmtCommands[cmdS]
	if !ok {
		return usagef("--command: start, stop, pre-operational, reset-node or reset-communication")
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	if err := nmt.NewMaster(bus).Send(ctx, id, cmd); err != nil {
		return classify("NMT", err)
	}
	emit(result("nmt", "node", int(id), "command", cmdS))
	return nil
}

// ── listen ──────────────────────────────────────────────────────────────────

func stateName(s canopen.NMTState) string {
	switch s {
	case canopen.StateInitialising:
		return "initialising"
	case canopen.StatePreOperational:
		return "pre-operational"
	case canopen.StateOperational:
		return "operational"
	case canopen.StateStopped:
		return "stopped"
	}
	return fmt.Sprintf("unknown-0x%02X", uint8(s))
}

func cmdListen(args []string) error {
	o := newOpts("listen", true)
	var nodeS string
	count := 1
	o.fs.IntVar(&count, "count", 1, "")
	o.fs.StringVar(&nodeS, "node-id", "", "")
	if err := o.parse(args); err != nil {
		return err
	}
	if count < 1 {
		return usagef("--count must be at least 1")
	}
	var only canopen.NodeID
	if o.set["node-id"] {
		id, err := parseNode(nodeS, false, false)
		if err != nil {
			return err
		}
		only = id
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	events := make(chan map[string]any, 256)
	put := func(doc map[string]any) {
		select {
		case events <- doc:
		default:
		}
	}
	hb, err := nmt.NewHeartbeatConsumer(bus, nmt.WithHeartbeatHandler(func(e nmt.HeartbeatEvent) {
		if only != 0 && e.Node != only {
			return
		}
		switch e.Kind {
		case nmt.EventBootUp:
			put(event("bootup", "node", int(e.Node)))
		case nmt.EventStateChange:
			put(event("heartbeat", "node", int(e.Node), "state", stateName(e.State)))
		}
	}))
	if err != nil {
		return fmt.Errorf("heartbeat consumer: %w", err)
	}
	defer hb.Close()
	ec := emcy.NewConsumer(bus, emcy.WithHandler(func(e emcy.Event) {
		if only != 0 && e.Node != only {
			return
		}
		put(event("emcy", "node", int(e.Node), "code", fmt.Sprintf("0x%04X", e.Code),
			"register", fmt.Sprintf("0x%02X", uint8(e.Register)), "data", hexData(e.MSEF[:])))
	}))
	defer ec.Close()
	if err := hb.Start(); err != nil {
		return fmt.Errorf("heartbeat consumer: %w", err)
	}
	if err := ec.Start(); err != nil {
		return fmt.Errorf("EMCY consumer: %w", err)
	}
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	emit(event("ready"))
	received := 0
	for received < count {
		select {
		case <-ctx.Done():
			emit(result("listen", "received", received, "complete", false))
			return timeoutf("%d of %d events", received, count)
		case doc := <-events:
			emit(doc)
			received++
		}
	}
	emit(result("listen", "received", received, "complete", true))
	return nil
}

// ── pdo-configure / pdo-receive ─────────────────────────────────────────────

var reMap = regexp.MustCompile(`^\s*` + reMapEntry.String()[1:len(reMapEntry.String())-1] + `\s*$`)

func parseMapping(s string) (pdo.Mapping, []string, error) {
	var m pdo.Mapping
	var names []string
	bits := 0
	for _, part := range strings.Split(s, ",") {
		g := reMap.FindStringSubmatch(part)
		if g == nil {
			return nil, nil, usagef("%q: want IDX:SUB:BITS, e.g. 0x2002:00:32", part)
		}
		b, _ := strconv.Atoi(g[3])
		if b < 1 || b > 64 {
			return nil, nil, usagef("%q: 1..64 bits", part)
		}
		e := pdo.Entry{Index: hex16(g[1]), SubIndex: hex8(g[2]), Bits: uint8(b)}
		m = append(m, e)
		names = append(names, fmt.Sprintf("0x%04X:%02X:%d", e.Index, e.SubIndex, b))
		bits += b
	}
	if len(m) < 1 || len(m) > 8 || bits > 64 {
		return nil, nil, usagef("a PDO maps 1..8 objects of at most 64 bits")
	}
	return m, names, nil
}

func cobHex(c canopen.COBID) string { return fmt.Sprintf("0x%03X", uint32(c.ID())) }

func cmdPDOConfigure(args []string) error {
	o := newOpts("pdo-configure", true)
	var nodeS, mapS string
	var k, tt, timer int
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.IntVar(&k, "tpdo", 0, "")
	o.fs.StringVar(&mapS, "map", "", "")
	o.fs.IntVar(&tt, "transmission-type", 0, "")
	o.fs.IntVar(&timer, "event-timer-ms", 0, "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, err := nodeArg(o, nodeS)
	if err != nil {
		return err
	}
	if err := o.require("tpdo", "map", "transmission-type"); err != nil {
		return err
	}
	if k < 1 || k > 512 {
		return usagef("--tpdo is 1..512")
	}
	if tt < 0 || tt > 255 {
		return usagef("--transmission-type is 0..255")
	}
	if timer < 0 || timer > 0xFFFF {
		return usagef("--event-timer-ms is 0..65535")
	}
	mapping, names, err := parseMapping(mapS)
	if err != nil {
		return err
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	c, err := sdo.NewClient(bus, id, sdo.WithTimeout(sdoTimeout(o)))
	if err != nil {
		return fmt.Errorf("sdo client: %w", err)
	}
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	s := sdoAdapter{c}
	cur, err := pdo.ReadConfig(ctx, s, true, k)
	if err != nil {
		return classify(fmt.Sprintf("read TPDO %d", k), err)
	}
	cfg := cur
	cfg.COBID = cur.COBID &^ canopen.COBInvalid
	cfg.TransmissionType = pdo.TransmissionType(tt)
	cfg.EventTimer = uint16(timer)
	cfg.Mapping = mapping
	if err := pdo.Configure(ctx, s, true, k, cfg); err != nil {
		return classify(fmt.Sprintf("configure TPDO %d", k), err)
	}
	emit(result("pdo-configure", "node", int(id), "tpdo", k, "cobId", cobHex(cfg.COBID), "map", names,
		"transmissionType", tt, "eventTimerMs", timer))
	return nil
}

// newMaster starts a go-canopen master (SDO clients for every node).
func newMaster(bus *canbus.Bus, o *opts, extra ...master.Option) (*master.Master, error) {
	opts := append([]master.Option{master.WithSDOOptions(sdo.WithTimeout(sdoTimeout(o)))}, extra...)
	m, err := master.New(bus, opts...)
	if err != nil {
		return nil, fmt.Errorf("master: %w", err)
	}
	if err := m.Start(); err != nil {
		_ = m.Close()
		return nil, fmt.Errorf("master start: %w", err)
	}
	return m, nil
}

// sharedDictionary is the dictionary of the shared test device, which
// describes the data types of the objects a remote PDO maps.
func sharedDictionary(id canopen.NodeID) (*od.Dictionary, error) {
	f, err := loadEDS(sharedEDS)
	if err != nil {
		return nil, err
	}
	return f.Dictionary(id)
}

func cmdPDOReceive(args []string) error {
	o := newOpts("pdo-receive", true)
	var nodeS string
	var k int
	count := 1
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.IntVar(&k, "tpdo", 0, "")
	o.fs.IntVar(&count, "count", 1, "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, err := nodeArg(o, nodeS)
	if err != nil {
		return err
	}
	if err := o.require("tpdo"); err != nil {
		return err
	}
	if k < 1 || k > 512 {
		return usagef("--tpdo is 1..512")
	}
	if count < 1 {
		return usagef("--count must be at least 1")
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	desc, err := sharedDictionary(id)
	if err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	m, err := newMaster(bus, o)
	if err != nil {
		return err
	}
	defer m.Close()
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	cfg, err := m.ReadPDO(ctx, id, true, k)
	if err != nil {
		return classify(fmt.Sprintf("read TPDO %d", k), err)
	}
	pdos := make(chan map[string]any, 256)
	l, err := m.ListenTPDO(ctx, id, k, desc, func(_ *canbus.Frame, values []pdo.Value) {
		vals := map[string]any{}
		for _, v := range values {
			vals[hexKey(v.Index, v.SubIndex)] = hexData(v.Raw)
		}
		select {
		case pdos <- event("pdo", "node", int(id), "pdo", k, "cobId", cobHex(cfg.COBID), "values", vals):
		default:
		}
	})
	if err != nil {
		return classify(fmt.Sprintf("listen to TPDO %d", k), err)
	}
	defer l.Close()
	emit(event("ready"))
	received := 0
	for received < count {
		select {
		case <-ctx.Done():
			emit(result("pdo-receive", "received", received, "complete", false))
			return timeoutf("%d of %d PDOs", received, count)
		case doc := <-pdos:
			emit(doc)
			received++
		}
	}
	emit(result("pdo-receive", "received", received, "complete", true))
	return nil
}

// ── sync ────────────────────────────────────────────────────────────────────

func cmdSync(args []string) error {
	o := newOpts("sync", true)
	count, period := 1, 100
	o.fs.IntVar(&count, "count", 1, "")
	o.fs.IntVar(&period, "period-ms", 100, "")
	if err := o.parse(args); err != nil {
		return err
	}
	if count < 1 {
		return usagef("--count must be at least 1")
	}
	if period <= 0 {
		return usagef("--period-ms must be positive")
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	p, err := syncobj.NewProducer(bus)
	if err != nil {
		return fmt.Errorf("SYNC producer: %w", err)
	}
	defer p.Close()
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	sent := 0
	for sent < count {
		if sent > 0 {
			if waitCtx(ctx, time.Duration(period)*time.Millisecond) != nil {
				break
			}
		}
		if err := p.Send(ctx); err != nil {
			break
		}
		sent++
	}
	emit(result("sync", "sent", sent, "periodMs", period))
	if sent < count {
		return timeoutf("%d of %d SYNC messages sent", sent, count)
	}
	return nil
}

// ── cia402 ──────────────────────────────────────────────────────────────────

// cia402Names are the state names of the contract (python canopen's).
var cia402Names = map[cia402.State]string{
	cia402.NotReadyToSwitchOn:  "NOT READY TO SWITCH ON",
	cia402.SwitchOnDisabled:    "SWITCH ON DISABLED",
	cia402.ReadyToSwitchOn:     "READY TO SWITCH ON",
	cia402.SwitchedOn:          "SWITCHED ON",
	cia402.OperationEnabled:    "OPERATION ENABLED",
	cia402.QuickStopActive:     "QUICK STOP ACTIVE",
	cia402.FaultReactionActive: "FAULT REACTION ACTIVE",
	cia402.Fault:               "FAULT",
}

func cia402Name(s cia402.State) string {
	if n, ok := cia402Names[s]; ok {
		return n
	}
	return "UNKNOWN"
}

func cmdCiA402(args []string) error {
	o := newOpts("cia402", true)
	var nodeS, op string
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.StringVar(&op, "op", "", "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, err := nodeArg(o, nodeS)
	if err != nil {
		return err
	}
	if err := o.require("op"); err != nil {
		return err
	}
	if op != "enable" && op != "disable" && op != "fault-reset" {
		return usagef("--op: enable, disable or fault-reset")
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	m, err := newMaster(bus, o)
	if err != nil {
		return err
	}
	defer m.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	c := m.Drive(id, &cia402.ControllerOptions{Timeout: o.timeout()})
	before, err := c.State(ctx)
	if err != nil {
		return classify("statusword", err)
	}
	emit(event("cia402-state", "node", int(id), "state", cia402Name(before)))
	switch op {
	case "enable":
		err = c.Enable(ctx)
	case "disable":
		err = c.Disable(ctx)
	default:
		// As python canopen's BaseNode402.reset_from_fault, the contract's
		// reference: clear the fault, then back to OPERATION ENABLED.
		if err = c.FaultReset(ctx); err == nil {
			err = c.Enable(ctx)
		}
	}
	if err != nil {
		return classify("CiA 402 "+op, err)
	}
	after, err := c.State(ctx)
	if err != nil {
		return classify("statusword", err)
	}
	emit(event("cia402-state", "node", int(id), "state", cia402Name(after)))
	emit(result("cia402", "node", int(id), "op", op, "state", cia402Name(after)))
	return nil
}

// ── rpdo-send ───────────────────────────────────────────────────────────────

func cmdRPDOSend(args []string) error {
	o := newOpts("rpdo-send", true)
	var nodeS string
	var k, delay int
	var sets multi
	var withSync bool
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.IntVar(&k, "rpdo", 0, "")
	o.fs.Var(&sets, "set", "")
	o.fs.BoolVar(&withSync, "sync", false, "")
	o.fs.IntVar(&delay, "delay-ms", 0, "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, err := nodeArg(o, nodeS)
	if err != nil {
		return err
	}
	if err := o.require("rpdo", "set"); err != nil {
		return err
	}
	if k < 1 || k > 512 {
		return usagef("--rpdo is 1..512")
	}
	if delay < 0 || delay > 60000 {
		return usagef("--delay-ms is 0..60000")
	}
	if delay > 0 && !withSync {
		return usagef("--delay-ms needs --sync")
	}
	given := map[string][]byte{}
	for _, s := range sets {
		g := reSet.FindStringSubmatch(s)
		if g == nil || len(g[3])%2 != 0 {
			return usagef("%q: want IDX:SUB=HEX, e.g. 0x2001:00=efbe", s)
		}
		b, _ := parseHex(g[3])
		given[hexKey(hex16(g[1]), hex8(g[2]))] = b
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	desc, err := sharedDictionary(id)
	if err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	m, err := newMaster(bus, o)
	if err != nil {
		return err
	}
	defer m.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	cfg, err := m.ReadPDO(ctx, id, false, k)
	if err != nil {
		return classify(fmt.Sprintf("read RPDO %d", k), err)
	}
	var values []any
	sent := map[string]any{}
	for _, e := range cfg.Mapping {
		if e.IsDummy() {
			continue
		}
		key := hexKey(e.Index, e.SubIndex)
		b, ok := given[key]
		if !ok {
			return usagef("RPDO %d maps %s: no --set for it", k, key)
		}
		values = append(values, b)
		sent[key] = hexData(b)
	}
	if len(sent) != len(given) {
		return usagef("RPDO %d does not map every --set object (it maps %v)", k, cfg.Mapping)
	}
	s, err := m.RPDOSender(ctx, id, k, desc)
	if err != nil {
		return classify(fmt.Sprintf("RPDO %d", k), err)
	}
	var sp *syncobj.Producer
	if withSync {
		if sp, err = syncobj.NewProducer(bus); err != nil {
			return fmt.Errorf("SYNC producer: %w", err)
		}
		defer sp.Close()
		if err := sp.Send(ctx); err != nil {
			return classify("SYNC", err)
		}
		if err := waitCtx(ctx, time.Duration(delay)*time.Millisecond); err != nil {
			return timeoutf("before the RPDO")
		}
	}
	if err := s.Send(ctx, values...); err != nil {
		return classify(fmt.Sprintf("RPDO %d", k), err)
	}
	if withSync {
		if err := waitCtx(ctx, 20*time.Millisecond); err != nil {
			return timeoutf("before the second SYNC")
		}
		if err := sp.Send(ctx); err != nil {
			return classify("SYNC", err)
		}
	}
	emit(result("rpdo-send", "node", int(id), "rpdo", k, "cobId", cobHex(cfg.COBID), "values", sent,
		"sync", withSync, "delayMs", delay))
	return nil
}
