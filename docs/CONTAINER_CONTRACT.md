# Container contract

Version: **1.0** (`contract_version` in [versions.yaml](../versions.yaml))

Every adapter image exposes the same commands, flags, JSON documents and exit
codes. Consumers (go-canopen's interop suite, this repository's self-test)
depend on this document and on [`schemas/`](../schemas), never on an
adapter's source. A breaking change bumps the major version, an addition the
minor one. Every JSON document carries `"schemaVersion": "1.0"`: it names the
compatible line and changes only with the major version.

| Version | Change |
|---------|--------|
| 1.0 | First release (canopen-interop v0.1.0) |

## Image layout

| Item | Value |
|------|-------|
| Image | `ghcr.io/otfabric/canopen-interop-<adapter>` (`canopennode`, `lely`, `pythoncanopen`) |
| Entrypoint | `canopen-interop` |
| Default command | `print-capabilities` |
| Network | `--network host`: CAN interfaces are network devices of a network namespace, and ordinary Docker networks do not carry CAN. The harness creates `vcan` interfaces in the host namespace and runs every container in it. CAN raw sockets need no capability. |
| Fixtures | `/fixtures` (a copy of [`fixtures/`](../fixtures)); the shared test device is `/fixtures/eds/interop-device.eds` |
| Files | Exchanged through a mounted directory; the self-test mounts `/work` |
| User | Unprivileged (uid 10001), working directory `/tmp` |

## Commands

| Command | Purpose |
|---------|---------|
| `print-capabilities` | Capability document: operations and features |
| `print-version` | Version document: upstream, version, revision, licence |
| `print-fixture <path>` | Print a bundled fixture (relative to `/fixtures`) to stdout |
| [`device`](#device) | Run a CANopen device (SDO server, NMT slave, heartbeat/EMCY/PDO producer) |
| [`sdo-upload`](#sdo-upload--sdo-download) | Read an object of a node (SDO client) |
| [`sdo-download`](#sdo-upload--sdo-download) | Write an object of a node (SDO client) |
| [`nmt`](#nmt) | Send an NMT command (NMT master) |
| [`listen`](#listen) | Report boot-up, heartbeat and EMCY through the stack's consumers |
| [`pdo-configure`](#pdo-configure) | Configure a TPDO of a node by SDO |
| [`pdo-receive`](#pdo-receive) | Receive and unpack a node's TPDO |
| [`sync`](#sync) | Produce SYNC messages |
| [`eds-check`](#eds-check) | Check an EDS/DCF and print the stack's view of its objects |
| [`cia402`](#cia402) | Drive the CiA 402 state machine of a node |

An operation that is not listed in `operations` of `print-capabilities`, or
a flag that needs a feature the image declares `false`, ends with exit code
2 and an error document with code `unsupported`. The CANopen work of every
operation is done by the upstream stack; an adapter never builds or parses a
CANopen frame itself.

### Common flags

| Flag | Meaning |
|------|---------|
| `--interface <name>` | CAN interface (default `vcan0`); missing or down: exit 4 |
| `--timeout-ms <n>` | Overall time limit of the operation (default 5000); for `device` the run time |

Numbers: `--index` is hexadecimal with or without `0x` (`0x2000`, `2000`),
`--subindex` decimal or `0x`-hex, `--node-id` decimal 1..127 (`nmt`: 0 = all
nodes). `IDX:SUB` pairs (`--dump`, `--map`) are hexadecimal (`0x2002:00`).

### Output

One JSON document per line on stdout (JSON Lines). Diagnostics go to stderr
only. Data are lower-case hex strings without `0x` (`"data":"91010f00"`),
in the CANopen encoding (little-endian, strings without terminator).
Indices, error codes and registers are `0x` strings with upper-case digits:
`"index":"0x2000"`, `"code":"0x8130"`, `"register":"0x11"`,
`"abortCode":"0x06020000"`; value maps are keyed `"0x2000:00"`.

## `device`

```
device --node-id N --eds <path> [--heartbeat-ms N] [--dump IDX:SUB ...]
       [--tick-ms N] [--emcy CODE:REG:HEXDATA [--emcy-after-ms N]]
```

Runs a CANopen device with the object dictionary of the EDS until
`--timeout-ms` or SIGTERM (`docker stop`). The device boots like a CiA 301
device: boot-up message, then **pre-operational** until an NMT start
command (every adapter; see [LIMITATIONS.md](../LIMITATIONS.md) for how each
stack is made to do so).

1. `{"type":"ready","node":N}` once the device runs (boot-up sent).
2. On exit: `{"type":"result","operation":"device","node":N,"values":{"0x2000:00":"5a",...}}`
   with one entry per `--dump`: the value as the device's object dictionary
   holds it (CANopen encoding), or `null` when the device cannot read it. A
   client's SDO downloads and RPDO writes can be verified this way.

| Flag | Meaning | Feature |
|------|---------|---------|
| `--heartbeat-ms N` | Producer heartbeat time (0x1017) at power-on; kept across NMT reset node | `heartbeatProducer` |
| `--dump IDX:SUB` | Print this sub-object in the result (repeatable) | |
| `--tick-ms N` | Increment the UNSIGNED32 counter 0x2002:00 every N ms, so event-driven TPDOs mapping it carry changing data | `tick` |
| `--emcy CODE:REG:HEXDATA` | Produce this EMCY (error code, error register, up to 5 manufacturer bytes) once, `--emcy-after-ms` after start (default 0) | `emcy` |

A stack may restrict `--emcy` to what it can send and ends any other value
with exit 2: CANopenNode derives the error register from an error status bit
(`HEXDATA` must be 5 bytes: the bit, then the 32-bit info code), lely always
sets bit 0 of the register. The vector `1000:01:2b01020304` is valid for all
three. The canopennode device is built with the object dictionary of
`fixtures/eds/interop-device.eds` and refuses another EDS (exit 2).

## `sdo-upload` / `sdo-download`

```
sdo-upload   --node-id N --index 0x2000 [--subindex 0] [--block]
sdo-download --node-id N --index 0x2000 [--subindex 0] --data <hex> [--block]
```

The stack's SDO client transfers the data: expedited for up to 4 bytes,
segmented above (the server's choice on upload), SDO block transfer with
`--block` (feature `sdoBlock`). Results:

```json
{"schemaVersion":"1.0","type":"result","operation":"sdo-upload","node":5,"index":"0x2000","subindex":0,"block":false,"size":1,"data":"11"}
{"schemaVersion":"1.0","type":"result","operation":"sdo-download","node":5,"index":"0x2000","subindex":0,"block":false,"size":1}
```

An SDO abort (from the server, or the client's own abort) ends with exit 5
and `{"type":"error","code":"protocol","abortCode":"0x06020000",...}`; no
response ends with exit 3 (`timeout`; adapters whose client reports it as
abort 0x05040000 include that code).

## `nmt`

`nmt --node-id N --command start|stop|pre-operational|reset-node|reset-communication`
sends the NMT command (node 0: all nodes). Result
`{"type":"result","operation":"nmt","node":N,"command":"start"}`. The lely
master can only command as a booted CiA 302 NMT master, which first sends
NMT reset communication to all nodes ([LIMITATIONS.md](../LIMITATIONS.md)).

## `listen`

`listen --count N [--node-id N]` prints `{"type":"ready"}` when the stack's
consumers run, then one line per event, until N events or `--timeout-ms`:

```json
{"schemaVersion":"1.0","type":"bootup","node":5}
{"schemaVersion":"1.0","type":"heartbeat","node":5,"state":"pre-operational"}
{"schemaVersion":"1.0","type":"emcy","node":5,"code":"0x1000","register":"0x01","data":"2b01020304"}
```

`state` is `initialising`, `pre-operational`, `operational` or `stopped`.
Heartbeat events report **state changes** as the stack's heartbeat consumer
sees them (CANopenNode and lely report only changes; the python canopen
adapter reports a heartbeat when the state differs from the node's last one,
and a boot-up resets that). Then `{"type":"result","operation":"listen","received":n,"complete":bool}`;
`complete` false (timeout) ends with exit 3. Without `--node-id` all nodes
are monitored (feature `listenAllNodes`).

## `pdo-configure`

`pdo-configure --node-id N --tpdo K --map 0x2002:00:32[,IDX:SUB:BITS...] --transmission-type T [--event-timer-ms N]`
configures TPDO K of the node by SDO with the stack's PDO configuration
procedure (disable, mapping, communication parameters, enable). Result
`{"type":"result","operation":"pdo-configure","node":5,"tpdo":2,"cobId":"0x285","map":["0x2002:00:32"],"transmissionType":255,"eventTimerMs":100}`.

## `pdo-receive`

`pdo-receive --node-id N --tpdo K --count M` reads the TPDO's COB-ID and
mapping from the node by SDO, subscribes, prints `{"type":"ready"}`, then
per PDO `{"type":"pdo","node":5,"pdo":1,"cobId":"0x185","values":{"0x2002:00":"07000000"}}`
(values unpacked by the stack), then a result like `listen`'s. The node must
be operational to transmit.

## `sync`

`sync --count N --period-ms P`: the stack's SYNC producer sends N SYNC
messages (COB-ID 0x80, no counter), P ms apart. Result
`{"type":"result","operation":"sync","sent":N,"periodMs":P}`.

## `eds-check`

`eds-check --eds <path>`: the stack's EDS/DCF parser (and checker):

```json
{"type":"result","operation":"eds-check","valid":true,"errors":[],
 "objects":[{"index":"0x1000","subindex":0,"name":"Device type","dataType":"0x0007",
             "access":"ro","pdoMapping":false,"default":"0x000F0191"}, ...]}
```

A normalized view of the parse: one entry per sub-object the stack creates
(`access` `ro|wo|rw|rwr|rww|const` as the file spells it, `default` the raw
`DefaultValue` string of the file, `null` where the stack has none). `valid`
is the stack's verdict; `errors` its messages (lely: dcfchk warnings,
prefixed `warning:`). An unreadable file is still exit 0 with
`"valid":false`; exit 2 only when the file does not exist.

## `cia402`

`cia402 --node-id N --op enable|disable|fault-reset` (pythoncanopen,
`BaseNode402`): drives the CiA 402 state machine through controlword 0x6040
and statusword 0x6041 by SDO; prints `{"type":"cia402-state","node":N,"state":"SWITCH ON DISABLED"}`
for the state before and after, and a result with the final state. A node
without 0x6040/0x6041 ends with exit 5 (SDO abort).

## Capability document

```json
{"schemaVersion":"1.0","adapter":"lely","operations":["device","sdo-upload",...],
 "features":{"sdoExpedited":true,"sdoSegmented":true,"sdoBlock":true,...}}
```

| Feature | Meaning |
|---------|---------|
| `sdoExpedited`, `sdoSegmented`, `sdoBlock` | SDO client transfers (`sdo-upload`, `sdo-download`, `--block`) |
| `deviceSegmented`, `deviceBlock` | SDO server of `device`: segmented, block transfer |
| `heartbeatProducer` | `device --heartbeat-ms` |
| `emcy` | `device --emcy` (possibly restricted, see above) |
| `tick` | `device --tick-ms` |
| `deviceTpdo`, `deviceRpdo` | the device transmits TPDOs / applies RPDOs as configured in its OD |
| `deviceLimits` | the device's SDO server checks LowLimit/HighLimit (abort 0x06090031/32) |
| `listenBootup`, `listenHeartbeat`, `listenEmcy`, `listenAllNodes` | `listen` events; `listen` without `--node-id` |
| `pdoConfigure`, `pdoReceive`, `sync`, `edsCheck`, `cia402` | the operations of the same name |

[docs/CAPABILITIES.md](CAPABILITIES.md) has the matrix. Every document is
described by a JSON Schema in [`schemas/`](../schemas): `capabilities`,
`version`, `event` (every line of an operation's output), `result`, `error`.

## Exit codes

| Code | Meaning | Error `code` |
|------|---------|--------------|
| 0 | Success | |
| 1 | Internal error (the stack or the adapter failed unexpectedly) | `internal` |
| 2 | Usage error, or unsupported operation/feature | `usage`, `unsupported` |
| 3 | Timeout, or incomplete (`listen`, `pdo-receive`, `sync`) | `timeout` |
| 4 | Environment: interface missing or down | `interface` |
| 5 | Protocol failure: SDO abort (`abortCode`), invalid PDO, NMT failure | `protocol` |

On failure the last stdout line is an error document
`{"type":"error","code":"...","message":"...","abortCode":"0x........"}`
(`abortCode` only for SDO aborts).
