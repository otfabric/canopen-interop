// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/otfabric/go-canopen"
	"github.com/otfabric/go-canopen/eds"
	"github.com/otfabric/go-canopen/emcy"
	"github.com/otfabric/go-canopen/lss"
	"github.com/otfabric/go-canopen/node"
	"github.com/otfabric/go-canopen/od"
	"github.com/otfabric/go-canopen/store"
)

// loadEDS parses an EDS/DCF; a file that does not exist is a usage error.
func loadEDS(path string) (*eds.File, error) {
	if st, err := os.Stat(path); err != nil || st.IsDir() {
		return nil, usagef("no EDS file %s", path)
	}
	f, err := eds.ParseFile(path)
	if err != nil {
		return nil, usagef("EDS %s: %v", path, err)
	}
	return f, nil
}

// setDefault changes the DefaultValue of a sub-object of the file before
// the dictionary is built (the power-on value).
func setDefault(f *eds.File, index uint16, sub uint8, value string) bool {
	o := f.Object(index)
	if o == nil {
		return false
	}
	if len(o.Subs) == 0 && sub == 0 {
		o.DefaultValue = value
		return true
	}
	if s := o.Sub(sub); s != nil {
		s.DefaultValue = value
		return true
	}
	return false
}

// emcyArg is --emcy CODE:REG:HEXDATA.
type emcyArg struct {
	code uint16
	reg  uint8
	msef [5]byte
}

func parseEMCY(s string) (emcyArg, error) {
	m := reEMCY.FindStringSubmatch(s)
	if m == nil || len(m[3])%2 != 0 || len(m[3]) > 10 {
		return emcyArg{}, usagef("%q: want CODE:REG:HEXDATA (up to 5 data bytes)", s)
	}
	e := emcyArg{code: hex16(m[1]), reg: hex8(m[2])}
	b, _ := parseHex(m[3])
	copy(e.msef[:], b)
	return e, nil
}

func cmdDevice(args []string) error {
	o := newOpts("device", true)
	var nodeS, path, serialS, emcyS string
	var dumps multi
	var hbMs, tickMs, emcyAfter int
	o.fs.StringVar(&nodeS, "node-id", "", "")
	o.fs.StringVar(&path, "eds", "", "")
	o.fs.StringVar(&serialS, "serial", "", "")
	o.fs.IntVar(&hbMs, "heartbeat-ms", 0, "")
	o.fs.Var(&dumps, "dump", "")
	o.fs.IntVar(&tickMs, "tick-ms", 0, "")
	o.fs.StringVar(&emcyS, "emcy", "", "")
	o.fs.IntVar(&emcyAfter, "emcy-after-ms", 0, "")
	if err := o.parse(args); err != nil {
		return err
	}
	if err := o.require("node-id", "eds"); err != nil {
		return err
	}
	id, err := parseNode(nodeS, false, true)
	if err != nil {
		return err
	}
	var keys []objKey
	for _, d := range dumps {
		k, err := parseKey(d)
		if err != nil {
			return err
		}
		keys = append(keys, k)
	}
	if o.set["heartbeat-ms"] && (hbMs < 0 || hbMs > 0xFFFF) {
		return usagef("--heartbeat-ms is 0..65535")
	}
	if o.set["tick-ms"] && tickMs <= 0 {
		return usagef("--tick-ms must be positive")
	}
	if emcyAfter < 0 {
		return usagef("--emcy-after-ms must not be negative")
	}
	var em *emcyArg
	if emcyS != "" {
		e, err := parseEMCY(emcyS)
		if err != nil {
			return err
		}
		if e.code == 0 {
			return usagef("--emcy: error code 0x0000 is the error reset")
		}
		em = &e
	}
	lssSlave := id == lss.Unconfigured || o.set["serial"]
	var serial uint32
	if o.set["serial"] {
		if serial, err = parseU32(serialS); err != nil {
			return err
		}
	}
	f, err := loadEDS(path)
	if err != nil {
		return err
	}
	if err := o.checkBus(); err != nil {
		return err
	}
	if o.set["heartbeat-ms"] {
		setDefault(f, od.IndexProducerHeartbeat, 0, strconv.Itoa(hbMs))
	}
	if o.set["serial"] && !setDefault(f, 0x1018, 4, fmt.Sprintf("0x%08X", serial)) {
		return usagef("--serial: the EDS has no 0x1018:04")
	}
	d, err := f.Dictionary(id)
	if err != nil {
		return usagef("EDS %s: %v", path, err)
	}

	var nopts []node.Option
	if lssSlave {
		if err := store.AddObjects(d); err != nil {
			return fmt.Errorf("store objects: %w", err)
		}
		nopts = append(nopts, node.WithStore(store.NewMemory()), node.WithLSS(
			lss.WithBitTimingHandler(func(uint8, lss.BitTiming) error { return nil }),
			lss.WithActivateHandler(func(s lss.Settings, delay time.Duration) {
				emit(event("bit-rate", "kbps", s.BitTiming.Rate(), "delayMs", delay.Milliseconds()))
			}),
		))
	}
	bus, err := openBus(o.iface)
	if err != nil {
		return err
	}
	defer bus.Close()
	n, err := node.New(bus, d, nopts...)
	if err != nil {
		return fmt.Errorf("node: %w", err)
	}
	ctx, cancel := signalContext(o.timeout())
	defer cancel()
	if err := n.Start(); err != nil {
		_ = n.Close()
		return fmt.Errorf("node start: %w", err)
	}
	if id == lss.Unconfigured {
		emit(event("ready"))
	} else {
		emit(event("ready", "node", int(id)))
	}

	var wg sync.WaitGroup
	// The active node-ID: an LSS-configured node-ID becomes active at an
	// NMT reset communication.
	wg.Add(1)
	go func() {
		defer wg.Done()
		last := n.NodeID()
		tk := time.NewTicker(10 * time.Millisecond)
		defer tk.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-tk.C:
				if cur := n.NodeID(); cur != last {
					last = cur
					emit(event("node-id", "node", int(cur)))
				}
			}
		}
	}()
	if tickMs > 0 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			tk := time.NewTicker(time.Duration(tickMs) * time.Millisecond)
			defer tk.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-tk.C:
					v, _ := d.GetUint(0x2002, 0)
					_ = d.SetUint(0x2002, 0, uint64(uint32(v+1)))
				}
			}
		}()
	}
	if em != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Duration(emcyAfter) * time.Millisecond):
			}
			if err := n.EMCY().Raise(ctx, em.code, emcy.ErrorRegister(em.reg), em.msef); err != nil {
				logf("EMCY: %v", err)
			}
		}()
	}
	<-ctx.Done()
	wg.Wait()

	values := map[string]any{}
	for _, k := range keys {
		b, err := d.Get(k.index, k.sub)
		if err != nil {
			values[hexKey(k.index, k.sub)] = nil
		} else {
			values[hexKey(k.index, k.sub)] = hexData(b)
		}
	}
	active := n.NodeID()
	_ = n.Close()
	emit(result("device", "node", int(id), "nodeId", int(active), "values", values))
	return nil
}

// waitCtx sleeps d or until the context ends.
func waitCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// nodeArg parses --node-id of a client operation.
func nodeArg(o *opts, s string) (canopen.NodeID, error) {
	if err := o.require("node-id"); err != nil {
		return 0, err
	}
	return parseNode(s, false, false)
}
