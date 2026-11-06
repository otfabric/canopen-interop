// SPDX-License-Identifier: MIT

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/otfabric/go-canopen"
	"github.com/otfabric/go-canopen/lss"
)

// schemaDir finds schemas/ of the harness (the repository root is two
// levels up; in the image build it is not there and the test skips).
func schemaDir(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	dir := filepath.Join(filepath.Dir(file), "..", "..", "schemas")
	if _, err := os.Stat(filepath.Join(dir, "capabilities.schema.json")); err != nil {
		t.Skip("schemas/ not found (built outside the repository)")
	}
	return dir
}

func TestCapabilitiesDeclareEveryFeature(t *testing.T) {
	var schema struct {
		Properties struct {
			Operations struct {
				Items struct {
					Enum []string `json:"enum"`
				} `json:"items"`
			} `json:"operations"`
			Features struct {
				Properties map[string]any `json:"properties"`
			} `json:"features"`
		} `json:"properties"`
	}
	b, err := os.ReadFile(filepath.Join(schemaDir(t), "capabilities.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &schema); err != nil {
		t.Fatal(err)
	}
	for f := range schema.Properties.Features.Properties {
		if _, ok := features[f]; !ok {
			t.Errorf("feature %s not declared", f)
		}
	}
	for f := range features {
		if _, ok := schema.Properties.Features.Properties[f]; !ok {
			t.Errorf("feature %s not in the schema", f)
		}
	}
	if fmt.Sprint(schema.Properties.Operations.Items.Enum) != fmt.Sprint(operations) {
		t.Errorf("operations %v, schema %v", operations, schema.Properties.Operations.Items.Enum)
	}
	h := handlers()
	for _, op := range operations {
		if h[op] == nil {
			t.Errorf("no handler for %s", op)
		}
	}
}

func TestUpstreamNotIndependent(t *testing.T) {
	up := capabilitiesDoc()["upstream"].(map[string]any)
	if up["independentEngine"] != false || up["name"] != "go-canopen" {
		t.Fatalf("upstream %v", up)
	}
}

func exitOf(err error) (int, string) {
	var ae *adapterError
	if errors.As(err, &ae) {
		return ae.exit, ae.code
	}
	return exitInternal, "internal"
}

func TestUsageErrors(t *testing.T) {
	for _, args := range [][]string{
		{"sdo-upload", "--no-such-flag"},
		{"sdo-upload", "--node-id", "5"},
		{"sdo-upload", "--node-id", "0", "--index", "0x1000"},
		{"sdo-download", "--node-id", "5", "--index", "0x1000", "--data", "zz"},
		{"nmt", "--node-id", "5", "--command", "jump"},
		{"lss", "--step", "configure-bit-timing:5"},
		{"lss", "--step", "inquire:colour"},
		{"mpdo-send", "--node-id", "5", "--mode", "dam", "--cob-id", "0x300", "--index", "0x2000", "--data", "0102030405"},
		{"mpdo-receive", "--node-id", "5", "--mode", "sam", "--cob-id", "0x300"},
		{"cdcf", "--dcf", "/nonexistent", "--node-id", "5"},
		{"eds-check", "--eds", "/nonexistent"},
		{"no-such-command"},
	} {
		code, kind := exitOf(run(args))
		if code != exitUsage || kind != "usage" {
			t.Errorf("%v: exit %d %s, want 2 usage", args, code, kind)
		}
	}
}

func TestMissingInterface(t *testing.T) {
	code, kind := exitOf(run([]string{"sdo-upload", "--interface", "nosuchcan0", "--node-id", "5", "--index", "0x1000"}))
	if code != exitEnv || kind != "interface" {
		t.Fatalf("exit %d %s, want 4 interface", code, kind)
	}
}

func TestClassify(t *testing.T) {
	abort := &canopen.AbortError{Code: canopen.AbortCode(0x06020000), Index: 0x3000, Remote: true}
	var ae *adapterError
	if !errors.As(classify("x", abort), &ae) || ae.exit != exitProtocol || ae.extra["abortCode"] != "0x06020000" {
		t.Errorf("remote abort: %+v", ae)
	}
	to := fmt.Errorf("sdo: %w: %w", canopen.ErrTimeout, &canopen.AbortError{Code: canopen.AbortCode(0x05040000)})
	if !errors.As(classify("x", to), &ae) || ae.exit != exitTimeout || ae.extra["abortCode"] != "0x05040000" {
		t.Errorf("timeout: %+v", ae)
	}
	cfg := &lss.ConfigError{Command: lss.CmdSwitchGlobal, Code: 1}
	if !errors.As(lssFail(lssStep{name: "configure-node-id"}, cfg), &ae) || ae.exit != exitProtocol || ae.extra["lssError"] != 1 {
		t.Errorf("LSS refusal: %+v", ae)
	}
	if !errors.As(lssFail(lssStep{name: "store"}, canopen.ErrTimeout), &ae) || ae.exit != exitTimeout || ae.extra["step"] != "store" {
		t.Errorf("LSS timeout: %+v", ae)
	}
}

func TestParseLSSSteps(t *testing.T) {
	for _, s := range []string{
		"switch-global:waiting", "switch-global:configuration", "switch-selective:0:0x434F4931:0x00010000:7",
		"configure-node-id:40", "configure-node-id:255", "configure-bit-timing:3", "configure-bit-timing:9",
		"activate-bit-timing:50", "store", "inquire:serial", "inquire:node-id", "fastscan", "assign-all:126:store",
	} {
		if _, err := parseLSSStep(s); err != nil {
			t.Errorf("%s: %v", s, err)
		}
	}
	for _, s := range []string{"switch-global:x", "configure-node-id:0", "configure-node-id:128", "store:1",
		"assign-all:0", "assign-all:5:keep", "switch-selective:1:2:3"} {
		if _, err := parseLSSStep(s); err == nil {
			t.Errorf("%s: accepted", s)
		}
	}
}

func TestParseValues(t *testing.T) {
	m, names, err := parseMapping("0x2002:00:32,0x2001:00:16")
	if err != nil || len(m) != 2 || names[1] != "0x2001:00:16" || m[0].Bits != 32 {
		t.Fatalf("mapping %v %v %v", m, names, err)
	}
	if _, _, err := parseMapping("0x2002:00:65"); err == nil {
		t.Error("65 bits accepted")
	}
	nv, err := parseNodeValue("21:0x2001:00=0x4415")
	if err != nil || nv.node != 21 || nv.index != 0x2001 || nv.value != "0x4415" {
		t.Fatalf("%+v %v", nv, err)
	}
	if nv, err := parseNodeValue("0x2003:00=-2"); err != nil || nv.node != 0 || nv.value != "-2" {
		t.Fatalf("%+v %v", nv, err)
	}
	e, err := parseEMCY("1000:01:2b01020304")
	if err != nil || e.code != 0x1000 || e.reg != 1 || e.msef != [5]byte{0x2b, 1, 2, 3, 4} {
		t.Fatalf("%+v %v", e, err)
	}
	if id, err := parseNode("255", false, true); err != nil || id != 255 {
		t.Fatalf("%v %v", id, err)
	}
	if _, err := parseNode("255", false, false); err == nil {
		t.Error("255 accepted")
	}
}
