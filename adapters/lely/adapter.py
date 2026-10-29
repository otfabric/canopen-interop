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
import signal
import subprocess
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


def master_dcf(path, hb_nodes=(), emcy_nodes=()):
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
            "NrOfTXPDO=0", "LSS_Supported=0", "",
            "[DeviceComissioning]", "NodeID=%d" % MASTER_NODE_ID, "CANopenManager=1", "",
            "[DummyUsage]"] + ["Dummy%04X=0" % i for i in range(1, 8)] + [""]
    for title, group in (("MandatoryObjects", mandatory), ("OptionalObjects", optional),
                         ("ManufacturerObjects", manuf)):
        head += ["[%s]" % title, "SupportedObjects=%d" % len(group)]
        head += ["%d=0x%04X" % (n, i) for n, i in enumerate(group, 1)] + [""]
    with open(path, "w") as f:
        f.write("\n".join(head + objs) + "\n")
    return path


def device_dcf(eds, heartbeat_ms):
    """The device's EDS as lely-coi loads it: with 0x1F80 (NMT start-up) =
    0x00000004, so that the device waits in pre-operational for NMT start
    like the other devices (a lely slave without 0x1F80 starts itself), and
    with ParameterValue of 0x1017 set to the power-on heartbeat time (lely
    restores it on NMT reset)."""
    with open(eds, "r", newline="") as f:
        lines = f.read().splitlines()
    out, section, has_1f80 = [], None, False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].upper()
            has_1f80 = has_1f80 or section == "1F80"
            out.append(line)
            if section == "1017" and heartbeat_ms is not None:
                out.append("ParameterValue=%d" % heartbeat_ms)
            continue
        if section == "1017" and heartbeat_ms is not None and s.lower().startswith("parametervalue="):
            continue
        out.append(line)
    if heartbeat_ms is not None and not any(l.strip().upper() == "[1017]" for l in lines):
        raise coi.usage("the EDS has no object 0x1017 for --heartbeat-ms")
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
                       "AccessType=rw", "DefaultValue=0x00000004", "PDOMapping=0"]
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


# ── device ──────────────────────────────────────────────────────────────────

def cmd_device(args, caps):
    emcy = "-"
    if args.emcy is not None:
        code, register, data = args.emcy
        if not register & 0x01:
            raise coi.unsupported("lely sets bit 0 (generic error) of the error register with every EMCY: "
                                  "register 0x%02X cannot be sent" % register)
        emcy = "%04X:%02X:%s" % (code, register, data.hex())
    dcf_path = device_dcf(args.eds, args.heartbeat_ms)
    dump = ",".join("%04X:%02X" % k for k in args.dump) or "-"
    h = Helper(["device", args.interface, args.node_id, dcf_path, args.timeout_ms, args.tick_ms or 0, emcy,
                args.emcy_after_ms, dump])
    stop = coi.Stop()
    dumps, ready, ended = {}, False, False
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
                coi.emit(coi.event("ready", node=args.node_id))
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
    coi.emit(coi.result("device", node=args.node_id, values=values))


if __name__ == "__main__":
    coi.run(ADAPTER, CAPABILITIES, VERSION, {
        "device": cmd_device,
        "sdo-upload": cmd_sdo_upload,
        "sdo-download": cmd_sdo_download,
        "nmt": cmd_nmt,
        "listen": cmd_listen,
        "pdo-receive": cmd_pdo_receive,
        "eds-check": cmd_eds_check,
    })
