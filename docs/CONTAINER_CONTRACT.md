# Container contract

Version: **1.1** (`contract_version` in [versions.yaml](../versions.yaml))

Every adapter image exposes the same commands, flags, JSON documents and exit
codes. Consumers (go-canopen's interop suite, this repository's self-test)
depend on this document and on [`schemas/`](../schemas), never on an
adapter's source. A breaking change bumps the major version, an addition the
minor one. Every JSON document carries `"schemaVersion": "1.0"`: it names the
compatible line and changes only with the major version.

| Version | Change |
|---------|--------|
| 1.0 | First release (canopen-interop v0.1.0) |
| 1.1 | Additions (canopen-interop v0.2.0): [`lss`](#lss) (LSS master, CiA 305), LSS slave devices (`device --node-id 255 --serial`, events `node-id` and `bit-rate`, `nodeId` in the device result), [`rpdo-send`](#rpdo-send) (RPDO sender, SYNC window), [`mpdo-send`/`mpdo-receive`](#mpdo-send--mpdo-receive) (DAM/SAM-MPDO), [`boot`](#boot) (CiA 302 boot-up with concise DCFs), [`cdcf`](#cdcf) (concise DCF generators); error fields `step` and `lssError`; 14 features. Documents still carry `"schemaVersion": "1.0"`. |

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
| [`lss`](#lss) | Run LSS master steps (CiA 305) — 1.1 |
| [`rpdo-send`](#rpdo-send) | Send a node's RPDO, optionally between two SYNCs — 1.1 |
| [`mpdo-send`](#mpdo-send--mpdo-receive) | Run a node that sends DAM- or SAM-MPDOs — 1.1 |
| [`mpdo-receive`](#mpdo-send--mpdo-receive) | Run a node that receives DAM- or SAM-MPDOs — 1.1 |
| [`boot`](#boot) | Boot a network as CiA 302 NMT master with concise DCFs — 1.1 |
| [`cdcf`](#cdcf) | Generate a concise DCF (CiA 302-3) — 1.1 |

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
nodes; `device`: 255 = unconfigured LSS slave). `IDX:SUB` pairs (`--dump`,
`--map`, `--set`, `--sdo`) are hexadecimal (`0x2002:00`).

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

Contract 1.1 adds the LSS slave (feature `lssSlave`):

| Flag | Meaning | Feature |
|------|---------|---------|
| `--node-id 255` | Start with an unconfigured node-ID (CiA 305): no boot-up, no SDO server, only LSS until an LSS master configures a node-ID | `lssSlave` |
| `--serial N` | Serial number 0x1018:04 at power-on (the last part of the LSS address; vendor-ID 0, product code 0x434F4931, revision 0x00010000 come from the EDS) | `lssSlave` |

`ready` of an unconfigured device has no `node`. Further events:
`{"type":"node-id","node":40}` when the device's active node-ID changes (an
LSS-configured node-ID became active at an NMT reset communication, or by
the stack itself when an unconfigured slave switches to LSS waiting), and
`{"type":"bit-rate","kbps":250,"delayMs":50}` when the stack's LSS slave is
asked to activate a bit rate (`lssSlaveBitTiming`: vcan has no bit rate,
the device only reports it). The result carries `"nodeId"`, the active
node-ID at the end (255: never configured). LSS "store configuration"
(`lssSlaveStore`) keeps node-ID and bit rate in RAM for the device's run;
an NMT reset node restarts the device with its power-on node-ID.

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

## `lss`

```
lss --step STEP [--step STEP ...]
```

The stack's LSS master (CiA 305) runs the steps in order, in one run (the
LSS state of the slaves lives on the bus, not in the container). Steps:

| Step | Service | Feature | Event fields |
|------|---------|---------|--------------|
| `switch-global:waiting\|configuration` | Switch state global | `lssMaster` | `mode` |
| `switch-selective:VENDOR:PRODUCT:REVISION:SERIAL` | Switch state selective (numbers decimal or `0x`) | `lssMaster` | `address` |
| `configure-node-id:N` | Configure node-ID (1..127, 255) | `lssMaster` | `node` |
| `configure-bit-timing:INDEX` | Configure bit timing, CiA 301 table 0 (0 = 1000 kbit/s … 8 = 10 kbit/s, 9 = automatic) | `lssBitTiming` | `tableIndex`, `kbps` |
| `activate-bit-timing:DELAY_MS` | Activate bit timing parameters | `lssBitTiming` | `delayMs` |
| `store` | Store configuration | `lssMaster` | |
| `inquire:vendor-id\|product-code\|revision\|serial\|node-id` | Inquire identity / node-ID | `lssMaster` | `what`, `value` (`0x%08X`) or `node` |
| `fastscan` | LSS fastscan: identify one non-configured slave, which is then in configuration state | `lssFastscan` | `address` |
| `assign-all:FIRST[:store]` | Find every non-configured slave (fastscan) and give it the next node-ID from FIRST, optionally stored | `lssAssignAll` | `assigned`: `[{"node":N,"address":{...}}]` |

One event per completed step, then the result:

```json
{"schemaVersion":"1.0","type":"lss","step":"inquire","what":"serial","value":"0x00000007"}
{"schemaVersion":"1.0","type":"lss","step":"fastscan","address":{"vendorId":"0x00000000","productCode":"0x434F4931","revision":"0x00010000","serial":"0x00000007"}}
{"schemaVersion":"1.0","type":"result","operation":"lss","steps":2}
```

A step no slave answers ends with exit 3 and an error document with
`"step"`; a slave's rejection (configure node-ID, bit timing, store) with
exit 5, `"step"` and, where the stack reports it, `"lssError"` (the error
code of CiA 305: 1 not supported / out of range, 2 storage media error).
The stacks' masters differ in what they accept (LIMITATIONS.md):
CANopenNode configures (node-ID, bit timing, store) only a slave selected by
`switch-selective` or `fastscan` and activates bit timing only after
`switch-global:configuration`; lely runs its master inside the CiA 302
boot-up and sends NMT reset communication to all nodes after the steps.

## `rpdo-send`

```
rpdo-send --node-id N --rpdo K --set IDX:SUB=HEX [--set ...] [--sync [--delay-ms D]]
```

The stack reads RPDO K's COB-ID and mapping from the node by SDO, packs the
`--set` values (one per mapped object, in the CANopen encoding) and sends
the PDO once (feature `rpdoSend`). With `--sync` its SYNC producer sends a
SYNC, the RPDO follows D ms later, and a second SYNC 20 ms after that (a
synchronous RPDO takes effect at the SYNC after its reception; with a SYNC
window 0x1007 the device may discard an RPDO received after the window,
feature `syncWindowRpdo` of the device). Result
`{"type":"result","operation":"rpdo-send","node":5,"rpdo":1,"cobId":"0x205","values":{"0x2001:00":"efbe",...},"sync":true,"delayMs":5}`.

## `mpdo-send` / `mpdo-receive`

```
mpdo-send    --node-id P --mode dam|sam --cob-id 0x300 --index I [--subindex S] --data HEX
             [--target N] [--count C] [--period-ms T]
mpdo-receive --node-id C --mode dam|sam --cob-id 0x300 --count N
             [--dispatch PRODUCER:RIDX:RSUB:LIDX:LSUB ...]
```

Multiplexed PDOs (CiA 301 7.2.2.3), features `mpdoDam`, `mpdoSam`. Both run
a device of the stack with node-ID P or C (the test device extended by an
object scanner list 0x1FA0 and an object dispatching list 0x1FD0,
`fixtures/eds/mpdo-device.eds`) that starts itself into operational:

* `mpdo-send`: Transmit-PDO 1 with the COB-ID is the MPDO producer. DAM
  (destination address mode): C MPDOs to node `--target` (0 = all nodes)
  writing `--data` (1..4 bytes) into the consumer's object I:S. SAM (source
  address mode): the device's own object I:S takes `--data` (the size of its
  data type) and is sent with the producer's node-ID; the stack lists it in
  the scanner list. Result `{"operation":"mpdo-send","node":11,"mode":"dam","cobId":"0x300","sent":1}`.
* `mpdo-receive`: Receive-PDO 1 with the COB-ID is the MPDO consumer. DAM:
  MPDOs addressed to C or to all nodes are written into C's dictionary.
  SAM: the `--dispatch` entries (object dispatching list, block size 1)
  map the object RIDX:RSUB of producer node PRODUCER to the local object
  LIDX:LSUB. After `{"type":"ready"}` every object the stack's RPDO service
  writes is an event `{"type":"mpdo","node":10,"index":"0x2001","subindex":0,"data":"efbe"}`
  (the value as the dictionary holds it), then a result like `listen`'s.

## `boot`

```
boot --slave N:DCF[:mandatory] [--slave ...] [--sdo N:IDX:SUB=VALUE ...] [--no-start]
```

The stack's NMT master boots the network as CiA 302-2 describes (feature
`boot`): the network list (0x1F81) holds every `--slave`, the expected
device type and identity (0x1F84–0x1F88) come from each slave's DCF/EDS,
and every slave with `--sdo` entries gets a concise DCF (0x1F22) with those
values (VALUE an integer, encoded with the object's data type), which the
master downloads during the 'boot slave' process; then the master starts
the nodes (`--no-start`: it does not). One event per 'boot slave' result:

```json
{"schemaVersion":"1.0","type":"boot","node":21,"ok":true,"errorStatus":null,"message":""}
{"schemaVersion":"1.0","type":"boot","node":24,"ok":false,"errorStatus":"M","message":"Value of object 1018 sub-index 02 from CANopen device is different to value in object 1F86 (Product code)."}
{"schemaVersion":"1.0","type":"result","operation":"boot","booted":[21],"failed":[24],"missing":[]}
```

`errorStatus` is the error status A–O of CiA 302-2. A slave that boots again
(a new boot-up message) has a further event; the last one counts. A failed
slave ends with exit 5, a slave without a result by `--timeout-ms` with
exit 3 (`missing`). The DCF paths are inside the container (`/fixtures/...`
or `/work/...`).

## `cdcf`

```
cdcf --dcf PATH --node-id N [--generator dcfgen|objects] [--sdo IDX:SUB=VALUE ...]
     [--range MIN-MAX] [--output PATH]
```

A concise DCF (CiA 302-3: UNSIGNED32 entry count, then per entry index
UNSIGNED16, sub-index UNSIGNED8, size UNSIGNED32 and the data), generated by
the stack for node N of the DCF/EDS; no CAN interface is used:

* `dcfgen` (feature `cdcfDcfgen`): the configuration entries of `--sdo`
  (VALUE an integer), in the order given, each with the size of the object's
  data type, as the stack's configuration tool writes them for a master.
* `objects` (feature `cdcfObjects`): every sub-object of the index range
  (default `0x1000-0x9FFF`) in index order with its value (ParameterValue,
  else DefaultValue, `$NODEID` evaluated with N), as the stack stores a
  dictionary.

Result `{"type":"result","operation":"cdcf","generator":"dcfgen","node":5,"size":44,"data":"04000000..."}`
(`data` the whole file); `--output` also writes the file (e.g. under `/work`).

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
| `lssMaster`, `lssBitTiming`, `lssFastscan`, `lssAssignAll` | `lss` steps (1.1) |
| `lssSlave`, `lssSlaveBitTiming`, `lssSlaveStore` | `device --node-id 255 --serial`, the device's LSS bit timing and store (1.1) |
| `rpdoSend` | `rpdo-send` (1.1) |
| `mpdoDam`, `mpdoSam` | `mpdo-send`, `mpdo-receive` in that mode (1.1) |
| `boot` | `boot` (1.1) |
| `cdcfDcfgen`, `cdcfObjects` | `cdcf --generator` (1.1) |
| `syncWindowRpdo` | the device discards a synchronous RPDO received after the SYNC window 0x1007 until the next SYNC (1.1; CiA 301 allows both) |

A 1.0 consumer ignores the 1.1 features; every 1.1 image declares all of
them.

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
(`abortCode` only for SDO aborts; `step` and `lssError` for `lss`, 1.1).
