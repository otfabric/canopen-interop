# Testing

## Locally

On Linux with the CAN modules (Ubuntu: `linux-modules-extra-$(uname -r)`):

```sh
make ci                       # validate lint images smoke vcan selftest documents
make selftest-sdo             # one suite: od sdo nmt emcy pdo eds rpdo lss mpdo boot cdcf
make smoke ADAPTERS=lely      # one adapter
```

On macOS/Docker Desktop there is no CAN: `make validate lint images smoke
selftest-eds`. A Linux VM with vcan (e.g. Lima) runs everything.

`VCAN=<ifc>` selects another interface. The self-test needs the interface to
itself: other CANopen traffic (another device with node-ID 5 or 9) disturbs
it.

## The self-test

`scripts/selftest.py`, suites (`SUITES`):

| Suite | Scenarios |
|-------|-----------|
| od | every sub-object of the shared EDS read from every device with the size of its data type (write-only objects abort 0x06010001; empty error history entries may abort 0x08000024); `empty-domain` |
| sdo | every master × every device: expedited and segmented uploads of known values; expedited, segmented and block downloads read back; block upload; aborts (no object, no sub-index, write-only, read-only, length low/high, value low/high where the device checks limits); a node that does not answer (exit 3); `device-dump`: the device's object dictionary holds every downloaded value |
| nmt | every master × every device: boot-up and pre-operational heartbeat via `listen`, then start, stop, pre-operational, start (all nodes), reset communication, start, reset node, each checked by the heartbeat state (and boot-up) that follows |
| emcy | every master's EMCY consumer × every EMCY producer: code 0x1000, register 0x01, data 2b01020304 |
| pdo | every device with event-driven TPDOs × every master with pdo-receive: TPDO1 (counter 0x2002, increasing), TPDO2 configured by `pdo-configure` (0x2002 + 0x2001, event timer), TPDO3 configured synchronous (transmission type 1) and driven by each SYNC producer |
| eds | every checker × every EDS/DCF fixture: validity, the objects compared with the fixture, and the checkers compared with each other |
| rpdo (1.1) | every RPDO sender × every device with RPDOs: an event-driven RPDO 1 read back; synchronous RPDO 1 (transmission type 1) with no SYNC window, inside a 50 ms window (5 ms after SYNC) and after it (150 ms): discarded where the device declares `syncWindowRpdo`, applied otherwise |
| lss (1.1) | per LSS master: switch selective with an address nobody has (exit 3); every LSS master × every LSS slave (unconfigured device, own serial number): inquire identity and node-ID 255; switch selective, configure node-ID 40, store, switch to waiting: the node-ID active without an NMT command (`lss-activate-on-waiting`), else after NMT reset communication, and node 40 answering SDO; configure and activate bit timing (250 kbit/s, 50 ms) as the device reports it; fastscan of a new unconfigured device and node-ID 41; fastscan with only a configured device on the bus (no answer); assign-all of two unconfigured devices (node-IDs 126, 127) |
| mpdo (1.1) | every MPDO producer × consumer: DAM to the consumer, to another node (ignored), to all nodes; SAM with two dispatching entries and two MPDOs that are not dispatched |
| boot (1.1) | every boot master: a network of one device of every stack (nodes 21–23) booted with concise DCFs (0x2001 and heartbeat 200 ms), read back by SDO and checked operational by its heartbeat; a device whose product code differs (error status M) per device; a missing mandatory slave (error status B) |
| cdcf (1.1) | every generator: dcfgen's concise DCF of five `--sdo` entries compared byte for byte with the CiA 302-3 encoding, and the objects generator over 0x1000–0x1FFF and 0x2000–0x2004 of `interop-device-node5.dcf` compared with every sub-object's value ($NODEID = 5, ParameterValue first) |

Every result is `pass`, `fail`, `known` (a failure listed in
[tests/known-differences.tsv](tests/known-differences.tsv)) or `skip` (a
capability the stack lacks, with the reason). Reports: `reports/selftest.json`,
`reports/results.json` (interop-result-v1: a run with no pass, or with a
known difference that no longer reproduces, fails), `reports/selftest.md`, every document in `reports/documents/` (validated by
`make documents` against `schemas/`); `scripts/compat-report.py` renders
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## CI

`.github/workflows/ci.yml`: validate and lint, then on an amd64 and an
arm64 runner every image built natively, the smoke test, the whole self-test
on vcan and the document validation; the compatibility report is the job
summary. `release.yml` repeats this on a tag and pushes what it tested.
`upstream.yml` compares the pins (and the base-image digests) with the latest
upstream daily and keeps an issue open while one is behind.
