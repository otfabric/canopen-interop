# SPDX-License-Identifier: MIT
"""Shared command line, documents and exit codes of the canopen-interop adapters.

Implements the parts of docs/CONTAINER_CONTRACT.md that do not depend on a
stack: argument parsing, the JSON documents, error handling and exit codes.
Every CANopen operation is done by the adapter's upstream stack; nothing
here builds or parses a CANopen frame.
"""

import argparse
import json
import os
import re
import signal
import sys
import time

SCHEMA_VERSION = "1.0"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 3
EXIT_ENV = 4
EXIT_PROTOCOL = 5

ALL_OPERATIONS = [
    "device", "sdo-upload", "sdo-download", "nmt", "listen",
    "pdo-configure", "pdo-receive", "sync", "eds-check", "cia402",
]

# Operations that use a CAN interface.
BUS_OPERATIONS = set(ALL_OPERATIONS) - {"eds-check"}

NMT_COMMANDS = ["start", "stop", "pre-operational", "reset-node", "reset-communication"]
NMT_STATES = ["initialising", "pre-operational", "operational", "stopped"]

# Well-known SDO abort codes (CiA 301 7.2.4.3.17).
ABORT_TOGGLE = 0x05030000
ABORT_TIMEOUT = 0x05040000
ABORT_UNSUPPORTED_ACCESS = 0x06010000
ABORT_WRITE_ONLY = 0x06010001
ABORT_READ_ONLY = 0x06010002
ABORT_NO_OBJECT = 0x06020000
ABORT_LENGTH = 0x06070010
ABORT_LENGTH_HIGH = 0x06070012
ABORT_LENGTH_LOW = 0x06070013
ABORT_NO_SUBINDEX = 0x06090011
ABORT_VALUE_HIGH = 0x06090031
ABORT_VALUE_LOW = 0x06090032
ABORT_GENERAL = 0x08000000


class AdapterError(Exception):
    """An error that ends the operation with an error document."""

    def __init__(self, exit_code, code, message, **extra):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message
        self.extra = extra


def usage(message):
    return AdapterError(EXIT_USAGE, "usage", message)


def unsupported(message):
    return AdapterError(EXIT_USAGE, "unsupported", message)


def timeout(message):
    return AdapterError(EXIT_TIMEOUT, "timeout", message)


def internal(message):
    return AdapterError(EXIT_FAILURE, "internal", message)


def interface_error(message):
    return AdapterError(EXIT_ENV, "interface", message)


def protocol_error(message, abort_code=None):
    extra = {}
    if abort_code is not None:
        extra["abortCode"] = "0x%08X" % abort_code
    return AdapterError(EXIT_PROTOCOL, "protocol", message, **extra)


def sdo_abort(abort_code, message=""):
    return protocol_error(message or "SDO abort 0x%08X" % abort_code, abort_code)


# ── Documents ───────────────────────────────────────────────────────────────

def emit(doc):
    """Write one JSON document as one line on stdout and flush it."""
    sys.stdout.write(json.dumps(doc, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def event(kind, **fields):
    doc = {"schemaVersion": SCHEMA_VERSION, "type": kind}
    doc.update(fields)
    return doc


def result(operation, **fields):
    doc = {"schemaVersion": SCHEMA_VERSION, "type": "result", "operation": operation}
    doc.update(fields)
    return doc


def hex_index(index):
    return "0x%04X" % index


def hex_key(index, subindex):
    """The key of a value map: "0x2000:00"."""
    return "0x%04X:%02X" % (index, subindex)


def hex_code(value, digits):
    return "0x%0*X" % (digits, value)


def bootup_event(node):
    return event("bootup", node=node)


def heartbeat_event(node, state):
    return event("heartbeat", node=node, state=state)


def emcy_event(node, code, register, data):
    return event("emcy", node=node, code=hex_code(code, 4), register=hex_code(register, 2),
                 data=bytes(data).hex())


def state_name(value):
    """Name of an NMT state byte (heartbeat): CiA 301 7.2.8.3.2.1."""
    return {0x00: "initialising", 0x04: "stopped", 0x05: "operational", 0x7F: "pre-operational"}.get(
        value & 0x7F, "unknown-0x%02X" % (value & 0x7F))


# ── Interface ───────────────────────────────────────────────────────────────

def check_interface(name):
    """Fail with exit 4 when the interface is missing or down."""
    base = "/sys/class/net/" + name
    if not os.path.isdir(base):
        raise interface_error("interface %s does not exist" % name)
    try:
        with open(base + "/operstate") as f:
            state = f.read().strip()
        with open(base + "/flags") as f:
            flags = int(f.read().strip(), 16)
    except OSError as e:
        raise interface_error("interface %s: %s" % (name, e))
    if not flags & 0x1 or state == "down":
        raise interface_error("interface %s is down" % name)


# ── Values ──────────────────────────────────────────────────────────────────

def parse_index(text):
    """An object index: hex, with or without 0x."""
    try:
        v = int(text, 16)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("index %r is not hex" % text)
    if not 0 <= v <= 0xFFFF:
        raise argparse.ArgumentTypeError("index %r out of range" % text)
    return v


def parse_subindex(text):
    """A sub-index: decimal, or hex with 0x."""
    try:
        v = int(text, 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("sub-index %r is not a number" % text)
    if not 0 <= v <= 0xFF:
        raise argparse.ArgumentTypeError("sub-index %r out of range" % text)
    return v


def parse_hex_data(text):
    try:
        return bytes.fromhex(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("data %r is not hex" % text)


def parse_node(text, allow_zero=False):
    try:
        v = int(text, 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("node-ID %r is not a number" % text)
    if not (0 if allow_zero else 1) <= v <= 127:
        raise argparse.ArgumentTypeError("node-ID %d out of range" % v)
    return v


def parse_key(text):
    """IDX:SUB of --dump: index hex, sub-index hex."""
    m = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2})", text)
    if not m:
        raise argparse.ArgumentTypeError("%r: want IDX:SUB in hex, e.g. 0x2000:00" % text)
    return int(m.group(1), 16), int(m.group(2), 16)


def parse_mapping(text):
    """--map entries: IDX:SUB:BITS[,IDX:SUB:BITS...] (index and sub-index hex, bits decimal)."""
    out = []
    for part in text.split(","):
        m = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2}):(\d+)", part.strip())
        if not m:
            raise argparse.ArgumentTypeError("%r: want IDX:SUB:BITS, e.g. 0x2002:00:32" % part)
        bits = int(m.group(3))
        if not 1 <= bits <= 64:
            raise argparse.ArgumentTypeError("%r: 1..64 bits" % part)
        out.append((int(m.group(1), 16), int(m.group(2), 16), bits))
    if not 1 <= len(out) <= 8 or sum(b for _, _, b in out) > 64:
        raise argparse.ArgumentTypeError("a PDO maps 1..8 objects of at most 64 bits")
    return out


def parse_emcy(text):
    """CODE:REG:HEXDATA: code and register hex, up to 5 bytes of data."""
    m = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]{1,4}):(?:0x)?([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]*)", text)
    if not m or len(m.group(3)) % 2 or len(m.group(3)) > 10:
        raise argparse.ArgumentTypeError("%r: want CODE:REG:HEXDATA (up to 5 data bytes)" % text)
    return int(m.group(1), 16), int(m.group(2), 16), bytes.fromhex(m.group(3))


# ── Command line ────────────────────────────────────────────────────────────

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise usage(message)


def build_parser(adapter):
    p = _Parser(prog="canopen-interop", description="canopen-interop adapter " + adapter)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("print-capabilities")
    sub.add_parser("print-version")
    pf = sub.add_parser("print-fixture")
    pf.add_argument("path")

    def common(sp):
        sp.add_argument("--interface", default="vcan0")
        sp.add_argument("--timeout-ms", type=int, default=5000)

    d = sub.add_parser("device")
    common(d)
    d.add_argument("--node-id", type=parse_node, required=True)
    d.add_argument("--eds", required=True)
    d.add_argument("--heartbeat-ms", type=int)
    d.add_argument("--dump", type=parse_key, action="append", default=[])
    d.add_argument("--tick-ms", type=int)
    d.add_argument("--emcy", type=parse_emcy)
    d.add_argument("--emcy-after-ms", type=int, default=0)

    for name in ("sdo-upload", "sdo-download"):
        s = sub.add_parser(name)
        common(s)
        s.add_argument("--node-id", type=parse_node, required=True)
        s.add_argument("--index", type=parse_index, required=True)
        s.add_argument("--subindex", type=parse_subindex, default=0)
        s.add_argument("--block", action="store_true")
        if name == "sdo-download":
            s.add_argument("--data", type=parse_hex_data, required=True)

    n = sub.add_parser("nmt")
    common(n)
    n.add_argument("--node-id", type=lambda t: parse_node(t, allow_zero=True), required=True)
    n.add_argument("--command", choices=NMT_COMMANDS, required=True)

    li = sub.add_parser("listen")
    common(li)
    li.add_argument("--count", type=int, default=1)
    li.add_argument("--node-id", type=parse_node)

    pc = sub.add_parser("pdo-configure")
    common(pc)
    pc.add_argument("--node-id", type=parse_node, required=True)
    pc.add_argument("--tpdo", type=int, required=True)
    pc.add_argument("--map", type=parse_mapping, required=True)
    pc.add_argument("--transmission-type", type=int, required=True)
    pc.add_argument("--event-timer-ms", type=int, default=0)

    pr = sub.add_parser("pdo-receive")
    common(pr)
    pr.add_argument("--node-id", type=parse_node, required=True)
    pr.add_argument("--tpdo", type=int, required=True)
    pr.add_argument("--count", type=int, default=1)

    sy = sub.add_parser("sync")
    common(sy)
    sy.add_argument("--count", type=int, default=1)
    sy.add_argument("--period-ms", type=int, default=100)

    ec = sub.add_parser("eds-check")
    ec.add_argument("--eds", required=True)

    c4 = sub.add_parser("cia402")
    common(c4)
    c4.add_argument("--node-id", type=parse_node, required=True)
    c4.add_argument("--op", choices=["enable", "disable", "fault-reset"], required=True)
    return p


def check_args(cmd, args, caps):
    """Contract-level checks shared by every adapter, before the stack runs."""
    f = caps["features"]
    if cmd in BUS_OPERATIONS:
        if args.timeout_ms <= 0:
            raise usage("--timeout-ms must be positive")
    if cmd == "device":
        if not os.path.isfile(args.eds):
            raise usage("no EDS file %s" % args.eds)
        if args.heartbeat_ms is not None:
            if not 0 <= args.heartbeat_ms <= 0xFFFF:
                raise usage("--heartbeat-ms is 0..65535")
            if not f.get("heartbeatProducer"):
                raise unsupported("heartbeat producer (--heartbeat-ms)")
        if args.tick_ms is not None:
            if args.tick_ms <= 0:
                raise usage("--tick-ms must be positive")
            if not f.get("tick"):
                raise unsupported("--tick-ms (counter 0x2002:00 with event-driven TPDOs)")
        if args.emcy is not None and not f.get("emcy"):
            raise unsupported("--emcy (EMCY producer)")
        if args.emcy_after_ms < 0:
            raise usage("--emcy-after-ms must not be negative")
    if cmd in ("sdo-upload", "sdo-download") and args.block and not f.get("sdoBlock"):
        raise unsupported("SDO block transfer (client)")
    if cmd == "listen":
        if args.count < 1:
            raise usage("--count must be at least 1")
        if args.node_id is None and not f.get("listenAllNodes"):
            raise unsupported("listen without --node-id (this stack's consumers are configured per node)")
    if cmd in ("pdo-configure", "pdo-receive"):
        if not 1 <= args.tpdo <= 512:
            raise usage("--tpdo is 1..512")
    if cmd == "pdo-configure":
        if not 0 <= args.transmission_type <= 255:
            raise usage("--transmission-type is 0..255")
        if not 0 <= args.event_timer_ms <= 0xFFFF:
            raise usage("--event-timer-ms is 0..65535")
    if cmd in ("pdo-receive", "sync") and args.count < 1:
        raise usage("--count must be at least 1")
    if cmd == "sync" and args.period_ms <= 0:
        raise usage("--period-ms must be positive")
    if cmd == "eds-check" and not os.path.isfile(args.eds):
        raise usage("no EDS file %s" % args.eds)


class Deadline:
    def __init__(self, timeout_ms):
        self.end = time.monotonic() + timeout_ms / 1000.0

    def remaining(self):
        return max(0.0, self.end - time.monotonic())

    def expired(self):
        return time.monotonic() >= self.end


class Stop:
    """Set by SIGTERM/SIGINT: a device ends its run, prints its result."""

    def __init__(self):
        self.requested = False
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame):
        self.requested = True

    def wait(self, deadline, step=0.05):
        """Sleep until the deadline or a signal; True when a signal ended it."""
        while not self.requested and not deadline.expired():
            time.sleep(min(step, deadline.remaining()))
        return self.requested


def run(adapter, capabilities, version, handlers):
    """Parse the command line and run the handler; never returns."""
    caps = {"schemaVersion": SCHEMA_VERSION, "adapter": adapter}
    caps.update(capabilities)
    caps["operations"] = [op for op in ALL_OPERATIONS if op in handlers]
    try:
        argv = sys.argv[1:] or ["print-capabilities"]
        if argv[0] in ALL_OPERATIONS and argv[0] not in handlers:
            # Before the flags: an unsupported operation is reported as such,
            # not as a usage error about its required flags.
            raise unsupported("operation %s is not supported by %s" % (argv[0], adapter))
        args = build_parser(adapter).parse_args(argv)
        cmd = args.cmd
        if cmd is None:
            raise usage("no command")
        if cmd == "print-capabilities":
            emit(caps)
        elif cmd == "print-version":
            doc = {"schemaVersion": SCHEMA_VERSION, "adapter": adapter}
            doc.update(version)
            emit(doc)
        elif cmd == "print-fixture":
            path = os.path.normpath(os.path.join("/fixtures", args.path))
            if not path.startswith("/fixtures/") or not os.path.isfile(path):
                raise usage("no fixture %s" % args.path)
            with open(path, "rb") as f:
                sys.stdout.buffer.write(f.read())
            sys.stdout.flush()
        else:
            check_args(cmd, args, caps)
            if cmd in BUS_OPERATIONS:
                check_interface(args.interface)
            handlers[cmd](args, caps)
        sys.exit(EXIT_OK)
    except AdapterError as e:
        doc = {"schemaVersion": SCHEMA_VERSION, "type": "error", "code": e.code, "message": e.message}
        doc.update(e.extra)
        emit(doc)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(EXIT_FAILURE)
    except BrokenPipeError:
        sys.exit(EXIT_FAILURE)
    except Exception as e:  # an upstream stack failed in a way not foreseen
        log("%s: %s: %s" % (adapter, type(e).__name__, e))
        emit({"schemaVersion": SCHEMA_VERSION, "type": "error", "code": "internal",
              "message": "%s: %s" % (type(e).__name__, e)})
        sys.exit(EXIT_FAILURE)
