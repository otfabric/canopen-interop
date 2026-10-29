# SPDX-License-Identifier: MIT
"""canopen-interop adapter for Lely CANopen (lely-core).

Every CANopen operation is done by lely-core through the helper lely-coi
(adapters/lely/lely-coi.cpp: liblely-coapp BasicSlave, AsyncMaster, Sdo and
the liblely-co NMT and Receive-PDO services) and, for eds-check, by
Lely's Python dcf-tools (the dcfchk checker). This file builds the DCF files
lely-coi loads (the device's EDS with its power-on heartbeat time, the
master's DCF with the consumers to run), starts lely-coi and translates its
output lines into JSON documents (adapters/common/coi.py).
"""

import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import warnings

import coi

ADAPTER = "lely"
MASTER_NODE_ID = 0x7F

CAPABILITIES = {
    "features": {
        "sdoExpedited": True,
        "sdoSegmented": True,
        "sdoBlock": True,
        "deviceSegmented": True,
        "deviceBlock": True,
        "heartbeatProducer": True,
        "emcy": True,                  # bit 0 of the error register is always set by lely
        "tick": True,
        "deviceTpdo": True,
        "deviceRpdo": True,
        "deviceLimits": True,
        "listenBootup": True,
        "listenHeartbeat": True,
        "listenEmcy": True,
        "listenAllNodes": True,
        "pdoConfigure": False,
        "pdoReceive": True,
        "sync": False,                 # not provided in v0.1.0 (LIMITATIONS.md)
        "edsCheck": True,
        "cia402": False,
        # contract 1.1
        "lssMaster": True,             # coapp LssMaster in the CiA 302 boot-up (LIMITATIONS.md)
        "lssBitTiming": True,          # not table index 9 (automatic): coapp takes bit/s
        "lssFastscan": True,
        "lssAssignAll": False,         # lely-core has no "configure all nodes" service
        "lssSlave": True,              # co_lss slave of BasicSlave (LSS_Supported=1)
        "lssSlaveBitTiming": True,
        "lssSlaveStore": True,         # BasicSlave::OnStore (RAM, for the run)
        "rpdoSend": False,
        "mpdoDam": True,               # co_dam_mpdo_event, co_rpdo (DAM consumer)
        "mpdoSam": True,               # co_sam_mpdo_event with 0x1FA0, co_rpdo with 0x1FD0
        "boot": True,                  # CiA 302 NMT master boot-up, concise DCF (0x1F22) from dcfgen
        "cdcfDcfgen": True,            # dcf-tools dcfgen
        "cdcfObjects": True,           # co_dev_write_dcf
        "syncWindowRpdo": True,        # co_rpdo discards synchronous RPDOs after the window (0x1007)
    },
}

VERSION = {
    "upstream": "lely-core",
    "repository": "https://gitlab.com/lely_industries/lely-core",
    "version": os.environ.get("UPSTREAM_VERSION", ""),
    "revision": os.environ.get("UPSTREAM_REVISION", ""),
    "license": "Apache-2.0",
    "stack": "Lely CANopen %s: liblely-coapp/liblely-co on SocketCAN (io2), Python dcf-tools" % (
        os.environ.get("UPSTREAM_VERSION", "")),
    "adapterVersion": os.environ.get("ADAPTER_VERSION", "dev"),
}

NMT_CS = {"start": 0x01, "stop": 0x02, "pre-operational": 0x80, "reset-node": 0x81, "reset-communication": 0x82}


# ── DCF files for lely-coi ─────────────────────────────────────────────────

def _var(index, name, dtype, access, value, sub=None, pdo=False):
    head = "[%X]" % index if sub is None else "[%Xsub%X]" % (index, sub)
    return [head, "ParameterName=%s" % name, "ObjectType=0x7", "DataType=%s" % dtype,
            "AccessType=%s" % access, "DefaultValue=%s" % value, "PDOMapping=%d" % pdo, ""]


def _array(index, name, dtype, access, values, pdo=False, otype="0x8"):
    out = ["[%X]" % index, "ParameterName=%s" % name, "ObjectType=%s" % otype, "SubNumber=0x%X" % (len(values) + 1), ""]
    out += _var(index, "Highest sub-index supported", "0x0005", "ro", "0x%02X" % len(values), sub=0)
    for i, v in enumerate(values, 1):
        out += _var(index, "%s %d" % (name, i), dtype, access, v, sub=i, pdo=pdo)
    return out


def master_dcf(path, hb_nodes=(), emcy_nodes=(), lss=False):
    """The master's DCF: NMT master (0x1F80 bit 0) without slaves (0x1F81),
    heartbeat consumers (0x1016) and EMCY consumers (0x1028) for the given
    nodes, one Receive-PDO (0x1400/0x1600) and its target arrays 0x5F00-0x5F03
    (UNSIGNED8/16/32/64), the SYNC objects 0x1005/0x1006."""
    objs = []
    objs += _var(0x1000, "Device type", "0x0007", "ro", "0x00000000")
    objs += _var(0x1001, "Error register", "0x0005", "ro", "0x00")
    objs += _var(0x1005, "COB-ID SYNC message", "0x0007", "rw", "0x00000080")
    objs += _var(0x1006, "Communication cycle period", "0x0007", "rw", "0")
    hb = ["0x%08X" % ((n << 16) | 0xFFFF) for n in hb_nodes] or ["0x00000000"]
    objs += _array(0x1016, "Consumer heartbeat time", "0x0007", "rw", hb)
    objs += _var(0x1017, "Producer heartbeat time", "0x0006", "rw", "0")
    objs += ["[1018]", "ParameterName=Identity object", "ObjectType=0x9", "SubNumber=0x2", ""]
    objs += _var(0x1018, "Highest sub-index supported", "0x0005", "ro", "1", sub=0)
    objs += _var(0x1018, "Vendor-ID", "0x0007", "ro", "0x00000000", sub=1)
    # sub-index n of 0x1028 is the consumer of node n
    em = ["0x%08X" % ((0x80 + n) | (0 if n in emcy_nodes else 0x80000000)) for n in range(1, 128)]
    objs += _array(0x1028, "Emergency consumer object", "0x0007", "rw", em)
    objs += ["[1400]", "ParameterName=RPDO communication parameter", "ObjectType=0x9", "SubNumber=0x3", ""]
    objs += _var(0x1400, "Highest sub-index supported", "0x0005", "ro", "2", sub=0)
    objs += _var(0x1400, "COB-ID used by RPDO", "0x0007", "rw", "0x80000200", sub=1)
    objs += _var(0x1400, "Transmission type", "0x0005", "rw", "0xFF", sub=2)
    objs += _array(0x1600, "RPDO mapping", "0x0007", "rw", ["0x00000000"] * 8, otype="0x9")
    objs += _var(0x1F80, "NMT startup", "0x0007", "rw", "0x00000001")
    for i, dt in enumerate(("0x0005", "0x0006", "0x0007", "0x001B")):
        objs += _array(0x5F00 + i, "RPDO target %s" % dt, dt, "rww", ["0"] * 8, pdo=True)
    indices = sorted({int(l[1:-1].split("sub")[0], 16) for l in objs if l.startswith("[") and "sub" not in l})
    mandatory = [i for i in indices if i in (0x1000, 0x1001, 0x1018)]
    optional = [i for i in indices if i not in mandatory and i < 0x2000]
    manuf = [i for i in indices if i >= 0x2000]
    head = ["[FileInfo]", "FileName=coi-master.dcf", "EDSVersion=4.0", "Description=canopen-interop lely master", "",
            "[DeviceInfo]", "VendorName=otfabric", "VendorNumber=0x00000000", "ProductName=canopen-interop lely master",
            "BaudRate_500=1", "SimpleBootUpMaster=1", "SimpleBootUpSlave=0", "Granularity=8", "NrOfRXPDO=1",
            "NrOfTXPDO=0", "LSS_Supported=%d" % lss, "",
            "[DeviceComissioning]", "NodeID=%d" % MASTER_NODE_ID, "CANopenManager=1", "",
            "[DummyUsage]"] + ["Dummy%04X=0" % i for i in range(1, 8)] + [""]
    for title, group in (("MandatoryObjects", mandatory), ("OptionalObjects", optional),
                         ("ManufacturerObjects", manuf)):
        head += ["[%s]" % title, "SupportedObjects=%d" % len(group)]
        head += ["%d=0x%04X" % (n, i) for n, i in enumerate(group, 1)] + [""]
    with open(path, "w") as f:
        f.write("\n".join(head + objs) + "\n")
    return path


def device_dcf(eds, heartbeat_ms, params=None, startup=0x00000004):
    """The device's EDS as lely-coi loads it: with 0x1F80 (NMT start-up) =
    0x00000004, so that the device waits in pre-operational for NMT start
    like the other devices (a lely slave without 0x1F80 starts itself), and
    with ParameterValue of 0x1017 set to the power-on heartbeat time (lely
    restores it on NMT reset). params: further ParameterValues, keyed by
    section name ("1018sub4")."""
    params = {k.upper(): v for k, v in (params or {}).items()}
    if heartbeat_ms is not None:
        params["1017"] = "%d" % heartbeat_ms
    with open(eds, "r", newline="") as f:
        lines = f.read().splitlines()
    out, section, has_1f80, seen = [], None, False, set()
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].upper()
            has_1f80 = has_1f80 or section == "1F80"
            out.append(line)
            if section in params:
                out.append("ParameterValue=%s" % params[section])
                seen.add(section)
            continue
        if section in params and s.lower().startswith("parametervalue="):
            continue
        out.append(line)
    if heartbeat_ms is not None and "1017" not in seen:
        raise coi.usage("the EDS has no object 0x1017 for --heartbeat-ms")
    missing = sorted(set(params) - seen)
    if missing:
        raise coi.usage("the EDS has no sub-object %s" % ", ".join(missing))
    if not has_1f80:
        fixed, section = [], None
        for line in out:
            s = line.strip()
            if s.startswith("["):
                section = s[1:-1].lower()
            if section == "optionalobjects" and s.lower().startswith("supportedobjects="):
                n = int(s.split("=", 1)[1], 0)
                fixed.append("SupportedObjects=%d" % (n + 1))
                fixed.append("%d=0x1F80" % (n + 1))
                continue
            fixed.append(line)
        out = fixed + ["", "[1F80]", "ParameterName=NMT startup", "ObjectType=0x7", "DataType=0x0007",
                       "AccessType=rw", "DefaultValue=0x%08X" % startup, "PDOMapping=0"]
    elif startup != 0x00000004:
        raise coi.usage("the EDS has its own 0x1F80")
    path = "/tmp/coi-device-%d.dcf" % os.getpid()
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path


# ── lely-coi ────────────────────────────────────────────────────────────────

class Helper:
    """lely-coi as a child process; its stdout lines in a queue."""

    def __init__(self, args):
        self.proc = subprocess.Popen(["lely-coi"] + [str(a) for a in args], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/tmp")
        self.lines = queue.Queue()
        self.stderr = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw.decode("utf-8", "replace").rstrip("\n"))
        self.lines.put(None)

    def _read_err(self):
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))

    def next(self, timeout):
        try:
            return self.lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return ""

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def fail(self, line):
        if line and line.startswith("ERROR "):
            code, _, text = line[6:].partition(" ")
            abort = int(code, 16)
            if abort == coi.ABORT_TIMEOUT:
                raise coi.AdapterError(coi.EXIT_TIMEOUT, "timeout", "SDO timeout: %s" % text,
                                       abortCode="0x%08X" % abort)
            raise coi.sdo_abort(abort, "SDO abort 0x%08X: %s" % (abort, text))
        detail = line or " | ".join(self.stderr[-3:])
        raise coi.internal("lely-coi: %s" % (detail or "ended without a result (exit %s)" % self.proc.poll()))


def run_once(args, timeout_ms):
    """Run lely-coi to its RESULT/ERROR line."""
    h = Helper(args)
    deadline = coi.Deadline(timeout_ms + 3000)
    try:
        while True:
            line = h.next(deadline.remaining())
            if line is None or line == "":
                if line == "" and not deadline.expired():
                    continue
                h.fail(None)
            if line.startswith("RESULT"):
                return line[6:].strip()
            if line.startswith(("ERROR", "FATAL")):
                h.fail(line)
    finally:
        h.stop()


def master_path():
    return master_dcf("/tmp/coi-master-%d.dcf" % os.getpid())


def cmd_sdo_upload(args, caps):
    out = run_once(["upload", args.interface, master_path(), args.node_id, "%04X" % args.index, args.subindex,
                    int(args.block), args.timeout_ms], args.timeout_ms)
    data = bytes.fromhex(out)
    coi.emit(coi.result("sdo-upload", node=args.node_id, index=coi.hex_index(args.index),
                        subindex=args.subindex, block=args.block, size=len(data), data=data.hex()))


def cmd_sdo_download(args, caps):
    run_once(["download", args.interface, master_path(), args.node_id, "%04X" % args.index, args.subindex,
              int(args.block), args.timeout_ms, args.data.hex() or "-"], args.timeout_ms)
    coi.emit(coi.result("sdo-download", node=args.node_id, index=coi.hex_index(args.index),
                        subindex=args.subindex, block=args.block, size=len(args.data)))


def cmd_nmt(args, caps):
    run_once(["nmt", args.interface, master_path(), args.node_id, NMT_CS[args.command]], args.timeout_ms)
    coi.emit(coi.result("nmt", node=args.node_id, command=args.command))


def stream(args, helper_args, handle, what):
    """Run lely-coi until --count events or --timeout-ms; handle(line) returns
    a document to count, or None."""
    h = Helper(helper_args)
    deadline = coi.Deadline(args.timeout_ms)
    received = 0
    try:
        while received < args.count and not deadline.expired():
            line = h.next(min(0.2, deadline.remaining()))
            if line == "":
                continue
            if line is None:
                break
            if line == "READY":
                coi.emit(coi.event("ready"))
                continue
            if line.startswith(("ERROR", "FATAL")):
                h.fail(line)
            doc = handle(line)
            if doc is not None:
                received += 1
                coi.emit(doc)
    finally:
        h.stop()
    complete = received >= args.count
    coi.emit(coi.result(args.cmd, received=received, complete=complete))
    if not complete:
        raise coi.timeout("%d of %d %s before the timeout" % (received, args.count, what))


def cmd_listen(args, caps):
    """The boot-up indication and the heartbeat (0x1016) and EMCY (0x1028)
    consumers of a booted lely master. Booting a CiA 302 NMT master sends NMT
    'reset communication' to all nodes first (documented in LIMITATIONS.md)."""
    nodes = [args.node_id] if args.node_id else [n for n in range(1, 128) if n != MASTER_NODE_ID]
    path = master_dcf("/tmp/coi-master-%d.dcf" % os.getpid(), nodes, nodes)

    def handle(line):
        f = line.split()
        if f[0] == "BOOTUP":
            node = int(f[1])
            return coi.bootup_event(node) if node in nodes else None
        if f[0] == "STATE":
            node = int(f[1])
            return coi.heartbeat_event(node, f[2]) if node in nodes else None
        if f[0] == "EMCY":
            node = int(f[1])
            if node in nodes:
                return coi.emcy_event(node, int(f[2], 16), int(f[3], 16), bytes.fromhex(f[4]))
        return None

    stream(args, ["listen", args.interface, path, args.timeout_ms + 1000], handle, "events")


def cmd_pdo_receive(args, caps):
    def handle(line):
        f = line.split()
        if f[0] != "PDO":
            return None
        values = {}
        for kv in f[2:]:
            key, _, hexv = kv.partition("=")
            idx, sub = key.split(":")
            values[coi.hex_key(int(idx, 16), int(sub, 16))] = hexv
        return coi.event("pdo", node=args.node_id, pdo=args.tpdo, cobId="0x%03X" % int(f[1], 16), values=values)

    stream(args, ["pdo-receive", args.interface, master_path(), args.node_id, args.tpdo, args.timeout_ms + 1000],
           handle, "PDOs")


def cmd_eds_check(args, caps):
    """Lely's dcf-tools: lint() (dcfchk) and the Device model built from it."""
    import dcf
    errors, objects, valid = [], [], True
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            cfg = dcf.parse_file(args.eds)
            valid = bool(dcf.lint(cfg))
            # $NODEID needs a node-ID: the DCF's, else 1 (as dcfchk -n 1)
            env = {} if "DeviceComissioning" in cfg and "NodeID" in cfg["DeviceComissioning"] else {"NODEID": 1}
            device = dcf.Device(cfg, env)
        except Exception as e:  # the checker's verdict
            valid = False
            device = None
            errors.append("%s: %s" % (type(e).__name__, e))
    errors = ["warning: %s" % w.message for w in caught] + errors
    if device is not None:
        for index in sorted(device):
            obj = device[index]
            for sub in sorted(obj):
                so = obj[sub]
                sub_name = "%04Xsub%X" % (index, sub)
                if sub_name in cfg:
                    raw = cfg[sub_name].get("DefaultValue")
                elif sub == 0 and "CompactSubObj" in cfg["%04X" % index]:
                    raw = None  # the entry count lely derives from CompactSubObj
                else:
                    raw = cfg["%04X" % index].get("DefaultValue")
                objects.append({
                    "index": coi.hex_index(index),
                    "subindex": sub,
                    "name": so.name,
                    "dataType": "0x%04X" % so.data_type.index,
                    "access": so.access_type.name,
                    "pdoMapping": bool(so.pdo_mapping),
                    "default": raw,
                })
    coi.emit(coi.result("eds-check", valid=valid and not any(not e.startswith("warning") for e in errors),
                        errors=errors, objects=objects))


# ── LSS master ──────────────────────────────────────────────────────────────

LSS_ERRORS = {  # coapp error -> CiA 305 error code, per step
    ("configure-node-id", "out-of-range"): 1,
    ("configure-bit-timing", "invalid-argument"): 1,
    ("store", "not-supported"): 1,
    ("store", "io-error"): 2,
}


def cmd_lss(args, caps):
    """lely-coi lss: coapp's LssMaster in the LSS phase of the master's
    CiA 302 boot-up (afterwards the start-up sends NMT reset communication
    to all nodes, LIMITATIONS.md)."""
    steps = []
    for name, arg in args.step:
        if name == "switch-global":
            steps.append("switch-global:" + arg)
        elif name == "switch-selective":
            steps.append("switch-selective:%d:%d:%d:%d" % arg)
        elif name == "configure-node-id":
            steps.append("configure-node-id:%d" % arg)
        elif name == "configure-bit-timing":
            if coi.BIT_TIMING[arg] == 0:
                raise coi.unsupported("lely's LSS master takes a bit rate in bit/s: no automatic bit rate (index 9)")
            steps.append("configure-bit-timing:%d" % (coi.BIT_TIMING[arg] * 1000))
        elif name == "activate-bit-timing":
            steps.append("activate-bit-timing:%d" % arg)
        elif name == "inquire":
            steps.append("inquire:" + arg)
        else:
            steps.append(name)
    path = master_dcf("/tmp/coi-master-%d.dcf" % os.getpid(), lss=True)
    h = Helper(["lss", args.interface, path, args.timeout_ms] + steps)
    deadline = coi.Deadline(args.timeout_ms + 3000)
    done, i = False, 0
    try:
        while not done:
            line = h.next(deadline.remaining())
            if line == "":
                if deadline.expired():
                    raise coi.timeout("LSS steps did not complete")
                continue
            if line is None:
                h.fail(None)
            f = line.split()
            if f[0] == "DONE":
                done = True
            elif f[0] == "LSSERR":
                step, err = f[1], f[2]
                if err == "timeout":
                    raise coi.lss_timeout(step)
                code = LSS_ERRORS.get((step, err))
                if code is not None:
                    raise coi.lss_error(step, code, "LSS %s: the slave rejected the request (%s)" % (step, err))
                raise coi.protocol_error("LSS %s: %s" % (step, " ".join(f[2:])), step=step)
            elif f[0] == "LSS":
                name, arg = args.step[i]
                i += 1
                if name == "switch-global":
                    coi.emit(coi.lss_event(name, mode=arg))
                elif name == "switch-selective":
                    coi.emit(coi.lss_event(name, address=coi.lss_address(*arg)))
                elif name == "configure-node-id":
                    coi.emit(coi.lss_event(name, node=arg))
                elif name == "configure-bit-timing":
                    coi.emit(coi.lss_event(name, tableIndex=arg, kbps=coi.BIT_TIMING[arg]))
                elif name == "activate-bit-timing":
                    coi.emit(coi.lss_event(name, delayMs=arg))
                elif name == "inquire":
                    if arg == "node-id":
                        coi.emit(coi.lss_event(name, what=arg, node=int(f[2])))
                    else:
                        coi.emit(coi.lss_event(name, what=arg, value=f[2]))
                elif name == "fastscan":
                    coi.emit(coi.lss_event(name, address=coi.lss_address(*(int(x, 16) for x in f[2:6]))))
                else:
                    coi.emit(coi.lss_event(name))
            elif f[0] in ("ERROR", "FATAL"):
                h.fail(line)
        # let the master's start-up go out (NMT reset communication)
        h.proc.wait(5)
    finally:
        h.stop()
    if i != len(args.step):
        raise coi.internal("lely-coi lss reported %d of %d steps" % (i, len(args.step)))
    coi.emit(coi.result("lss", steps=len(args.step)))


# ── MPDO ────────────────────────────────────────────────────────────────────

MPDO_EDS = "/fixtures/eds/mpdo-device.eds"


def cmd_mpdo_send(args, caps):
    """A lely device (fixtures/eds/mpdo-device.eds, self-starting) whose
    TPDO 1 is a DAM- or SAM-MPDO producer; lely sends the MPDOs."""
    params = {"1800sub1": "0x%08X" % args.cob_id, "1800sub2": "0xFF",
              "1A00sub0": "0xFF" if args.mode == "dam" else "0xFE",
              "1400sub1": "0x80000200"}
    if args.mode == "sam":
        # object scanner list: block size 1, the object
        params.update({"1FA0sub0": "1", "1FA0sub1": "0x%08X" % (1 << 24 | args.index << 8 | args.subindex)})
    path = device_dcf(MPDO_EDS, None, params, startup=0)
    out = run_once_lines(["mpdo-send", args.interface, args.node_id, path, args.mode, args.target,
                          "%04X" % args.index, args.subindex, args.data.hex(), args.count, args.period_ms],
                         args.count * args.period_ms + 10000)
    sent = int(out[-1].split()[1])
    coi.emit(coi.result("mpdo-send", node=args.node_id, mode=args.mode, cobId="0x%03X" % args.cob_id, sent=sent))
    if sent < args.count:
        raise coi.timeout("%d of %d MPDOs sent (the device did not become operational)" % (sent, args.count))


def run_once_lines(helper_args, timeout_ms):
    """Run lely-coi to its SENT/RESULT line; the lines seen."""
    h = Helper(helper_args)
    deadline = coi.Deadline(timeout_ms + 3000)
    seen = []
    try:
        while True:
            line = h.next(deadline.remaining())
            if line == "":
                if deadline.expired():
                    raise coi.timeout("lely-coi did not finish")
                continue
            if line is None:
                h.fail(None)
            if line.startswith("MPDOERR "):
                raise coi.protocol_error("lely: " + line[8:])
            if line.startswith(("ERROR", "FATAL")):
                h.fail(line)
            seen.append(line)
            if line.startswith(("SENT", "RESULT")):
                return seen
    finally:
        h.stop()


def cmd_mpdo_receive(args, caps):
    """A lely device (fixtures/eds/mpdo-device.eds, self-starting) whose
    RPDO 1 is a DAM- or SAM-MPDO consumer (SAM: object dispatching list
    0x1FD0); every object lely's RPDO service writes is reported."""
    params = {"1400sub1": "0x%08X" % args.cob_id, "1400sub2": "0xFF",
              "1600sub0": "0xFF" if args.mode == "dam" else "0xFE",
              "1800sub1": "0x80000180"}
    if args.mode == "sam":
        if len(args.dispatch) > 4:
            raise coi.usage("at most 4 --dispatch entries (0x1FD0 of fixtures/eds/mpdo-device.eds)")
        params["1FD0sub0"] = str(len(args.dispatch))
        for n, (prod, ridx, rsub, lidx, lsub) in enumerate(args.dispatch, 1):
            params["1FD0sub%X" % n] = "0x%016X" % (1 << 56 | lidx << 40 | lsub << 32 | ridx << 16 | rsub << 8 | prod)
    path = device_dcf(MPDO_EDS, None, params, startup=0)

    def handle(line):
        f = line.split()
        if f[0] != "WRITE":
            return None
        idx, sub = f[1].split(":")
        return coi.event("mpdo", node=args.node_id, index=coi.hex_index(int(idx, 16)), subindex=int(sub, 16),
                         data=f[2] if len(f) > 2 else "")

    stream(args, ["mpdo-receive", args.interface, args.node_id, path, args.timeout_ms + 1000], handle, "MPDO writes")


# ── CiA 302 boot-up and concise DCFs (dcfgen) ───────────────────────────────

def dcfgen(workdir, slaves, sdo, start=True, boot_time_ms=0):
    """Lely's dcfgen on a YAML configuration: master.dcf and one concise DCF
    (<name>.bin) per slave with SDO entries. slaves: [(node, dcf, mandatory)]."""
    import yaml
    cfg = {"options": {"dcf_path": workdir},
           "master": {"node_id": MASTER_NODE_ID, "start": start, "start_nodes": start, "boot_time": boot_time_ms}}
    for node, path, mandatory in slaves:
        entry = {"dcf": path, "node_id": node, "mandatory": mandatory}
        entries = [{"index": i, "sub_index": s, "value": v} for n, i, s, v in sdo if n == node]
        if entries:
            entry["sdo"] = entries
        cfg["node%d" % node] = entry
    with open(os.path.join(workdir, "network.yml"), "w") as f:
        yaml.safe_dump(cfg, f)
    p = subprocess.run([sys.executable, "-W", "ignore", "-c", "from dcfgen.cli import main; main()",
                        "-d", workdir, "-v", os.path.join(workdir, "network.yml")],
                       capture_output=True, text=True, cwd=workdir)
    if p.returncode != 0:
        raise coi.protocol_error("dcfgen: %s" % (p.stderr.strip().splitlines() or ["exit %d" % p.returncode])[-1])
    return p.stdout


def cmd_boot(args, caps):
    """The CiA 302 boot-up of lely's NMT master: dcfgen writes the master
    DCF (network list 0x1F81, expected identity 0x1F84-0x1F88, a concise DCF
    per configured slave in 0x1F22), lely-coi boots the network and reports
    each slave's 'boot slave' result (error status A-O of CiA 302-2)."""
    workdir = tempfile.mkdtemp(prefix="coi-boot-")
    try:
        # 0x1F89 (boot time): lely ends the 'boot slave' process of a
        # mandatory slave that does not answer with error status B once this
        # time has elapsed (after its SDO retries): half the time limit.
        dcfgen(workdir, args.slave, args.sdo, start=not args.no_start,
               boot_time_ms=max(500, args.timeout_ms // 2))
        nodes = [n for n, _, _ in args.slave]
        h = Helper(["boot", args.interface, os.path.join(workdir, "master.dcf"), args.timeout_ms] + nodes)
        deadline = coi.Deadline(args.timeout_ms + 3000)
        final = {}  # node -> ok of its last 'boot slave' result
        try:
            while len(final) < len(nodes):
                line = h.next(deadline.remaining())
                if line == "":
                    if deadline.expired():
                        break
                    continue
                if line is None:
                    break
                if line.startswith("FATAL"):
                    h.fail(line)
                f = line.split(" ", 4)
                if f[0] != "BOOT" or int(f[1]) not in nodes:
                    continue
                # A slave that sends another boot-up while it is booted is
                # booted again: every result is an event, the last one counts.
                node, es = int(f[1]), f[3]
                ok = es == "-"
                final[node] = ok
                coi.emit(coi.event("boot", node=node, ok=ok, errorStatus=None if ok else es,
                                   message=f[4] if len(f) > 4 else ""))
        finally:
            h.stop()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    booted = [n for n in nodes if final.get(n) is True]
    failed = [n for n in nodes if final.get(n) is False]
    missing = [n for n in nodes if n not in final]
    coi.emit(coi.result("boot", booted=booted, failed=failed, missing=missing))
    if failed:
        raise coi.protocol_error("boot slave failed for node(s) %s" % ", ".join(map(str, failed)))
    if missing:
        raise coi.timeout("no boot result for node(s) %s" % ", ".join(map(str, missing)))


def cmd_cdcf(args, caps):
    """A concise DCF (CiA 302-3) for node --node-id of a DCF: dcfgen (the
    configuration entries of --sdo) or lely-core's co_dev_write_dcf (every
    sub-object of --range with its value)."""
    workdir = tempfile.mkdtemp(prefix="coi-cdcf-")
    try:
        if args.generator == "dcfgen":
            if not args.sdo:
                raise coi.usage("cdcf --generator dcfgen needs --sdo entries (dcfgen writes no file without)")
            sdo = [(args.node_id, i, s, v) for _, i, s, v in args.sdo]
            dcfgen(workdir, [(args.node_id, os.path.abspath(args.dcf), False)], sdo)
            path = os.path.join(workdir, "node%d.bin" % args.node_id)
        else:
            path = os.path.join(workdir, "objects.bin")
            run_once(["cdcf", os.path.abspath(args.dcf), args.node_id, "%04X" % args.range[0],
                      "%04X" % args.range[1], path], 10000)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if args.output:
        try:
            with open(args.output, "wb") as f:
                f.write(data)
        except OSError as e:
            raise coi.usage("--output %s: %s" % (args.output, e))
    coi.emit(coi.result("cdcf", generator=args.generator, node=args.node_id, size=len(data), data=data.hex()))


# ── device ──────────────────────────────────────────────────────────────────

def cmd_device(args, caps):
    emcy = "-"
    if args.emcy is not None:
        code, register, data = args.emcy
        if not register & 0x01:
            raise coi.unsupported("lely sets bit 0 (generic error) of the error register with every EMCY: "
                                  "register 0x%02X cannot be sent" % register)
        emcy = "%04X:%02X:%s" % (code, register, data.hex())
    params = {"1018sub4": "0x%08X" % args.serial} if args.serial is not None else None
    dcf_path = device_dcf(args.eds, args.heartbeat_ms, params)
    dump = ",".join("%04X:%02X" % k for k in args.dump) or "-"
    h = Helper(["device", args.interface, args.node_id, dcf_path, args.timeout_ms, args.tick_ms or 0, emcy,
                args.emcy_after_ms, dump])
    stop = coi.Stop()
    dumps, ready, ended = {}, False, False
    node_id = args.node_id
    try:
        while not ended:
            if stop.requested and h.proc.poll() is None:
                h.proc.send_signal(signal.SIGTERM)
            line = h.next(0.1)
            if line == "":
                continue
            if line is None:
                break
            if line == "READY" and not ready:
                ready = True
                if args.node_id == coi.LSS_UNCONFIGURED:
                    coi.emit(coi.event("ready"))
                else:
                    coi.emit(coi.event("ready", node=args.node_id))
            elif line.startswith("NODEID "):
                nid = int(line[7:])
                if nid != node_id:  # an LSS-configured node-ID became active
                    node_id = nid
                    coi.emit(coi.event("node-id", node=nid))
            elif line.startswith("BITRATE "):
                kbps, delay = (int(x) for x in line[8:].split())
                coi.emit(coi.event("bit-rate", kbps=kbps, delayMs=delay))
            elif line.startswith("DUMP "):
                key, _, value = line[5:].partition(" ")
                idx, sub = key.split(":")
                dumps[coi.hex_key(int(idx, 16), int(sub, 16))] = None if value == "ERR" else value
            elif line == "END":
                ended = True
            elif line.startswith("FATAL"):
                h.fail(line)
    finally:
        h.stop()
    if not ready:
        raise coi.internal("lely-coi device did not start: %s" % " | ".join(h.stderr[-3:]))
    values = {coi.hex_key(i, s): dumps.get(coi.hex_key(i, s)) for i, s in args.dump}
    coi.emit(coi.result("device", node=args.node_id, values=values, nodeId=node_id))


if __name__ == "__main__":
    coi.run(ADAPTER, CAPABILITIES, VERSION, {
        "device": cmd_device,
        "sdo-upload": cmd_sdo_upload,
        "sdo-download": cmd_sdo_download,
        "nmt": cmd_nmt,
        "listen": cmd_listen,
        "pdo-receive": cmd_pdo_receive,
        "eds-check": cmd_eds_check,
        "lss": cmd_lss,
        "mpdo-send": cmd_mpdo_send,
        "mpdo-receive": cmd_mpdo_receive,
        "boot": cmd_boot,
        "cdcf": cmd_cdcf,
    })
