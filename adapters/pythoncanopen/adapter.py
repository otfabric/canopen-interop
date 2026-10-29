# SPDX-License-Identifier: MIT
"""canopen-interop adapter for python canopen (christiansandberg/canopen).

Every CANopen operation is done by python canopen over python-can's
socketcan interface: LocalNode (SDO server, NMT slave, heartbeat and EMCY
producer) for the device role, RemoteNode (SDO client incl. block transfer,
NMT master, heartbeat and EMCY consumers, PDO configuration and reception),
the SYNC producer and BaseNode402 for the master role, and the EDS importer
for eds-check. This file only translates the container contract to that API
and its results to JSON documents (adapters/common/coi.py).
"""

import os
import threading
import time

import can
import canopen
from canopen import objectdictionary as odm
from canopen.objectdictionary import datatypes
from canopen import lss as lssmod
from canopen.sdo import SdoAbortedError, SdoCommunicationError

import coi

ADAPTER = "pythoncanopen"
REVISION = os.environ.get("UPSTREAM_REVISION", "")

CAPABILITIES = {
    "features": {
        # SDO client (master role)
        "sdoExpedited": True,
        "sdoSegmented": True,
        "sdoBlock": True,
        # device role
        "deviceSegmented": True,
        "deviceBlock": False,          # SdoServer has no block transfer
        "heartbeatProducer": True,
        "emcy": True,
        "tick": False,                 # LocalNode has no TPDO producer
        "deviceTpdo": False,
        "deviceRpdo": False,           # LocalNode has no RPDO consumer
        "deviceLimits": False,         # LocalNode does not check LowLimit/HighLimit
        # consumers (listen)
        "listenBootup": True,
        "listenHeartbeat": True,
        "listenEmcy": True,
        "listenAllNodes": True,
        # PDO, SYNC, EDS, CiA 402
        "pdoConfigure": True,
        "pdoReceive": True,
        "sync": True,
        "edsCheck": True,
        "cia402": True,
        # contract 1.1
        "lssMaster": True,             # network.lss (LssMaster)
        "lssBitTiming": True,
        "lssFastscan": True,           # LssMaster.fast_scan
        "lssAssignAll": False,
        "lssSlave": False,             # no LSS slave in python canopen
        "lssSlaveBitTiming": False,
        "lssSlaveStore": False,
        "rpdoSend": True,              # RemoteNode.rpdo[k].transmit(), SYNC producer
        "mpdoDam": False,
        "mpdoSam": False,
        "boot": False,
        "cdcfDcfgen": False,
        "cdcfObjects": False,
        "syncWindowRpdo": False,       # LocalNode has no RPDO consumer
    },
}

VERSION = {
    "upstream": "canopen",
    "repository": "https://github.com/christiansandberg/canopen",
    "version": canopen.__version__,
    "revision": REVISION,
    "license": "MIT",
    "stack": "python canopen %s on python-can %s (socketcan)" % (canopen.__version__, can.__version__),
    "adapterVersion": os.environ.get("ADAPTER_VERSION", "dev"),
}

NMT_CODES = {"start": 0x01, "stop": 0x02, "pre-operational": 0x80,
             "reset-node": 0x81, "reset-communication": 0x82}


def connect(args):
    network = canopen.Network()
    try:
        network.connect(interface="socketcan", channel=args.interface)
    except OSError as e:
        raise coi.interface_error("%s: %s" % (args.interface, e))
    return network


def sdo_errors(fn):
    """Run an SDO operation of the stack, mapping its exceptions to the contract."""
    try:
        return fn()
    except SdoAbortedError as e:
        raise coi.sdo_abort(e.code, "SDO abort 0x%08X: %s" % (e.code, e))
    except SdoCommunicationError as e:
        if "No SDO response" in str(e) or "timeout" in str(e).lower():
            raise coi.timeout(str(e))
        raise coi.protocol_error(str(e))


def remote(network, node_id, od=None, timeout_ms=5000):
    node = canopen.RemoteNode(node_id, od or odm.ObjectDictionary())
    # One response may take up to the whole operation's time.
    node.sdo.RESPONSE_TIMEOUT = max(0.3, min(timeout_ms / 1000.0, 5.0))
    network.add_node(node)
    return node


# ── master: SDO ─────────────────────────────────────────────────────────────

def cmd_sdo_upload(args, caps):
    network = connect(args)
    try:
        node = remote(network, args.node_id, timeout_ms=args.timeout_ms)

        def upload():
            if args.block:
                with node.sdo.open(args.index, args.subindex, "rb", block_transfer=True) as f:
                    return f.read()
            return node.sdo.upload(args.index, args.subindex)

        data = sdo_errors(upload)
    finally:
        network.disconnect()
    coi.emit(coi.result("sdo-upload", node=args.node_id, index=coi.hex_index(args.index),
                        subindex=args.subindex, block=args.block, size=len(data), data=bytes(data).hex()))


def cmd_sdo_download(args, caps):
    network = connect(args)
    try:
        node = remote(network, args.node_id, timeout_ms=args.timeout_ms)

        def download():
            if args.block:
                with node.sdo.open(args.index, args.subindex, "wb", size=len(args.data),
                                   block_transfer=True) as f:
                    f.write(args.data)
            else:
                node.sdo.download(args.index, args.subindex, args.data)

        sdo_errors(download)
    finally:
        network.disconnect()
    coi.emit(coi.result("sdo-download", node=args.node_id, index=coi.hex_index(args.index),
                        subindex=args.subindex, block=args.block, size=len(args.data)))


# ── master: NMT, listen ─────────────────────────────────────────────────────

def cmd_nmt(args, caps):
    network = connect(args)
    try:
        if args.node_id == 0:
            network.nmt.send_command(NMT_CODES[args.command])
        else:
            remote(network, args.node_id).nmt.send_command(NMT_CODES[args.command])
        time.sleep(0.05)  # let python-can hand the frame to the kernel
    finally:
        network.disconnect()
    coi.emit(coi.result("nmt", node=args.node_id, command=args.command))


def cmd_listen(args, caps):
    """Boot-up, heartbeat state changes and EMCYs from RemoteNode's consumers."""
    network = connect(args)
    lock = threading.Lock()
    done = threading.Event()
    received = [0]
    last = {}

    def put(doc):
        with lock:
            if received[0] >= args.count:
                return
            received[0] += 1
            coi.emit(doc)
            if received[0] >= args.count:
                done.set()

    def on_state(node_id):
        def cb(state):
            if state == 0:
                last[node_id] = None
                put(coi.bootup_event(node_id))
                return
            name = coi.state_name(state)
            if last.get(node_id) != name:  # report state changes, as the other stacks do
                last[node_id] = name
                put(coi.heartbeat_event(node_id, name))
        return cb

    def on_emcy(node_id):
        def cb(err):
            put(coi.emcy_event(node_id, err.code, err.register, err.data))
        return cb

    try:
        ids = [args.node_id] if args.node_id else range(1, 128)
        for i in ids:
            node = remote(network, i)
            node.nmt.add_heartbeat_callback(on_state(i))
            node.emcy.add_callback(on_emcy(i))
        coi.emit(coi.event("ready"))
        done.wait(args.timeout_ms / 1000.0)
    finally:
        network.disconnect()
    complete = received[0] >= args.count
    coi.emit(coi.result("listen", received=received[0], complete=complete))
    if not complete:
        raise coi.timeout("%d of %d events before the timeout" % (received[0], args.count))


# ── master: PDO, SYNC ───────────────────────────────────────────────────────

def _var(name, index, subindex, data_type, access="rw"):
    v = odm.ODVariable(name, index, subindex)
    v.data_type = data_type
    v.access_type = access
    return v


def _bits_type(bits):
    return {8: datatypes.UNSIGNED8, 16: datatypes.UNSIGNED16, 32: datatypes.UNSIGNED32,
            64: datatypes.UNSIGNED64}.get(bits, datatypes.UNSIGNED64 if bits > 32 else datatypes.UNSIGNED32)


def pdo_od(tpdo, mapped, com_base=0x1800):
    """A master-side object dictionary with the TPDO's (com_base 0x1400:
    RPDO's) communication and mapping records and the mapped objects (the
    master is not given the device's EDS: lengths come from the mapping)."""
    od = odm.ObjectDictionary()
    com = odm.ODRecord("PDO communication parameter", com_base + tpdo - 1)
    com.add_member(_var("Highest sub-index supported", com.index, 0, datatypes.UNSIGNED8, "ro"))
    for sub, dt, name in ((1, datatypes.UNSIGNED32, "COB-ID"), (2, datatypes.UNSIGNED8, "Transmission type"),
                          (3, datatypes.UNSIGNED16, "Inhibit time"), (5, datatypes.UNSIGNED16, "Event timer"),
                          (6, datatypes.UNSIGNED8, "SYNC start value")):
        com.add_member(_var(name, com.index, sub, dt))
    od.add_object(com)
    mp = odm.ODRecord("PDO mapping parameter", com_base + 0x200 + tpdo - 1)
    mp.add_member(_var("Number of mapped objects", mp.index, 0, datatypes.UNSIGNED8))
    for sub in range(1, 9):
        mp.add_member(_var("Mapped object %d" % sub, mp.index, sub, datatypes.UNSIGNED32))
    od.add_object(mp)
    for index, subindex, bits in mapped:
        if index in od:
            continue
        if subindex == 0:
            od.add_object(_var(coi.hex_key(index, subindex), index, 0, _bits_type(bits)))
        else:
            rec = odm.ODRecord(coi.hex_index(index), index)
            rec.add_member(_var("count", index, 0, datatypes.UNSIGNED8, "ro"))
            od.add_object(rec)
    for index, subindex, bits in mapped:
        obj = od[index]
        if isinstance(obj, odm.ODRecord) and subindex not in obj.subindices:
            obj.add_member(_var(coi.hex_key(index, subindex), index, subindex, _bits_type(bits)))
    return od


def read_mapping(network, args, map_index=None):
    """The TPDO (or other PDO) mapping of the remote node, read with the stack's SDO client."""
    probe = remote(network, args.node_id, timeout_ms=args.timeout_ms)
    idx = map_index or 0x1A00 + args.tpdo - 1
    count = sdo_errors(lambda: probe.sdo.upload(idx, 0))[0]
    out = []
    for sub in range(1, count + 1):
        v = int.from_bytes(sdo_errors(lambda: probe.sdo.upload(idx, sub))[:4], "little")
        if v >> 16:
            out.append((v >> 16, (v >> 8) & 0xFF, v & 0xFF))
    del network.nodes[args.node_id]
    return out


def cmd_pdo_configure(args, caps):
    network = connect(args)
    try:
        current = read_mapping(network, args)
        node = remote(network, args.node_id, pdo_od(args.tpdo, current + args.map), args.timeout_ms)
        pdo = node.tpdo[args.tpdo]
        sdo_errors(pdo.read)
        pdo.clear()
        for index, subindex, bits in args.map:
            if pdo.add_variable(index, subindex, bits) is None:
                raise coi.internal("python canopen could not map 0x%04X:%02X" % (index, subindex))
        pdo.trans_type = args.transmission_type
        pdo.event_timer = args.event_timer_ms
        pdo.enabled = True
        sdo_errors(pdo.save)
        cob_id = pdo.cob_id
    finally:
        network.disconnect()
    coi.emit(coi.result("pdo-configure", node=args.node_id, tpdo=args.tpdo, cobId="0x%03X" % cob_id,
                        map=["0x%04X:%02X:%d" % m for m in args.map],
                        transmissionType=args.transmission_type, eventTimerMs=args.event_timer_ms))


def cmd_pdo_receive(args, caps):
    network = connect(args)
    lock = threading.Lock()
    done = threading.Event()
    received = [0]
    try:
        mapped = read_mapping(network, args)
        node = remote(network, args.node_id, pdo_od(args.tpdo, mapped), args.timeout_ms)
        pdo = node.tpdo[args.tpdo]
        sdo_errors(pdo.read)  # reads the configuration by SDO and subscribes
        if not pdo.enabled:
            raise coi.protocol_error("TPDO %d of node %d is not valid (COB-ID 0x%X)" % (
                args.tpdo, args.node_id, pdo.cob_id))

        def on_pdo(m):
            values = {coi.hex_key(v.index, v.subindex): bytes(v.get_data()).hex() for v in m.map}
            with lock:
                if received[0] >= args.count:
                    return
                received[0] += 1
                coi.emit(coi.event("pdo", node=args.node_id, pdo=args.tpdo, cobId="0x%03X" % m.cob_id,
                                   values=values))
                if received[0] >= args.count:
                    done.set()

        pdo.add_callback(on_pdo)
        coi.emit(coi.event("ready"))
        done.wait(args.timeout_ms / 1000.0)
    finally:
        network.disconnect()
    complete = received[0] >= args.count
    coi.emit(coi.result("pdo-receive", received=received[0], complete=complete))
    if not complete:
        raise coi.timeout("%d of %d PDOs before the timeout" % (received[0], args.count))


def cmd_sync(args, caps):
    network = connect(args)
    deadline = coi.Deadline(args.timeout_ms)
    sent = 0
    try:
        start = time.monotonic()
        while sent < args.count:
            if deadline.expired():
                break
            network.sync.transmit()
            sent += 1
            if sent < args.count:
                time.sleep(max(0.0, start + sent * args.period_ms / 1000.0 - time.monotonic()))
        time.sleep(0.05)
    finally:
        network.disconnect()
    coi.emit(coi.result("sync", sent=sent, periodMs=args.period_ms))
    if sent < args.count:
        raise coi.timeout("%d of %d SYNCs before the timeout" % (sent, args.count))


# ── master: RPDO sender, LSS ────────────────────────────────────────────────

def cmd_rpdo_send(args, caps):
    """RPDO K of the node, as the stack reads it by SDO (COB-ID, mapping),
    packed by python canopen with the --set values and sent once; with
    --sync between two SYNCs of the stack's SYNC producer (the second one
    makes a synchronous RPDO take effect), --delay-ms after the first."""
    network = connect(args)
    try:
        mapped = read_mapping(network, args, 0x1600 + args.rpdo - 1)
        keys = {(i, s) for i, s, _ in mapped}
        given = {(i, s) for i, s, _ in args.set}
        if keys != given:
            raise coi.usage("--set must give every object RPDO %d maps: %s" % (
                args.rpdo, ", ".join(coi.hex_key(i, s) for i, s in sorted(keys))))
        node = remote(network, args.node_id, pdo_od(args.rpdo, mapped, 0x1400), args.timeout_ms)
        pdo = node.rpdo[args.rpdo]
        sdo_errors(lambda: pdo.read())
        if not pdo.enabled:
            raise coi.protocol_error("RPDO %d of node %d is not valid (COB-ID 0x%X)" % (
                args.rpdo, args.node_id, pdo.cob_id))
        for index, subindex, data in args.set:
            var = next(v for v in pdo.map if v.index == index and v.subindex == subindex)
            if len(data) * 8 != var.length:
                raise coi.usage("%s: %d bytes for a %d-bit mapping" % (coi.hex_key(index, subindex), len(data),
                                                                      var.length))
            var.set_data(data)
        if args.sync:
            network.sync.transmit()
            time.sleep(args.delay_ms / 1000.0)
        pdo.transmit()
        if args.sync:
            time.sleep(0.02)
            network.sync.transmit()
        time.sleep(0.05)
        cob_id = pdo.cob_id
    finally:
        network.disconnect()
    coi.emit(coi.result("rpdo-send", node=args.node_id, rpdo=args.rpdo, cobId="0x%03X" % cob_id,
                        values={coi.hex_key(i, s): d.hex() for i, s, d in args.set},
                        sync=args.sync, delayMs=args.delay_ms))


INQUIRE_CS = {"vendor-id": lssmod.CS_INQUIRE_VENDOR_ID, "product-code": lssmod.CS_INQUIRE_PRODUCT_CODE,
              "revision": lssmod.CS_INQUIRE_REVISION_NUMBER, "serial": lssmod.CS_INQUIRE_SERIAL_NUMBER}


def cmd_lss(args, caps):
    """Every step is a call of python canopen's LssMaster (network.lss)."""
    network = connect(args)
    master = network.lss
    # One LSS answer comes within milliseconds on vcan; fastscan waits this
    # long for every bit no slave confirms.
    master.RESPONSE_TIMEOUT = 0.3
    try:
        for name, arg in args.step:
            try:
                if name == "switch-global":
                    master.send_switch_state_global(
                        master.CONFIGURATION_STATE if arg == "configuration" else master.WAITING_STATE)
                    coi.emit(coi.lss_event(name, mode=arg))
                elif name == "switch-selective":
                    if not master.send_switch_state_selective(*arg):
                        raise coi.protocol_error("LSS switch-selective: unexpected response", step=name)
                    coi.emit(coi.lss_event(name, address=coi.lss_address(*arg)))
                elif name == "configure-node-id":
                    master.configure_node_id(arg)
                    coi.emit(coi.lss_event(name, node=arg))
                elif name == "configure-bit-timing":
                    master.configure_bit_timing(arg)
                    coi.emit(coi.lss_event(name, tableIndex=arg, kbps=coi.BIT_TIMING[arg]))
                elif name == "activate-bit-timing":
                    master.activate_bit_timing(arg)
                    coi.emit(coi.lss_event(name, delayMs=arg))
                elif name == "store":
                    master.store_configuration()
                    coi.emit(coi.lss_event(name))
                elif name == "inquire":
                    if arg == "node-id":
                        coi.emit(coi.lss_event(name, what=arg, node=master.inquire_node_id()))
                    else:
                        coi.emit(coi.lss_event(name, what=arg,
                                               value="0x%08X" % master.inquire_lss_address(INQUIRE_CS[arg])))
                elif name == "fastscan":
                    found, address = master.fast_scan()
                    if not found:
                        raise coi.lss_timeout(name, "LSS fastscan: no unconfigured slave answered")
                    coi.emit(coi.lss_event(name, address=coi.lss_address(*address)))
            except lssmod.LssError as e:
                text = str(e)
                if text.startswith("LSS Error: "):
                    raise coi.lss_error(name, int(text[11:]), "LSS %s: %s" % (name, text))
                if "No LSS response" in text:
                    raise coi.lss_timeout(name, "LSS %s: %s" % (name, text))
                raise coi.protocol_error("LSS %s: %s" % (name, text), step=name)
        time.sleep(0.05)  # let python-can hand the last frame to the kernel
    finally:
        network.disconnect()
    coi.emit(coi.result("lss", steps=len(args.step)))


# ── master: CiA 402 ─────────────────────────────────────────────────────────

def cmd_cia402(args, caps):
    """BaseNode402 state machine by SDO (controlword 0x6040, statusword 0x6041)."""
    od = odm.ObjectDictionary()
    od.add_object(_var("Controlword", 0x6040, 0, datatypes.UNSIGNED16))
    od.add_object(_var("Statusword", 0x6041, 0, datatypes.UNSIGNED16, "ro"))
    od.add_object(_var("Modes of operation", 0x6060, 0, datatypes.INTEGER8))
    od.add_object(_var("Modes of operation display", 0x6061, 0, datatypes.INTEGER8, "ro"))
    network = connect(args)
    try:
        node = canopen.BaseNode402(args.node_id, od)
        node.sdo.RESPONSE_TIMEOUT = max(0.3, min(args.timeout_ms / 1000.0, 5.0))
        network.add_node(node)
        sdo_errors(lambda: node.setup_402_state_machine(read_pdos=False))
        first = sdo_errors(lambda: node.state)
        coi.emit(coi.event("cia402-state", node=args.node_id, state=first))
        target = {"enable": "OPERATION ENABLED", "disable": "SWITCH ON DISABLED"}.get(args.op)
        try:
            if args.op == "fault-reset":
                sdo_errors(node.reset_from_fault)
            else:
                node.TIMEOUT_SWITCH_STATE_FINAL = args.timeout_ms / 1000.0
                sdo_errors(lambda: setattr(node, "state", target))
        except RuntimeError as e:
            raise coi.timeout(str(e))
        except ValueError as e:
            raise coi.protocol_error(str(e))
        final = sdo_errors(lambda: node.state)
        if final != first:
            coi.emit(coi.event("cia402-state", node=args.node_id, state=final))
    finally:
        network.disconnect()
    coi.emit(coi.result("cia402", node=args.node_id, op=args.op, state=final))


# ── EDS ─────────────────────────────────────────────────────────────────────

ACCESS = {"ro", "wo", "rw", "rwr", "rww", "const"}


def _entry(var, subindex):
    access = (var.access_type or "").lower()
    return {
        "index": coi.hex_index(var.index),
        "subindex": subindex,
        "name": var.name,
        "dataType": "0x%04X" % var.data_type if var.data_type is not None else None,
        "access": access if access in ACCESS else access or None,
        "pdoMapping": bool(var.pdo_mappable),
        "default": getattr(var, "default_raw", None),
    }


def cmd_eds_check(args, caps):
    errors, objects = [], []
    try:
        od = canopen.import_od(args.eds, None)
    except Exception as e:  # the importer's verdict
        errors.append("%s: %s" % (type(e).__name__, e))
        od = None
    if od is not None:
        for index in sorted(od.indices):
            obj = od[index]
            if isinstance(obj, odm.ODVariable):
                objects.append(_entry(obj, 0))
                continue
            subs = set(obj.subindices)
            if isinstance(obj, odm.ODArray) and 0 in obj.subindices:
                n = obj[0].default
                if isinstance(n, int):
                    subs.update(range(1, n + 1))
            for sub in sorted(subs):
                objects.append(_entry(obj[sub], sub))
    coi.emit(coi.result("eds-check", valid=not errors, errors=errors, objects=objects))


# ── device ──────────────────────────────────────────────────────────────────

def cmd_device(args, caps):
    """A LocalNode from the EDS. python canopen leaves booting to the
    application: this adapter (the application) sends the boot-up through
    NmtSlave (INITIALISING, then PRE-OPERATIONAL, which starts the heartbeat)
    at start and after an NMT reset command, and restores the EDS values on
    reset-node."""
    stop = coi.Stop()
    try:
        od = canopen.import_od(args.eds, args.node_id)
    except Exception as e:
        raise coi.usage("EDS %s: %s: %s" % (args.eds, type(e).__name__, e))
    network = connect(args)
    node = canopen.LocalNode(args.node_id, od)
    network.add_node(node)
    lock = threading.Lock()

    def boot(reset_node):
        with lock:
            node.nmt.stop_heartbeat()
            if reset_node:
                node.data_store.clear()
            node.nmt.send_command(0x81)   # INITIALISING: NmtSlave sends the boot-up message
            node.nmt.send_command(0x80)   # PRE-OPERATIONAL: starts the heartbeat (0x1017)
            if args.heartbeat_ms is not None and (reset_node or not booted[0]):
                node.sdo[0x1017].raw = args.heartbeat_ms
            booted[0] = True

    booted = [False]

    def on_nmt(can_id, data, timestamp):
        # Runs after NmtSlave.on_command (subscribed first) on the receive
        # thread: the reset completes (boot-up sent) before the next NMT
        # command is processed, so an NMT start right after a reset (CiA 302
        # boot-up of a master) is not undone by a late boot.
        if len(data) >= 2 and data[1] in (0, args.node_id) and data[0] in (0x81, 0x82):
            boot(reset_node=data[0] == 0x81)

    network.subscribe(0, on_nmt)
    try:
        boot(reset_node=False)
        coi.emit(coi.event("ready", node=args.node_id))
        deadline = coi.Deadline(args.timeout_ms)
        emcy_at = time.monotonic() + args.emcy_after_ms / 1000.0 if args.emcy else None
        while not stop.requested and not deadline.expired():
            if emcy_at is not None and time.monotonic() >= emcy_at:
                code, register, data = args.emcy
                node.emcy.send(code, register, data)
                emcy_at = None
            time.sleep(0.01)
        node.nmt.stop_heartbeat()
        values = {}
        for index, subindex in args.dump:
            try:
                values[coi.hex_key(index, subindex)] = bytes(node.get_data(index, subindex)).hex()
            except SdoAbortedError:
                values[coi.hex_key(index, subindex)] = None
    finally:
        network.disconnect()
    coi.emit(coi.result("device", node=args.node_id, values=values, nodeId=args.node_id))


if __name__ == "__main__":
    coi.run(ADAPTER, CAPABILITIES, VERSION, {
        "device": cmd_device,
        "sdo-upload": cmd_sdo_upload,
        "sdo-download": cmd_sdo_download,
        "nmt": cmd_nmt,
        "listen": cmd_listen,
        "pdo-configure": cmd_pdo_configure,
        "pdo-receive": cmd_pdo_receive,
        "sync": cmd_sync,
        "eds-check": cmd_eds_check,
        "cia402": cmd_cia402,
        "lss": cmd_lss,
        "rpdo-send": cmd_rpdo_send,
    })
