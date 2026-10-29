# Testing

## Locally

On Linux with the CAN modules (Ubuntu: `linux-modules-extra-$(uname -r)`):

```sh
make ci                       # validate lint images smoke vcan selftest documents
make selftest-sdo             # one suite: od sdo nmt emcy pdo eds
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

Every result is `pass`, `fail`, `known` (a failure listed in
[tests/known-differences.tsv](tests/known-differences.tsv)) or `skip` (a
capability the stack lacks, with the reason). Reports: `reports/selftest.json`,
`reports/selftest.md`, every document in `reports/documents/` (validated by
`make documents` against `schemas/`); `scripts/compat-report.py` renders
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## CI

`.github/workflows/ci.yml`: validate and lint, then on an amd64 and an
arm64 runner every image built natively, the smoke test, the whole self-test
on vcan and the document validation; the compatibility report is the job
summary. `release.yml` repeats this on a tag and pushes what it tested.
`upstream.yml` compares the pins with the latest upstream releases weekly.
