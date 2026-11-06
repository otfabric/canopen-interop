// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/otfabric/go-canopen"
	"github.com/otfabric/go-canopen/cdcf"
	"github.com/otfabric/go-canopen/eds"
	"github.com/otfabric/go-canopen/lss"
	"github.com/otfabric/go-canopen/master"
	"github.com/otfabric/go-canopen/mpdo"
	"github.com/otfabric/go-canopen/nmt"
	"github.com/otfabric/go-canopen/node"
	"github.com/otfabric/go-canopen/od"
)

// ── lss ─────────────────────────────────────────────────────────────────────

// lssStep is one --step of the lss operation.
type lssStep struct {
	name  string
	text  string
	mode  lss.State
	addr  lss.Address
	node  canopen.NodeID
	index uint8
	delay time.Duration
	what  string
	store bool
}

var lssInquire = map[string]lss.Part{
	"vendor-id": lss.PartVendor, "product-code": lss.PartProduct, "revision": lss.PartRevision, "serial": lss.PartSerial,
}

func parseLSSStep(s string) (lssStep, error) {
	name, rest, _ := strings.Cut(s, ":")
	var a []string
	if rest != "" {
		a = strings.Split(rest, ":")
	}
	st := lssStep{name: name, text: s}
	bad := func(want string) (lssStep, error) { return lssStep{}, usagef("LSS step %q: %s", s, want) }
	switch name {
	case "switch-global":
		if len(a) != 1 || (a[0] != "waiting" && a[0] != "configuration") {
			return bad("switch-global:waiting|configuration")
		}
		st.what = a[0]
		st.mode = lss.Waiting
		if a[0] == "configuration" {
			st.mode = lss.Configuration
		}
	case "switch-selective":
		if len(a) != 4 {
			return bad("switch-selective:VENDOR:PRODUCT:REVISION:SERIAL")
		}
		var v [4]uint32
		for i := range a {
			x, err := parseU32(a[i])
			if err != nil {
				return bad("numbers decimal or 0x")
			}
			v[i] = x
		}
		st.addr = lss.Address{Vendor: v[0], Product: v[1], Revision: v[2], Serial: v[3]}
	case "configure-node-id":
		if len(a) != 1 {
			return bad("configure-node-id:N")
		}
		n, err := strconv.ParseUint(a[0], 0, 8)
		if err != nil || !(n >= 1 && n <= 127 || n == 0xFF) {
			return bad("node-ID 1..127 or 255")
		}
		st.node = canopen.NodeID(n)
	case "configure-bit-timing":
		n, err := strconv.ParseUint(strings.Join(a, ":"), 0, 8)
		if len(a) != 1 || err != nil || n > 9 || n == 5 { // CiA 301 table 0: index 5 is reserved
			return bad("configure-bit-timing:INDEX (CiA 301 table: 0-4, 6-9)")
		}
		st.index = uint8(n)
	case "activate-bit-timing":
		n, err := strconv.ParseUint(strings.Join(a, ":"), 0, 16)
		if len(a) != 1 || err != nil {
			return bad("activate-bit-timing:DELAY_MS (0..65535)")
		}
		st.delay = time.Duration(n) * time.Millisecond
	case "inquire":
		if len(a) != 1 {
			return bad("inquire:vendor-id|product-code|revision|serial|node-id")
		}
		if _, ok := lssInquire[a[0]]; !ok && a[0] != "node-id" {
			return bad("inquire:vendor-id|product-code|revision|serial|node-id")
		}
		st.what = a[0]
	case "assign-all":
		if len(a) < 1 || len(a) > 2 || (len(a) == 2 && a[1] != "store") {
			return bad("assign-all:FIRST_NODE_ID[:store]")
		}
		n, err := strconv.ParseUint(a[0], 0, 8)
		if err != nil || n < 1 || n > 127 {
			return bad("node-ID 1..127")
		}
		st.node, st.store = canopen.NodeID(n), len(a) == 2
	case "store", "fastscan":
		if len(a) != 0 {
			return bad("no argument")
		}
	default:
		return lssStep{}, usagef("unknown LSS step %q", s)
	}
	return st, nil
}

func addressDoc(a lss.Address) map[string]any {
	return map[string]any{"vendorId": hexU32(a.Vendor), "productCode": hexU32(a.Product),
		"revision": hexU32(a.Revision), "serial": hexU32(a.Serial)}
}

// lssFail is the error of a step: exit 3 when no slave answered, exit 5
// with lssError for a slave's refusal.
func lssFail(st lssStep, err error) error {
	var ce *lss.ConfigError
	if errors.As(err, &ce) {
		return with(protocolf("LSS %s: %v", st.name, err), "step", st.name, "lssError", int(ce.Code))
	}
	if errors.Is(err, canopen.ErrTimeout) || errors.Is(err, context.DeadlineExceeded) {
		return with(timeoutf("LSS %s: %v", st.name, err), "step", st.name)
	}
	return with(protocolf("LSS %s: %v", st.name, err), "step", st.name)
}

func cmdLSS(args []string) error {
	o := newOpts("lss", true)
	var steps multi
	o.fs.Var(&steps, "step", "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("step"); err != nil {
		return err
	}
	var list []lssStep
	for _, s := range steps {
		st, err := parseLSSStep(s)
		if err != nil {
			return err
		}
		list = append(list, st)
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	m := lss.NewMaster(bus)
	if err := m.Start(); err != nil {
		return fmt.Errorf("LSS master: %w", err)
	}
	defer m.Close()
	ctx, cancel := context.WithTimeout(context.Background(), o.timeout())
	defer cancel()
	for _, st := range list {
		var ev map[string]any
		switch st.name {
		case "switch-global":
			err = m.SwitchStateGlobal(ctx, st.mode)
			ev = event("lss", "step", st.name, "mode", st.what)
		case "switch-selective":
			err = m.SwitchStateSelective(ctx, st.addr)
			ev = event("lss", "step", st.name, "address", addressDoc(st.addr))
		case "configure-node-id":
			err = m.SetNodeID(ctx, st.node)
			ev = event("lss", "step", st.name, "node", int(st.node))
		case "configure-bit-timing":
			err = m.SetBitTimingTable(ctx, 0, st.index)
			ev = event("lss", "step", st.name, "tableIndex", int(st.index), "kbps", lss.BitTiming(st.index).Rate())
		case "activate-bit-timing":
			err = m.ActivateBitTiming(ctx, st.delay)
			ev = event("lss", "step", st.name, "delayMs", st.delay.Milliseconds())
		case "store":
			err = m.Store(ctx)
			ev = event("lss", "step", st.name)
		case "inquire":
			if st.what == "node-id" {
				var n canopen.NodeID
				n, err = m.InquireNodeID(ctx)
				ev = event("lss", "step", st.name, "what", st.what, "node", int(n))
			} else {
				var v uint32
				v, err = m.Inquire(ctx, lssInquire[st.what])
				ev = event("lss", "step", st.name, "what", st.what, "value", hexU32(v))
			}
		case "fastscan":
			a, ok, ferr := m.FastscanSelect(ctx, lss.FastscanOptions{})
			err = ferr
			if err == nil && !ok {
				return with(timeoutf("LSS fastscan: no unconfigured slave answered"), "step", st.name)
			}
			ev = event("lss", "step", st.name, "address", addressDoc(a))
		case "assign-all":
			var aopts []lss.AssignOption
			if st.store {
				aopts = append(aopts, lss.AssignStore())
			}
			as, aerr := m.AssignAll(ctx, st.node, aopts...)
			err = aerr
			list := []map[string]any{}
			for _, a := range as {
				list = append(list, map[string]any{"node": int(a.NodeID), "address": addressDoc(a.Address)})
			}
			if err == nil && len(as) == 0 {
				return with(timeoutf("LSS assign-all: no unconfigured slave answered"), "step", st.name)
			}
			ev = event("lss", "step", st.name, "assigned", list)
		}
		if err != nil {
			return lssFail(st, err)
		}
		emit(ev)
	}
	emit(result("lss", "steps", len(list)))
	return nil
}

// ── mpdo-send / mpdo-receive ────────────────────────────────────────────────

const mpdoEDS = "/fixtures/eds/mpdo-device.eds"

func parseCOBID(s string) (canopen.COBID, error) {
	v, err := parseU32(s)
	if err != nil || v < 1 || v > 0x7FF {
		return 0, usagef("COB-ID %q: an 11-bit CAN-ID 0x001..0x7FF", s)
	}
	return canopen.COBID(v), nil
}

// mpdoNode runs a self-starting node of mpdo-device.eds (Operational on
// its own) whose PDO 1 is the MPDO, configured by the params.
func mpdoNode(iface string, id canopen.NodeID, params map[[2]uint16]string) (*node.Node, *od.Dictionary, func(), error) {
	f, err := loadEDS(mpdoEDS)
	if err != nil {
		return nil, nil, nil, err
	}
	for k, v := range params {
		if !setDefault(f, k[0], uint8(k[1]), v) {
			return nil, nil, nil, fmt.Errorf("%s has no 0x%04X:%02X", mpdoEDS, k[0], k[1])
		}
	}
	d, err := f.Dictionary(id)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("%s: %w", mpdoEDS, err)
	}
	bus, err := openBus(iface)
	if err != nil {
		return nil, nil, nil, err
	}
	n, err := node.New(bus, d, node.WithAutoStart())
	if err != nil {
		_ = bus.Close()
		return nil, nil, nil, fmt.Errorf("node: %w", err)
	}
	if err := n.Start(); err != nil {
		_ = n.Close()
		_ = bus.Close()
		return nil, nil, nil, fmt.Errorf("node start: %w", err)
	}
	return n, d, func() { _ = n.Close(); _ = bus.Close() }, nil
}

// operational waits until the node is Operational and its MPDO services
// run.
func operational(ctx context.Context, n *node.Node) error {
	for n.State() != canopen.StateOperational {
		if err := waitCtx(ctx, 10*time.Millisecond); err != nil {
			return timeoutf("the node did not become operational")
		}
	}
	return waitCtx(ctx, 200*time.Millisecond)
}

func mpdoFlags(o *opts, nodeS, mode, cob *string, count *int) {
	o.fs.StringVar(nodeS, "node-id", "", "")
	o.fs.StringVar(mode, "mode", "", "")
	o.fs.StringVar(cob, "cob-id", "", "")
	o.fs.IntVar(count, "count", 1, "")
}

func mpdoCommon(o *opts, nodeS, mode, cobS string, count int) (canopen.NodeID, canopen.COBID, error) {
	if err := o.require("node-id", "mode", "cob-id"); err != nil {
		return 0, 0, err
	}
	id, err := parseNode(nodeS, false, false)
	if err != nil {
		return 0, 0, err
	}
	if mode != "dam" && mode != "sam" {
		return 0, 0, usagef("--mode: dam or sam")
	}
	cob, err := parseCOBID(cobS)
	if err != nil {
		return 0, 0, err
	}
	if count < 1 {
		return 0, 0, usagef("--count must be at least 1")
	}
	return id, cob, nil
}

func mappingMode(mode string) string {
	if mode == "dam" {
		return fmt.Sprintf("0x%02X", mpdo.MappingDAM)
	}
	return fmt.Sprintf("0x%02X", mpdo.MappingSAM)
}

func cmdMPDOSend(args []string) error {
	o := newOpts("mpdo-send", true)
	var nodeS, mode, cobS, indexS, dataS, targetS string
	subS := "0"
	count, period := 1, 100
	mpdoFlags(o, &nodeS, &mode, &cobS, &count)
	o.fs.StringVar(&indexS, "index", "", "")
	o.fs.StringVar(&subS, "subindex", "0", "")
	o.fs.StringVar(&dataS, "data", "", "")
	o.fs.StringVar(&targetS, "target", "0", "")
	o.fs.IntVar(&period, "period-ms", 100, "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, cob, err := mpdoCommon(o, nodeS, mode, cobS, count)
	if err != nil {
		return err
	}
	if err := o.require("index", "data"); err != nil {
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
	data, err := parseHex(dataS)
	if err != nil {
		return err
	}
	if len(data) < 1 || len(data) > 4 {
		return usagef("an MPDO carries 1..4 data bytes")
	}
	target, err := parseNode(targetS, true, false)
	if err != nil {
		return err
	}
	if mode == "sam" && target != 0 {
		return usagef("--target is for DAM-MPDOs")
	}
	if period <= 0 {
		return usagef("--period-ms must be positive")
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	params := map[[2]uint16]string{
		{0x1800, 1}: hexU32(uint32(cob)), {0x1800, 2}: "0xFF", {0x1A00, 0}: mappingMode(mode),
	}
	if mode == "sam" {
		params[[2]uint16{0x1FA0, 0}] = "1"
		params[[2]uint16{0x1FA0, 1}] = hexU32(mpdo.ScannerEntry{Index: index, SubIndex: sub, BlockSize: 1}.Uint32())
	}
	n, d, closeNode, err := mpdoNode(o.iface, id, params)
	if err != nil {
		return err
	}
	defer closeNode()
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	if err := operational(ctx, n); err != nil {
		return err
	}
	p := n.MPDOProducer(1)
	if p == nil {
		return fmt.Errorf("TPDO 1 is not an MPDO producer")
	}
	if mode == "sam" {
		if err := d.Set(index, sub, data); err != nil {
			return usagef("0x%04X:%02X = %s: %v", index, sub, dataS, err)
		}
	}
	sent := 0
	for sent < count {
		if sent > 0 && waitCtx(ctx, time.Duration(period)*time.Millisecond) != nil {
			break
		}
		var err error
		for {
			if mode == "dam" {
				err = p.SendDAM(ctx, target, index, sub, data)
			} else {
				err = p.Send(ctx, index, sub)
			}
			if !errors.Is(err, mpdo.ErrDisabled) || waitCtx(ctx, 10*time.Millisecond) != nil {
				break
			}
		}
		if err != nil {
			emit(result("mpdo-send", "node", int(id), "mode", mode, "cobId", cobHex(cob), "sent", sent))
			return classify("MPDO", err)
		}
		sent++
	}
	// Let the frames leave the socket before the node closes.
	_ = waitCtx(ctx, 20*time.Millisecond)
	emit(result("mpdo-send", "node", int(id), "mode", mode, "cobId", cobHex(cob), "sent", sent))
	if sent < count {
		return timeoutf("%d of %d MPDOs sent", sent, count)
	}
	return nil
}

func cmdMPDOReceive(args []string) error {
	o := newOpts("mpdo-receive", true)
	var nodeS, mode, cobS string
	var dispatch multi
	count := 1
	mpdoFlags(o, &nodeS, &mode, &cobS, &count)
	o.fs.Var(&dispatch, "dispatch", "")
	if err := o.parse(args); err != nil {
		return err
	}
	id, cob, err := mpdoCommon(o, nodeS, mode, cobS, count)
	if err != nil {
		return err
	}
	if len(dispatch) > 0 && mode != "sam" {
		return usagef("--dispatch is for SAM-MPDOs")
	}
	if mode == "sam" && len(dispatch) == 0 {
		return usagef("a SAM-MPDO consumer needs --dispatch entries")
	}
	if len(dispatch) > 4 {
		return usagef("at most 4 --dispatch entries (0x1FD0 of %s)", mpdoEDS)
	}
	params := map[[2]uint16]string{
		{0x1400, 1}: hexU32(uint32(cob)), {0x1400, 2}: "0xFF", {0x1600, 0}: mappingMode(mode),
	}
	for i, s := range dispatch {
		g := reDispatch.FindStringSubmatch(s)
		p, _ := strconv.Atoi(firstOr(g, 1))
		if g == nil || p < 1 || p > 127 {
			return usagef("%q: want PRODUCER:RIDX:RSUB:LIDX:LSUB, e.g. 7:0x2001:00:0x2001:00", s)
		}
		e := mpdo.DispatcherEntry{Producer: canopen.NodeID(p), ProducerIndex: hex16(g[2]), ProducerSubIndex: hex8(g[3]),
			Index: hex16(g[4]), SubIndex: hex8(g[5]), BlockSize: 1}
		params[[2]uint16{0x1FD0, uint16(i + 1)}] = fmt.Sprintf("0x%016X", e.Uint64())
	}
	if len(dispatch) > 0 {
		params[[2]uint16{0x1FD0, 0}] = strconv.Itoa(len(dispatch))
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	n, d, closeNode, err := mpdoNode(o.iface, id, params)
	if err != nil {
		return err
	}
	defer closeNode()
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	if err := operational(ctx, n); err != nil {
		return err
	}
	if n.MPDOConsumer(1) == nil {
		return fmt.Errorf("RPDO 1 is not an MPDO consumer")
	}
	writes := make(chan map[string]any, 256)
	stop := d.Watch(func(v *od.Variable, data []byte) {
		if v.Index < 0x2000 {
			return
		}
		select {
		case writes <- event("mpdo", "node", int(id), "index", hexIndex(v.Index), "subindex", int(v.SubIndex), "data", hexData(data)):
		default:
		}
	})
	defer stop()
	emit(event("ready"))
	received := 0
	for received < count {
		select {
		case <-ctx.Done():
			emit(result("mpdo-receive", "received", received, "complete", false))
			return timeoutf("%d of %d MPDO writes", received, count)
		case doc := <-writes:
			emit(doc)
			received++
		}
	}
	emit(result("mpdo-receive", "received", received, "complete", true))
	return nil
}

func firstOr(g []string, i int) string {
	if i < len(g) {
		return g[i]
	}
	return ""
}

// ── boot ────────────────────────────────────────────────────────────────────

// managerNode is the node-ID of the CiA 302 manager (as go-canopen's
// interop suite runs it).
const managerNode canopen.NodeID = 1

type bootSlave struct {
	node      canopen.NodeID
	path      string
	mandatory bool
	entries   []cdcf.Entry
	heartbeat bool // the concise DCF sets a producer heartbeat time
}

func parseSlave(s string) (bootSlave, error) {
	parts := strings.Split(s, ":")
	sl := bootSlave{}
	if len(parts) > 2 && parts[len(parts)-1] == "mandatory" {
		sl.mandatory = true
		parts = parts[:len(parts)-1]
	}
	if len(parts) != 2 || parts[1] == "" {
		return sl, usagef("%q: want NODE:DCF_PATH[:mandatory]", s)
	}
	n, err := strconv.ParseUint(parts[0], 0, 8)
	if err != nil || n < 1 || n > 127 {
		return sl, usagef("%q: node-ID 1..127", s)
	}
	sl.node, sl.path = canopen.NodeID(n), parts[1]
	if st, err := os.Stat(sl.path); err != nil || st.IsDir() {
		return sl, usagef("no DCF file %s", sl.path)
	}
	return sl, nil
}

// valueEntry encodes VALUE with the data type the dictionary gives the
// object (canopen.ParseValue).
func valueEntry(d *od.Dictionary, nv nodeValue, id canopen.NodeID) (cdcf.Entry, error) {
	v, err := d.Variable(nv.index, nv.sub)
	if err != nil {
		return cdcf.Entry{}, usagef("0x%04X:%02X: %v", nv.index, nv.sub, err)
	}
	b, err := canopen.ParseValue(v.DataType, nv.value, id)
	if err != nil {
		return cdcf.Entry{}, usagef("0x%04X:%02X=%s: %v", nv.index, nv.sub, nv.value, err)
	}
	return cdcf.Entry{Index: nv.index, SubIndex: nv.sub, Data: b}, nil
}

func cmdBoot(args []string) error {
	o := newOpts("boot", true)
	var slaves, sdos multi
	var noStart bool
	o.fs.Var(&slaves, "slave", "")
	o.fs.Var(&sdos, "sdo", "")
	o.fs.BoolVar(&noStart, "no-start", false, "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("slave"); err != nil {
		return err
	}
	var list []*bootSlave
	byNode := map[canopen.NodeID]*bootSlave{}
	for _, s := range slaves {
		sl, err := parseSlave(s)
		if err != nil {
			return err
		}
		if byNode[sl.node] != nil {
			return usagef("--slave: node-ID %d twice", sl.node)
		}
		byNode[sl.node] = &sl
		list = append(list, &sl)
	}
	files := map[canopen.NodeID]*od.Dictionary{}
	for _, sl := range list {
		f, err := loadEDS(sl.path)
		if err != nil {
			return err
		}
		d, err := f.Dictionary(sl.node)
		if err != nil {
			return usagef("DCF %s: %v", sl.path, err)
		}
		files[sl.node] = d
	}
	for _, s := range sdos {
		nv, err := parseNodeValue(s)
		if err != nil {
			return err
		}
		sl := byNode[nv.node]
		if sl == nil {
			return usagef("--sdo for node %d, which is not a --slave", nv.node)
		}
		e, err := valueEntry(files[sl.node], nv, sl.node)
		if err != nil {
			return err
		}
		sl.entries = append(sl.entries, e)
		if e.Index == od.IndexProducerHeartbeat && canopen.DecodeUint(e.Data) != 0 {
			sl.heartbeat = true
		}
	}
	if err := o.checkBus(); err != nil {
		return err
	}

	// The manager's dictionary: the network list and the expected
	// identities (0x1F81, 0x1F84..0x1F87), a concise DCF per slave (0x1F22),
	// heartbeat supervision of the slaves that produce one (0x1016).
	md, err := od.Standard(od.Config{NodeID: managerNode, HeartbeatConsumers: len(list)})
	if err != nil {
		return fmt.Errorf("manager dictionary: %w", err)
	}
	if err := master.AddManagerObjects(md); err != nil {
		return fmt.Errorf("manager objects: %w", err)
	}
	consumer := uint8(0)
	for _, sl := range list {
		d := files[sl.node]
		flags := master.AssignSlave | master.AssignBoot
		if sl.mandatory {
			flags |= master.AssignMandatory
		}
		id := uint8(sl.node)
		set := func(index uint16, v uint64) error { return md.SetUint(index, id, v) }
		get := func(index uint16, sub uint8) uint64 { v, _ := d.GetUint(index, sub); return v }
		for _, e := range []struct {
			index uint16
			v     uint64
		}{
			{master.IndexSlaveAssignment, uint64(master.NewAssignment(flags, 0, 0))},
			{master.IndexExpectedDeviceType, get(0x1000, 0)},
			{master.IndexExpectedVendorID, get(0x1018, 1)},
			{master.IndexExpectedProductCode, get(0x1018, 2)},
			{master.IndexExpectedRevision, get(0x1018, 3)},
		} {
			if err := set(e.index, e.v); err != nil {
				return fmt.Errorf("manager 0x%04X:%02X: %w", e.index, id, err)
			}
		}
		if len(sl.entries) > 0 {
			b, err := cdcf.Encode(sl.entries)
			if err != nil {
				return fmt.Errorf("concise DCF of node %d: %w", sl.node, err)
			}
			if err := md.Set(cdcf.IndexConciseDCF, id, b); err != nil {
				return fmt.Errorf("manager 0x1F22:%02X: %w", id, err)
			}
		}
		if sl.heartbeat {
			consumer++
			if err := md.SetUint(od.IndexConsumerHeartbeat, consumer, uint64(nmt.ConsumerEntry(sl.node, time.Second))); err != nil {
				return fmt.Errorf("manager 0x1016:%02X: %w", consumer, err)
			}
		}
	}
	startup := master.StartupMaster
	if noStart {
		startup |= master.StartupNoStartSlaves
	}
	if err := md.SetUint(master.IndexNMTStartup, 0, uint64(startup)); err != nil {
		return fmt.Errorf("manager 0x1F80: %w", err)
	}
	// The boot time (0x1F89): a mandatory slave that does not answer ends
	// with error status B within --timeout-ms (0 would retry it for ever,
	// CiA 302-2).
	bootTime := o.timeoutMs * 3 / 4
	if bootTime > o.timeoutMs-2000 {
		bootTime = max(o.timeoutMs-2000, o.timeoutMs/2)
	}
	if err := md.SetUint(master.IndexBootTime, 0, uint64(bootTime)); err != nil {
		return fmt.Errorf("manager 0x1F89: %w", err)
	}

	events := make(chan master.Event, 1024)
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	m, err := newMaster(bus, o, master.WithEventHandler(func(e master.Event) {
		select {
		case events <- e:
		default:
		}
	}), master.WithDictionary(md),
		master.WithBootTiming(master.BootTiming{RetryDelay: 200 * time.Millisecond, ResetTimeout: 3 * time.Second}))
	if err != nil {
		return err
	}
	defer m.Close()

	final := map[canopen.NodeID]master.Event{}
	networkDone := false
	var mu sync.Mutex
	report := func(e master.Event) {
		doc := event("boot", "node", int(e.Node), "ok", e.Kind == master.EventBootDone, "errorStatus", nil, "message", "")
		if e.Kind == master.EventBootFailed {
			if e.Status != master.BootOK {
				doc["errorStatus"] = string(rune(e.Status))
			}
			if e.Err != nil {
				doc["message"] = e.Err.Error()
			}
		}
		emit(doc)
	}
wait:
	for {
		mu.Lock()
		all := len(final) == len(list)
		mu.Unlock()
		if all && networkDone {
			break
		}
		select {
		case <-ctx.Done():
			break wait
		case e := <-events:
			switch e.Kind {
			case master.EventBootDone, master.EventBootFailed:
				if byNode[e.Node] == nil {
					continue
				}
				report(e)
				mu.Lock()
				final[e.Node] = e
				mu.Unlock()
			case master.EventNetworkBootDone:
				networkDone = true
			case master.EventNetworkBootFailed:
				// The network boot-up halted: slaves without a result stay
				// missing.
				networkDone = true
				if e.Node != 0 && byNode[e.Node] != nil {
					if _, ok := final[e.Node]; !ok {
						ev := master.Event{Kind: master.EventBootFailed, Node: e.Node, Err: e.Err}
						var be *master.BootError
						if errors.As(e.Err, &be) {
							ev.Status = be.Status
						}
						report(ev)
						final[e.Node] = ev
					}
				}
				break wait
			}
		}
	}
	booted, failed, missing := []int{}, []int{}, []int{}
	for _, sl := range list {
		e, ok := final[sl.node]
		switch {
		case !ok:
			missing = append(missing, int(sl.node))
		case e.Kind == master.EventBootDone:
			booted = append(booted, int(sl.node))
		default:
			failed = append(failed, int(sl.node))
		}
	}
	emit(result("boot", "booted", booted, "failed", failed, "missing", missing))
	switch {
	case len(failed) > 0:
		return protocolf("boot failed for nodes %v", failed)
	case len(missing) > 0:
		return timeoutf("no boot result for nodes %v", missing)
	}
	return nil
}

// ── eds-check ───────────────────────────────────────────────────────────────

func cmdEDSCheck(args []string) error {
	o := newOpts("eds-check", false)
	var path string
	o.fs.StringVar(&path, "eds", "", "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("eds"); err != nil {
		return err
	}
	if st, err := os.Stat(path); err != nil || st.IsDir() {
		return usagef("no EDS file %s", path)
	}
	errs := []string{}
	objects := []map[string]any{}
	f, err := eds.ParseFile(path)
	if err != nil {
		emit(result("eds-check", "valid", false, "errors", []string{err.Error()}, "objects", objects))
		return nil
	}
	diags := f.Validate()
	for _, d := range diags {
		if d.Severity == eds.Warning {
			errs = append(errs, "warning: "+d.String())
		} else {
			errs = append(errs, d.String())
		}
	}
	valid := !eds.HasErrors(diags)
	id := canopen.NodeID(1)
	if f.DeviceComissioning != nil && f.DeviceComissioning.NodeID >= 1 && f.DeviceComissioning.NodeID <= 127 {
		id = canopen.NodeID(f.DeviceComissioning.NodeID)
	}
	d, bdiags, err := f.BuildDictionary(id, eds.BuildOptions{Lenient: !valid})
	if err != nil {
		valid = false
		errs = append(errs, err.Error())
	}
	for _, x := range bdiags {
		if x.Severity == eds.Error {
			valid = false
		}
		if !valid {
			errs = append(errs, "build: "+x.String())
		}
	}
	if d != nil {
		objs := d.Objects()
		sort.Slice(objs, func(i, j int) bool { return objs[i].Index < objs[j].Index })
		for _, ob := range objs {
			subs := append([]*od.Variable(nil), ob.Subs...)
			sort.Slice(subs, func(i, j int) bool { return subs[i].SubIndex < subs[j].SubIndex })
			for _, v := range subs {
				var def any
				if lit, ok := fileDefault(f, v.Index, v.SubIndex); ok && strings.TrimSpace(lit) != "" {
					def = lit
				}
				objects = append(objects, map[string]any{
					"index": hexIndex(v.Index), "subindex": int(v.SubIndex), "name": v.Name,
					"dataType": fmt.Sprintf("0x%04X", uint16(v.DataType)), "access": v.Access.String(),
					"pdoMapping": v.PDOMappable, "default": def,
				})
			}
		}
	}
	emit(result("eds-check", "valid", valid, "errors", errs, "objects", objects))
	return nil
}

// fileDefault is the DefaultValue literal of a sub-object in the file, if
// it has a section of its own.
func fileDefault(f *eds.File, index uint16, sub uint8) (string, bool) {
	o := f.Object(index)
	if o == nil {
		return "", false
	}
	if o.CompactSubObj > 0 {
		// Compact storage (CiA 306 4.5.2): the object's DefaultValue is
		// that of the sub-objects 1..n, unless [XXXXValue] gives one;
		// sub-index 0 is NrOfObjects.
		if sub >= 1 && sub <= o.CompactSubObj {
			if o.Values != nil {
				if v, ok := o.Values.Entries[sub]; ok {
					return v, true
				}
			}
			return o.DefaultValue, true
		}
		return "", false
	}
	if len(o.Subs) == 0 && sub == 0 {
		return o.DefaultValue, true
	}
	if s := o.Sub(sub); s != nil {
		return s.DefaultValue, true
	}
	return "", false
}

// ── cdcf ────────────────────────────────────────────────────────────────────

func cmdCDCF(args []string) error {
	o := newOpts("cdcf", false)
	var path, nodeS, output, rangeS string
	gen := "dcfgen"
	var sdos multi
	o.fs.StringVar(&path, "dcf", "", "")
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.StringVar(&gen, "generator", "dcfgen", "")
	o.fs.Var(&sdos, "sdo", "")
	o.fs.StringVar(&rangeS, "range", "0x1000-0x9FFF", "")
	o.fs.StringVar(&output, "output", "", "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("dcf", "node-id"); err != nil {
		return err
	}
	id, err := parseNode(nodeS, false, false)
	if err != nil {
		return err
	}
	if gen != "dcfgen" && gen != "objects" {
		return usagef("--generator: dcfgen or objects")
	}
	g := reRange.FindStringSubmatch(rangeS)
	if g == nil || hex16(g[1]) > hex16(g[2]) {
		return usagef("%q: want MIN-MAX in hex, e.g. 0x1000-0x1FFF", rangeS)
	}
	lo, hi := hex16(g[1]), hex16(g[2])
	if gen == "objects" && len(sdos) > 0 {
		return usagef("--sdo is for --generator dcfgen")
	}
	if st, err := os.Stat(path); err != nil || st.IsDir() {
		return usagef("no DCF file %s", path)
	}
	f, err := loadEDS(path)
	if err != nil {
		return err
	}
	d, err := f.Dictionary(id)
	if err != nil {
		return protocolf("DCF %s: %v", path, err)
	}
	var entries []cdcf.Entry
	if gen == "dcfgen" {
		for _, s := range sdos {
			nv, err := parseNodeValue(s)
			if err != nil {
				return err
			}
			if nv.node != 0 {
				return usagef("cdcf --sdo takes IDX:SUB=VALUE (the node is --node-id)")
			}
			e, err := valueEntry(d, nv, id)
			if err != nil {
				return err
			}
			entries = append(entries, e)
		}
	} else {
		objs := d.Objects()
		sort.Slice(objs, func(i, j int) bool { return objs[i].Index < objs[j].Index })
		for _, ob := range objs {
			if ob.Index < lo || ob.Index > hi {
				continue
			}
			subs := append([]*od.Variable(nil), ob.Subs...)
			sort.Slice(subs, func(i, j int) bool { return subs[i].SubIndex < subs[j].SubIndex })
			for _, v := range subs {
				b, err := d.Get(v.Index, v.SubIndex)
				if err != nil {
					return protocolf("0x%04X:%02X: %v", v.Index, v.SubIndex, err)
				}
				entries = append(entries, cdcf.Entry{Index: v.Index, SubIndex: v.SubIndex, Data: b})
			}
		}
	}
	b, err := cdcf.Encode(entries)
	if err != nil {
		return protocolf("concise DCF: %v", err)
	}
	if output != "" {
		if err := os.WriteFile(output, b, 0o644); err != nil {
			return fmt.Errorf("write %s: %w", output, err)
		}
	}
	emit(result("cdcf", "generator", gen, "node", int(id), "size", len(b), "data", hexData(b)))
	return nil
}
