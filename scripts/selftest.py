#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reference-to-reference self-test of the canopen-interop images.

Runs every applicable pair of adapters against each other on a Linux vcan
interface and checks the documents of docs/CONTAINER_CONTRACT.md:

  od      every object of fixtures/eds/interop-device.eds is readable on every
          device with the size its data type gives (write-only: abort)
  sdo     every master x every device: expedited, segmented and block upload
          and download, read-back and the device's own view (--dump), SDO
          aborts, a node that does not answer
  nmt     every master x every device: boot-up and heartbeat through the
          master's consumers (listen), NMT start/stop/pre-operational/reset
          commands and the state the heartbeat reports afterwards
  emcy    every master x every device with an EMCY producer: the EMCY the
          device sends, as the master's EMCY consumer reports it
  pdo     every device with event-driven TPDOs x every master that receives
          PDOs: the default TPDO 1 (counter 0x2002), a TPDO configured by
          pdo-configure, a synchronous TPDO driven by each SYNC producer
  eds     eds-check of every EDS/DCF fixture by every checker, and the
          normalized object lists of the checkers compared

Selection is capability driven: a pair or scenario an adapter cannot do is
recorded as "skip" with the reason, never as "pass". A failure listed in
tests/known-differences.tsv is reported as "known", never as "pass". Results
go to <reports>/selftest.json and <reports>/selftest.md; the exit status is 1
when a scenario failed.

Environment:
  DOCKER            docker command (default "docker"; e.g. "sudo docker")
  IMAGE_TEMPLATE    image name with {adapter} (default
                    "ghcr.io/otfabric/canopen-interop-{adapter}:dev")
  ADAPTERS          space-separated subset (default: all)
  SUITES            space-separated subset of od sdo nmt emcy pdo eds (default: all)
  VCAN              interface (default vcan0)
  REPORTS           report directory (default ./reports)
  WORK              scratch directory shared with the containers (default
                    a new directory under /tmp)
"""

import argparse
import configparser
import fnmatch
import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_ADAPTERS = ["canopennode", "lely", "pythoncanopen"]
ALL_SUITES = ["od", "sdo", "nmt", "emcy", "pdo", "eds"]

DOCKER = shlex.split(os.environ.get("DOCKER", "docker"))
TEMPLATE = os.environ.get("IMAGE_TEMPLATE", "ghcr.io/otfabric/canopen-interop-{adapter}:dev")
VCAN = os.environ.get("VCAN", "vcan0")
WORK = os.environ.get("WORK") or tempfile.mkdtemp(prefix="canopen-selftest-")
os.chmod(WORK, 0o777)

NODE = 5
EDS = "/fixtures/eds/interop-device.eds"
# Auxiliary operations (starting a device for PDOs, configuring a PDO) use
# the first adapter of this list that can do them.
HELPER_ORDER = ["pythoncanopen", "canopennode", "lely"]

results = []
DOCS = {}   # adapter -> every JSON document its containers printed
KNOWN = []  # (suite, scenario, master, device, explanation)


def keep(adapter, docs):
    DOCS.setdefault(adapter, []).extend(docs)


def image(adapter):
    return TEMPLATE.format(adapter=adapter)


def load_known():
    path = os.path.join(ROOT, "tests", "known-differences.tsv")
    for line in open(path):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) < 5:
            raise SystemExit("tests/known-differences.tsv: want 5 columns: %r" % line)
        KNOWN.append(tuple(f[:5]))


def known(suite, scenario, master, device):
    for s, sc, m, d, why in KNOWN:
        if s == suite and fnmatch.fnmatch(scenario, sc) and fnmatch.fnmatch(master or "-", m) \
                and fnmatch.fnmatch(device or "-", d):
            return why
    return None


def record(suite, scenario, status, detail="", master="", device="", duration=0.0):
    if status == "fail":
        why = known(suite, scenario, master, device)
        if why:
            status, detail = "known", "%s [known: %s]" % (detail, why)
    r = {"suite": suite, "scenario": scenario, "master": master, "device": device,
         "status": status, "detail": detail, "seconds": round(duration, 2)}
    results.append(r)
    pair = (master + " -> " + device) if master or device else ""
    print("%-5s %-5s %-30s %-30s %s" % (status.upper(), suite, scenario, pair, detail[:200]), flush=True)


def check(suite, scenario, ok, detail, master="", device="", start=None):
    record(suite, scenario, "pass" if ok else "fail", detail, master, device,
           time.monotonic() - start if start else 0.0)
    return ok


# ── containers ──────────────────────────────────────────────────────────────

def docker_argv(adapter, args, name=None):
    argv = DOCKER + ["run", "--rm", "-i", "--network", "host", "-v", "%s:/work" % WORK]
    if name:
        argv += ["--name", name]
    return argv + [image(adapter)] + [str(a) for a in args]


def parse_docs(text):
    docs = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                docs.append(json.loads(line))
            except ValueError:
                pass
    return docs


def run(adapter, args, timeout=60):
    """Run a container to completion: (exit code, documents, stderr)."""
    try:
        p = subprocess.run(docker_argv(adapter, args), stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, [], "harness timeout"
    docs = parse_docs(p.stdout)
    if args and args[0] not in ("print-capabilities", "print-version"):
        keep(adapter, docs)
    return p.returncode, docs, p.stderr


def result_of(docs):
    for d in reversed(docs):
        if d.get("type") in ("result", "error"):
            return d
    return {}


class Background:
    """A container whose stdout is read line by line (devices, listeners)."""

    def __init__(self, adapter, args):
        self.adapter = adapter
        self.name = "coi-" + uuid.uuid4().hex[:10]
        self.proc = subprocess.Popen(docker_argv(adapter, args, self.name), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.lines = queue.Queue()
        self.docs = []
        self.seen = 0
        self.stderr = ""
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_err(self):
        self.stderr = self.proc.stderr.read()

    def pump(self, timeout):
        """Collect lines for up to timeout seconds; False at end of output."""
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return True
        if line is None:
            self.lines.put(None)
            return False
        self.docs += parse_docs(line)
        return True

    def wait_for(self, pred, timeout):
        """Wait until a document after the last mark satisfies pred."""
        end = time.monotonic() + timeout
        while True:
            for i in range(self.seen, len(self.docs)):
                if pred(self.docs[i]):
                    self.seen = i + 1
                    return self.docs[i]
            if time.monotonic() >= end:
                return None
            if not self.pump(min(0.1, end - time.monotonic())) and self.proc.poll() is not None:
                for i in range(self.seen, len(self.docs)):
                    if pred(self.docs[i]):
                        self.seen = i + 1
                        return self.docs[i]
                return None

    def mark(self):
        while self.pump(0.0) and not self.lines.empty():
            pass
        self.seen = len(self.docs)

    def wait_ready(self, timeout=30):
        return self.wait_for(lambda d: d.get("type") == "ready", timeout) is not None

    def stop(self, timeout=30):
        """SIGTERM (docker stop): a device prints its result and exits."""
        if self.proc.poll() is None:
            subprocess.run(DOCKER + ["stop", "-t", "10", self.name], capture_output=True)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(DOCKER + ["kill", self.name], capture_output=True)
            self.proc.wait()
        while self.pump(1.0):
            pass
        keep(self.adapter, self.docs)
        return self.proc.returncode

    def result(self):
        return result_of(self.docs)

    def error(self):
        errs = [d for d in self.docs if d.get("type") == "error"]
        return errs[-1].get("message", "") if errs else self.stderr.strip()[-300:]


def capabilities(adapter):
    code, docs, err = run(adapter, ["print-capabilities"], timeout=120)
    if code != 0 or not docs:
        raise SystemExit("cannot read the capabilities of %s (%s): %s" % (adapter, image(adapter), err))
    return docs[0]


def can(caps, adapter, op, feature=None):
    c = caps[adapter]
    return op in c["operations"] and (feature is None or c["features"].get(feature, False))


def helper(caps, op, feature=None):
    for a in HELPER_ORDER:
        if a in caps and can(caps, a, op, feature):
            return a
    return None


def start_device(adapter, extra=(), timeout_ms=600000):
    dev = Background(adapter, ["device", "--interface", VCAN, "--node-id", NODE, "--eds", EDS,
                               "--timeout-ms", timeout_ms] + list(extra))
    if not dev.wait_ready(30):
        dev.stop()
        return None, dev
    time.sleep(0.2)
    return dev, dev


# ── EDS model (for the expected sizes; read with configparser) ──────────────

SIZES = {0x0001: 1, 0x0002: 1, 0x0003: 2, 0x0004: 4, 0x0005: 1, 0x0006: 2, 0x0007: 4, 0x0008: 4,
         0x0011: 8, 0x0015: 8, 0x001B: 8}


def eds_entries(path):
    """[(index, subindex, dataType, access, default)] of an EDS (objects with sub-sections or VARs)."""
    cfg = configparser.RawConfigParser()
    cfg.optionxform = str
    cfg.read(path)
    out = []
    for section in ("MandatoryObjects", "OptionalObjects", "ManufacturerObjects"):
        for i in range(1, int(cfg[section]["SupportedObjects"]) + 1):
            index = int(cfg[section][str(i)], 16)
            name = "%04X" % index
            subs = [s for s in cfg.sections() if s.upper().startswith(name + "SUB")]
            if not subs:
                s = cfg[name]
                out.append((index, 0, int(s["DataType"], 16), s["AccessType"], s.get("DefaultValue", "")))
            for sname in subs:
                s = cfg[sname]
                out.append((index, int(sname[len(name) + 3:], 16), int(s["DataType"], 16), s["AccessType"],
                            s.get("DefaultValue", "")))
    return sorted(out)


# ── od ──────────────────────────────────────────────────────────────────────

def suite_od(caps):
    """Every object of the shared EDS on every device, read by one master."""
    master = helper(caps, "sdo-upload")
    entries = eds_entries(os.path.join(ROOT, "fixtures", "eds", "interop-device.eds"))
    for dev_a in [a for a in caps if can(caps, a, "device")]:
        start = time.monotonic()
        dev, bg = start_device(dev_a)
        if dev is None:
            record("od", "objects", "fail", "device not ready: " + bg.error(), master, dev_a)
            continue
        bad, n = [], 0
        try:
            for index, sub, dtype, access, default in entries:
                if dtype == 0x000F:
                    continue  # DOMAIN: scenario empty-domain
                code, docs, _ = run(master, ["sdo-upload", "--interface", VCAN, "--node-id", NODE,
                                             "--index", "0x%04X" % index, "--subindex", sub, "--timeout-ms", 2000])
                r = result_of(docs)
                n += 1
                key = "0x%04X:%02X" % (index, sub)
                if access == "wo":
                    if r.get("abortCode") != "0x06010001":
                        bad.append("%s wo: %s" % (key, r.get("abortCode") or "exit %d" % code))
                    continue
                if code != 0 and index == 0x1003 and sub > 0 and r.get("abortCode") == "0x08000024":
                    continue  # an empty error history entry: "no data available" is allowed (CiA 301 7.5.2.4)
                if code != 0:
                    bad.append("%s: %s" % (key, r.get("message", "exit %d" % code)[:80]))
                    continue
                size = r.get("size")
                want = SIZES.get(dtype)
                if dtype == 0x0009:
                    want = len(default.encode())
                if want is not None and size != want:
                    bad.append("%s: %d bytes, want %d" % (key, size, want))
            code, docs, _ = run(master, ["sdo-upload", "--interface", VCAN, "--node-id", NODE, "--index", "0x2005"])
            r = result_of(docs)
            check("od", "empty-domain", code == 0 and r.get("size") == 0,
                  "0 bytes before any download" if code == 0 and r.get("size") == 0 else
                  "exit %d %s" % (code, r.get("abortCode") or r.get("data")), master, dev_a)
        finally:
            dev.stop()
        record("od", "objects", "fail" if bad else "pass",
               "; ".join(bad[:8]) + (" (+%d)" % (len(bad) - 8) if len(bad) > 8 else "") if bad
               else "%d sub-objects with the size of their data type" % n, master, dev_a, time.monotonic() - start)


# ── sdo ─────────────────────────────────────────────────────────────────────

DOMAIN = bytes(i % 251 for i in range(1024))
STRING64 = (b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-")[:64]
assert len(STRING64) == 64

UPLOADS = [  # scenario, index, subindex, expected hex
    ("upload-expedited-u32", 0x1000, 0, "91010f00"),
    ("upload-expedited-u16", 0x2001, 0, "3322"),
    ("upload-expedited-i32", 0x2003, 0, (-12345).to_bytes(4, "little", signed=True).hex()),
    ("upload-expedited-record", 0x1018, 2, "31494f43"),
    ("upload-segmented-u64", 0x2007, 0, "0807060504030201"),
    ("upload-segmented-name", 0x1008, 0, b"otfabric canopen-interop device".hex()),
    ("upload-segmented-string", 0x2004, 0, None),  # the EDS default, 64 characters
]

DOWNLOADS = [  # scenario, index, data, block
    ("download-expedited-u8", 0x2000, bytes([0xA5]), False),
    ("download-expedited-u16", 0x2001, bytes.fromhex("efbe"), False),
    ("download-expedited-u32", 0x2002, bytes.fromhex("78563412"), False),
    ("download-expedited-real32", 0x2006, bytes.fromhex("0000c03f"), False),
    ("download-segmented-u64", 0x2007, bytes.fromhex("1122334455667788"), False),
    ("download-segmented-string", 0x2004, STRING64, False),
    ("download-segmented-domain", 0x2005, DOMAIN[::-1], False),
    ("download-block-domain", 0x2005, DOMAIN, True),
]

# scenario, args, accepted abort codes (CiA 301 allows the general 0x06070010
# for a length mismatch), device feature needed
ABORTS = [
    ("abort-no-object", ["sdo-upload", "--index", "0x2999"], ["0x06020000"], None),
    ("abort-no-subindex", ["sdo-upload", "--index", "0x2000", "--subindex", "1"], ["0x06090011"], None),
    ("abort-write-only", ["sdo-upload", "--index", "0x2009"], ["0x06010001"], None),
    ("abort-read-only", ["sdo-download", "--index", "0x2008", "--data", "00000000"], ["0x06010002"], None),
    ("abort-length-low", ["sdo-download", "--index", "0x2001", "--data", "01"], ["0x06070013", "0x06070010"], None),
    ("abort-length-high", ["sdo-download", "--index", "0x2000", "--data", "0102"], ["0x06070012", "0x06070010"], None),
    ("abort-value-low", ["sdo-download", "--index", "0x200A", "--data", "05"], ["0x06090032"], "deviceLimits"),
    ("abort-value-high", ["sdo-download", "--index", "0x200A", "--data", "fa"], ["0x06090031"], "deviceLimits"),
]


def sdo_pair(caps, m, d):
    dumps = ["0x2000:00", "0x2001:00", "0x2002:00", "0x2004:00", "0x2005:00", "0x2006:00", "0x2007:00"]
    extra = []
    for k in dumps:
        extra += ["--dump", k]
    dev, bg = start_device(d, extra)
    if dev is None:
        record("sdo", "device", "fail", "device not ready: " + bg.error(), m, d)
        return
    base = ["--interface", VCAN, "--node-id", NODE]
    m_block = caps[m]["features"].get("sdoBlock")
    d_block = caps[d]["features"].get("deviceBlock")
    expect = {}
    try:
        for sc, index, sub, want in UPLOADS:
            start = time.monotonic()
            code, docs, _ = run(m, ["sdo-upload"] + base + ["--index", "0x%04X" % index, "--subindex", sub])
            r = result_of(docs)
            if want is None:
                cfg = configparser.RawConfigParser()
                cfg.optionxform = str
                cfg.read(os.path.join(ROOT, "fixtures", "eds", "interop-device.eds"))
                want = cfg["%04X" % index]["DefaultValue"].encode().hex()
            ok = code == 0 and r.get("data") == want
            check("sdo", sc, ok, "%d bytes" % r.get("size", 0) if ok else "exit %d %s" % (
                code, r.get("data") or r.get("message", "")), m, d, start)
        for sc, index, data, block in DOWNLOADS:
            start = time.monotonic()
            if block and not m_block:
                record("sdo", sc, "skip", "master has no SDO block client (sdoBlock)", m, d)
                continue
            if block and not d_block:
                record("sdo", sc, "skip", "device has no SDO block server (deviceBlock)", m, d)
                continue
            flags = ["--block"] if block else []
            code, docs, _ = run(m, ["sdo-download"] + base + ["--index", "0x%04X" % index, "--data", data.hex()] + flags)
            if code != 0:
                check("sdo", sc, False, "download exit %d: %s" % (code, result_of(docs).get("message", "")), m, d, start)
                continue
            expect["0x%04X:00" % index] = data.hex()
            code, docs, _ = run(m, ["sdo-upload"] + base + ["--index", "0x%04X" % index] + flags)
            r = result_of(docs)
            ok = code == 0 and r.get("data") == data.hex()
            check("sdo", sc, ok, "%d bytes written and read back%s" % (len(data), " (block)" if block else "")
                  if ok else "read back: exit %d %s" % (code, (r.get("data") or r.get("message", ""))[:80]), m, d, start)
        if m_block and d_block:
            start = time.monotonic()
            code, docs, _ = run(m, ["sdo-upload"] + base + ["--index", "0x2007", "--block"])
            r = result_of(docs)
            ok = code == 0 and r.get("data") == expect.get("0x2007:00")
            check("sdo", "upload-block-small", ok, "8 bytes by block upload" if ok else "exit %d %s" % (
                code, r.get("data") or r.get("message", "")), m, d, start)
        for sc, args, want, feature in ABORTS:
            start = time.monotonic()
            if feature and not caps[d]["features"].get(feature):
                record("sdo", sc, "skip", "device does not check LowLimit/HighLimit (%s)" % feature, m, d)
                continue
            code, docs, _ = run(m, args[:1] + base + args[1:])
            r = result_of(docs)
            ok = code == 5 and r.get("abortCode") in want
            check("sdo", sc, ok, "abort %s" % r.get("abortCode") if ok else "exit %d abort %s, want %s" % (
                code, r.get("abortCode"), " or ".join(want)), m, d, start)
            if code == 0 and args[0] == "sdo-download" and args[2] == "0x2000":
                expect["0x2000:00"] = args[-1]
        start = time.monotonic()
        code, docs, _ = run(m, ["sdo-upload", "--interface", VCAN, "--node-id", 9, "--index", "0x1000",
                                "--timeout-ms", 1000])
        check("sdo", "no-response", code == 3 and result_of(docs).get("code") == "timeout",
              "exit 3 (timeout)" if code == 3 else "exit %d" % code, m, d, start)
    finally:
        dev.stop()
    values = dev.result().get("values", {})
    diff = ["%s=%s want %s" % (k, (values.get(k) or "")[:16], v[:16]) for k, v in expect.items() if values.get(k) != v]
    record("sdo", "device-dump", "fail" if diff else "pass",
           "; ".join(diff) if diff else "the device's OD holds every downloaded value (%d)" % len(expect), m, d)


def suite_sdo(caps):
    masters = [a for a in caps if can(caps, a, "sdo-upload") and can(caps, a, "sdo-download")]
    devices = [a for a in caps if can(caps, a, "device")]
    for m in masters:
        for d in devices:
            sdo_pair(caps, m, d)


# ── nmt + heartbeat ─────────────────────────────────────────────────────────

def is_state(state):
    return lambda doc: doc.get("type") == "heartbeat" and doc.get("node") == NODE and doc.get("state") == state


def is_bootup(doc):
    return doc.get("type") == "bootup" and doc.get("node") == NODE


def nmt_pair(caps, m, d):
    start = time.monotonic()
    lis = Background(m, ["listen", "--interface", VCAN, "--node-id", NODE, "--count", 1000, "--timeout-ms", 120000])
    if not lis.wait_ready(30):
        record("nmt", "listen", "fail", "listener not ready: " + lis.error(), m, d)
        lis.stop()
        return
    time.sleep(0.3)
    dev, bg = start_device(d, ["--heartbeat-ms", 100])
    if dev is None:
        record("nmt", "device", "fail", "device not ready: " + bg.error(), m, d)
        lis.stop()
        return
    try:
        b = lis.wait_for(is_bootup, 5)
        check("nmt", "bootup", b is not None, "boot-up reported" if b else "no boot-up event", m, d, start)
        s = lis.wait_for(is_state("pre-operational"), 5)
        check("nmt", "heartbeat-pre-operational", s is not None,
              "heartbeat: pre-operational" if s else "no pre-operational heartbeat", m, d, start)
        steps = [("start", NODE, "operational", False), ("stop", NODE, "stopped", False),
                 ("pre-operational", NODE, "pre-operational", False), ("start", 0, "operational", False),
                 ("reset-communication", NODE, "pre-operational", True), ("start", NODE, "operational", False),
                 ("reset-node", NODE, "pre-operational", True)]
        for command, node, state, boots in steps:
            t0 = time.monotonic()
            lis.mark()
            code, docs, _ = run(m, ["nmt", "--interface", VCAN, "--node-id", node, "--command", command])
            name = "nmt-%s%s" % (command, "-all" if node == 0 else "")
            if code != 0:
                check("nmt", name, False, "exit %d: %s" % (code, result_of(docs).get("message", "")), m, d, t0)
                continue
            booted = lis.wait_for(is_bootup, 5) if boots else True
            reached = lis.wait_for(is_state(state), 5)
            ok = booted is not None and reached is not None
            check("nmt", name, ok, "%sheartbeat: %s" % ("boot-up, " if boots else "", state) if ok else
                  "%s; %s" % ("boot-up" if booted else "no boot-up", state if reached else "no %s heartbeat" % state),
                  m, d, t0)
    finally:
        dev.stop()
        lis.stop()


def suite_nmt(caps):
    masters = [a for a in caps if can(caps, a, "nmt") and can(caps, a, "listen", "listenHeartbeat")]
    devices = [a for a in caps if can(caps, a, "device", "heartbeatProducer")]
    for m in masters:
        for d in devices:
            nmt_pair(caps, m, d)


# ── emcy ────────────────────────────────────────────────────────────────────

# Error code 0x1000 (generic), register 0x01 (generic), errorBit 0x2B and info
# 0x04030201: the one vector all three producers can send (CANopenNode
# derives the register from the error bit, lely always sets bit 0).
EMCY = ("0x1000", "0x01", "2b01020304")


def suite_emcy(caps):
    masters = [a for a in caps if can(caps, a, "listen", "listenEmcy")]
    devices = [a for a in caps if can(caps, a, "device", "emcy")]
    for m in masters:
        for d in devices:
            start = time.monotonic()
            lis = Background(m, ["listen", "--interface", VCAN, "--node-id", NODE, "--count", 100,
                                 "--timeout-ms", 60000])
            if not lis.wait_ready(30):
                record("emcy", "emcy", "fail", "listener not ready: " + lis.error(), m, d)
                lis.stop()
                continue
            time.sleep(0.3)
            dev, bg = start_device(d, ["--emcy", "%s:%s:%s" % (EMCY[0][2:], EMCY[1][2:], EMCY[2]),
                                       "--emcy-after-ms", 500])
            try:
                if dev is None:
                    record("emcy", "emcy", "fail", "device not ready: " + bg.error(), m, d)
                    continue
                e = lis.wait_for(lambda doc: doc.get("type") == "emcy" and doc.get("node") == NODE, 6)
                got = (e.get("code"), e.get("register"), e.get("data")) if e else None
                check("emcy", "emcy", got == EMCY, "code %s register %s data %s" % EMCY if got == EMCY else
                      "got %s, want %s" % (got, EMCY), m, d, start)
            finally:
                if dev is not None:
                    dev.stop()
                lis.stop()


# ── pdo ─────────────────────────────────────────────────────────────────────

def increasing(events, key="0x2002:00", strict=True):
    """The counter in successive PDOs increases. With an event timer a PDO may
    repeat the last value (strict=False: never decreasing, rising overall)."""
    vals = [int.from_bytes(bytes.fromhex(e["values"][key]), "little") for e in events if key in e.get("values", {})]
    if strict:
        ok = all(b > a for a, b in zip(vals, vals[1:]))
    else:
        ok = all(b >= a for a, b in zip(vals, vals[1:])) and len(vals) > 1 and vals[-1] > vals[0]
    return len(vals) == len(events) and ok, vals


def suite_pdo(caps):
    devices = [a for a in caps if can(caps, a, "device", "tick") and caps[a]["features"].get("deviceTpdo")]
    for a in caps:
        if a not in devices and can(caps, a, "device"):
            record("pdo", "tpdo", "skip", "device has no TPDO producer (tick/deviceTpdo)", "", a)
    masters = [a for a in caps if can(caps, a, "pdo-receive")]
    for a in caps:
        if a not in masters:
            record("pdo", "pdo-receive", "skip", "adapter cannot receive PDOs (pdo-receive)", a, "")
    starter = helper(caps, "nmt")
    configurer = helper(caps, "pdo-configure")
    syncers = [a for a in caps if can(caps, a, "sync")]
    base = ["--interface", VCAN, "--node-id", NODE]
    for d in devices:
        dev, bg = start_device(d, ["--tick-ms", 100])
        if dev is None:
            record("pdo", "device", "fail", "device not ready: " + bg.error(), "", d)
            continue
        try:
            code, _, _ = run(starter, ["nmt"] + base + ["--command", "start"])
            time.sleep(0.6)
            for m in masters:
                start = time.monotonic()
                code, docs, _ = run(m, ["pdo-receive"] + base + ["--tpdo", 1, "--count", 3, "--timeout-ms", 5000])
                ev = [x for x in docs if x.get("type") == "pdo"]
                ok, vals = increasing(ev)
                check("pdo", "tpdo1-event", code == 0 and ok and len(ev) == 3,
                      "3 PDOs, counter %s" % vals if code == 0 and ok else "exit %d: %s %s" % (
                          code, vals, result_of(docs).get("message", "")), m, d, start)
            if configurer is None:
                record("pdo", "pdo-configure", "skip", "no adapter with pdo-configure", "", d)
            else:
                start = time.monotonic()
                code, docs, _ = run(configurer, ["pdo-configure"] + base + [
                    "--tpdo", 2, "--map", "0x2002:00:32,0x2001:00:16", "--transmission-type", 255,
                    "--event-timer-ms", 150])
                configured = code == 0
                check("pdo", "pdo-configure-tpdo2", configured, "TPDO 2: 0x2002:00 + 0x2001:00, event timer 150 ms"
                      if configured else "exit %d: %s" % (code, result_of(docs).get("message", "")), configurer, d, start)
                for m in masters if configured else []:
                    start = time.monotonic()
                    code, docs, _ = run(m, ["pdo-receive"] + base + ["--tpdo", 2, "--count", 4, "--timeout-ms", 5000])
                    ev = [x for x in docs if x.get("type") == "pdo"]
                    ok, vals = increasing(ev, strict=False)
                    ok = ok and all(e["values"].get("0x2001:00") == "3322" for e in ev)
                    check("pdo", "tpdo2-configured", code == 0 and ok and len(ev) == 4,
                          "4 PDOs, counter %s, 0x2001 3322" % vals if code == 0 and ok else "exit %d: counter %s %s" % (
                              code, vals, [e.get("values") for e in ev] or result_of(docs).get("message", "")), m, d, start)
                code, docs, _ = run(configurer, ["pdo-configure"] + base + [
                    "--tpdo", 3, "--map", "0x2002:00:32", "--transmission-type", 1])
                if code != 0:
                    record("pdo", "sync-tpdo3", "fail", "pdo-configure TPDO 3 (synchronous): exit %d %s" % (
                        code, result_of(docs).get("message", "")), configurer, d)
                for s in syncers if code == 0 else []:
                    for m in masters:
                        start = time.monotonic()
                        rx = Background(m, ["pdo-receive"] + base + ["--tpdo", 3, "--count", 3, "--timeout-ms", 8000])
                        if not rx.wait_ready(10):
                            rx.stop()
                            record("pdo", "sync-tpdo3", "fail", "receiver not ready: " + rx.error(), m, d)
                            continue
                        scode, sdocs, _ = run(s, ["sync", "--interface", VCAN, "--count", 4, "--period-ms", 200])
                        rx.stop()
                        ev = [x for x in rx.docs if x.get("type") == "pdo"]
                        sent = result_of(sdocs).get("sent")
                        check("pdo", "sync-tpdo3", scode == 0 and sent == 4 and len(ev) >= 3,
                              "%s sent 4 SYNCs, %s received %d synchronous PDOs" % (s, m, len(ev))
                              if scode == 0 and len(ev) >= 3 else "sync exit %d sent %s, %d PDOs" % (
                                  scode, sent, len(ev)), "%s+%s" % (s, m), d, start)
        finally:
            dev.stop()


# ── eds ─────────────────────────────────────────────────────────────────────

EDS_FIXTURES = [  # file, expected validity
    ("interop-device.eds", True),
    ("interop-device-node5.dcf", True),
    ("compact-subobj.eds", True),
    ("object-links.eds", True),
    ("invalid-missing-datatype.eds", False),
]


def suite_eds(caps):
    checkers = [a for a in caps if can(caps, a, "eds-check")]
    for a in caps:
        if a not in checkers:
            record("eds", "eds-check", "skip", "adapter has no EDS checker (eds-check)", a, "")
    views = {}
    for name, valid in EDS_FIXTURES:
        for a in checkers:
            start = time.monotonic()
            code, docs, _ = run(a, ["eds-check", "--eds", "/fixtures/eds/" + name])
            r = result_of(docs)
            ok = code == 0 and r.get("valid") is valid
            check("eds", "valid-" + name, ok, "valid=%s, %d objects%s" % (
                r.get("valid"), len(r.get("objects", [])),
                (", " + "; ".join(r.get("errors", []))[:120]) if r.get("errors") else "")
                if code == 0 else "exit %d %s" % (code, r.get("message", "")), a, "", start)
            if code == 0 and valid:
                views.setdefault(name, {})[a] = {(o["index"], o["subindex"]): o for o in r.get("objects", [])}
    # The normalized views of the checkers must agree with each other and
    # with the fixture itself (index, sub-index, data type, access, PDO mapping).
    for name, by in views.items():
        expected = None
        if name in ("interop-device.eds", "interop-device-node5.dcf"):
            expected = {("0x%04X" % i, s): (dt, acc.lower()) for i, s, dt, acc, _ in
                        eds_entries(os.path.join(ROOT, "fixtures", "eds", name))}
        names = sorted(by)
        for a in names:
            if expected is None:
                continue
            got = {k: (int(v["dataType"], 16), v["access"]) for k, v in by[a].items()}
            want = {k: v for k, v in expected.items()}
            diff = sorted(set(got.items()) ^ set(want.items()))
            record("eds", "objects-" + name, "fail" if diff else "pass",
                   "differs from the fixture: %s" % diff[:6] if diff else "%d sub-objects as in the fixture" % len(got),
                   a, "")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                fields = ("dataType", "access", "pdoMapping", "default")
                keys = set(by[a]) | set(by[b])
                diff = []
                for k in sorted(keys):
                    x, y = by[a].get(k), by[b].get(k)
                    if x is None or y is None:
                        diff.append("%s:%s only in %s" % (k[0], k[1], a if y is None else b))
                        continue
                    for f in fields:
                        if x.get(f) != y.get(f):
                            diff.append("%s:%s %s %r/%r" % (k[0], k[1], f, x.get(f), y.get(f)))
                record("eds", "agree-" + name, "fail" if diff else "pass",
                       "; ".join(diff[:6]) + (" (+%d)" % (len(diff) - 6) if len(diff) > 6 else "") if diff
                       else "%d sub-objects agree" % len(keys), a, b)


# ── report ──────────────────────────────────────────────────────────────────

def report(caps, outdir, started):
    os.makedirs(outdir, exist_ok=True)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    versions = {}
    for a in caps:
        code, docs, _ = run(a, ["print-version"])
        versions[a] = docs[0] if docs else {}
    doc = {"schemaVersion": "1.0", "started": started, "kernel": os.uname().release, "interface": VCAN,
           "images": {a: image(a) for a in caps}, "versions": versions,
           "capabilities": caps, "counts": counts, "results": results}
    os.makedirs(os.path.join(outdir, "documents"), exist_ok=True)
    for a, docs in DOCS.items():
        with open(os.path.join(outdir, "documents", a + ".jsonl"), "w") as f:
            for d in docs:
                f.write(json.dumps(d) + "\n")
    for a in caps:
        with open(os.path.join(outdir, "documents", a + ".capabilities.json"), "w") as f:
            f.write(json.dumps(caps[a]) + "\n")
        with open(os.path.join(outdir, "documents", a + ".version.json"), "w") as f:
            f.write(json.dumps(versions[a]) + "\n")
    with open(os.path.join(outdir, "selftest.json"), "w") as f:
        json.dump(doc, f, indent=1)
    with open(os.path.join(outdir, "selftest.md"), "w") as f:
        f.write("# canopen-interop self-test\n\n")
        f.write("Kernel %s, started %s. %s\n\n" % (os.uname().release, started,
                ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items()))))
        f.write("| Suite | Scenario | Master | Device | Result | Detail |\n|---|---|---|---|---|---|\n")
        for r in results:
            f.write("| %s | %s | %s | %s | %s | %s |\n" % (r["suite"], r["scenario"], r["master"], r["device"],
                                                          r["status"], r["detail"].replace("|", "\\|")[:200]))
    print("\n%s  (reports in %s)" % (", ".join("%d %s" % (v, k) for k, v in sorted(counts.items())), outdir))
    return counts.get("fail", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default=os.environ.get("REPORTS", os.path.join(ROOT, "reports")))
    args = ap.parse_args()
    load_known()
    adapters = os.environ.get("ADAPTERS", " ".join(ALL_ADAPTERS)).split()
    suites = os.environ.get("SUITES", " ".join(ALL_SUITES)).split()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    caps = {a: capabilities(a) for a in adapters}
    for name, fn in (("od", suite_od), ("sdo", suite_sdo), ("nmt", suite_nmt), ("emcy", suite_emcy),
                     ("pdo", suite_pdo), ("eds", suite_eds)):
        if name in suites:
            fn(caps)
    failed = report(caps, args.reports, started)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
