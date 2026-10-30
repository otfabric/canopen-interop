# canopen-interop

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI Status](https://github.com/otfabric/canopen-interop/actions/workflows/ci.yml/badge.svg)](https://github.com/otfabric/canopen-interop/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/otfabric/canopen-interop?label=release)](https://github.com/otfabric/canopen-interop/releases)

Independent CANopen implementations in containers, with one command-line
contract, for interoperability testing of CANopen software such as
[go-canopen](https://github.com/otfabric/go-canopen). Nothing in this
repository depends on go-canopen.

Every image runs one upstream CANopen stack behind the same commands, flags,
JSON documents and exit codes ([container contract](docs/CONTAINER_CONTRACT.md),
[JSON Schemas](schemas)): as a **device** (SDO server, NMT slave, heartbeat,
EMCY and PDO producer with the object dictionary of a shared EDS) and as a
**master** (SDO client with block transfer, NMT master, heartbeat and EMCY
consumers, PDO and SYNC), plus an EDS/DCF checker. Contract 1.1 adds LSS
(CiA 305) masters and unconfigured LSS slaves, an RPDO sender with the SYNC
window, multiplexed PDOs (DAM/SAM), the CiA 302 boot-up of a network with
concise DCFs, and concise DCF generators. A self-test runs the stacks
against each other on Linux virtual CAN (`vcan`), so a test suite can trust
a reference before it trusts a result against it.

## Stacks

| Adapter | Upstream | Version | Language | Licence | Operations |
|---------|----------|---------|----------|---------|------------|
| `canopennode` | [CANopenNode](https://github.com/CANopenNode/CANopenNode) with [CANopenLinux](https://github.com/CANopenNode/CANopenLinux) (`canopend`) | v4.1 | C | Apache-2.0 | device (also LSS slave), sdo-upload, sdo-download, nmt, listen, lss |
| `lely` | [Lely CANopen](https://gitlab.com/lely_industries/lely-core) (lely-core) | v2.3.5 | C/C++ | Apache-2.0 | device (also LSS slave), sdo-upload, sdo-download, nmt, listen, pdo-receive, eds-check, lss, mpdo-send, mpdo-receive, boot, cdcf |
| `pythoncanopen` | [canopen](https://github.com/christiansandberg/canopen) on python-can 4.6.1 | 2.4.1 | Python | MIT (python-can: LGPL-3.0) | device, sdo-upload, sdo-download, nmt, listen, pdo-configure, pdo-receive, sync, eds-check, cia402, lss, rpdo-send |

* **canopennode**: the device is `canopend` with an object dictionary
  generated from the shared EDS by [CANopenEditor](https://github.com/CANopenNode/CANopenEditor)
  v4.2.3 at build time (with `-i 0xFF` an unconfigured LSS slave); the
  master is `canopend` with CANopenNode's example object dictionary, driven
  through its CiA 309-3 ASCII gateway (SDO, NMT and the LSS commands).
* **lely**: a small C++ program on liblely-coapp (`BasicSlave`,
  `AsyncMaster`, `Sdo`, `LssMaster`) and liblely-co (NMT command, RPDO
  service, MPDO, concise DCF writer), and Lely's Python dcf-tools (`dcfchk`
  for `eds-check`, `dcfgen` for `boot` and `cdcf`).
* **pythoncanopen**: `LocalNode`, `RemoteNode` (also its RPDOs),
  `BaseNode402`, the SYNC producer, the LSS master and the EDS importer of
  python canopen.

No pinned stack implements CANopen FD (CiA 1301); see
[LIMITATIONS.md](LIMITATIONS.md).

Capabilities per image: [docs/CAPABILITIES.md](docs/CAPABILITIES.md) and
`print-capabilities`. What each stack cannot do: [LIMITATIONS.md](LIMITATIONS.md).

## The shared test device

[`fixtures/eds/interop-device.eds`](fixtures/eds/interop-device.eds) (CiA 306
EDS, in every image as `/fixtures/eds/interop-device.eds`): the
communication objects of CiA 301 (0x1000–0x1A03, four RPDOs and TPDOs, TPDO1
mapping the counter 0x2002 event-driven) and a test area 0x2000–0x200A
(every basic type, a 64-character string, a DOMAIN for segmented and block
transfers, read-only, write-only and range-limited objects).
[docs/FIXTURES.md](docs/FIXTURES.md) lists every object.

## Quick start

On Linux with the CAN modules (Ubuntu: `linux-modules-extra-$(uname -r)`):

```sh
make vcan                    # vcan0 (sudo)
make images                  # every image for this machine's architecture
make smoke selftest          # contract smoke test, reference-to-reference self-test
```

On macOS or with Docker Desktop (no CAN in its kernel): `make images smoke
selftest-eds` runs everything that does not need a CAN interface.

Use a published image directly:

```sh
docker run --rm --network host ghcr.io/otfabric/canopen-interop-lely:v0.2.0 \
    device --interface vcan0 --node-id 5 --eds /fixtures/eds/interop-device.eds --timeout-ms 60000 &
docker run --rm --network host ghcr.io/otfabric/canopen-interop-canopennode:v0.2.0 \
    sdo-upload --interface vcan0 --node-id 5 --index 0x1008
```

CAN interfaces belong to a network namespace and ordinary Docker networks do
not carry CAN, so containers run with `--network host` against `vcan`
interfaces of the host namespace.

## Self-test

`scripts/selftest.py` ([TESTING.md](TESTING.md)) runs, capability driven,
every master against every device: the object dictionary of the shared EDS
read on every device; SDO expedited, segmented and block upload and download
with read-back and the device's own view of the written values; SDO aborts;
NMT commands and the heartbeat states that follow, through each master's
heartbeat consumer; EMCY; event-driven, configured and SYNC-driven TPDOs;
and the EDS/DCF checkers on every fixture; since 1.1 also RPDOs with the
SYNC window, every LSS master against every LSS slave (inquire, configure
node-ID, store, bit timing, fastscan, assign-all), DAM/SAM-MPDOs, the CiA 302
boot-up of a network of all three devices with concise DCFs, and the
concise DCFs byte for byte. Accepted differences are listed in
[tests/known-differences.tsv](tests/known-differences.tsv) and reported as
`known`, never as `pass`; a scenario a stack cannot do is `skip` with the
reason. Results: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Documentation

| Document | Content |
|----------|---------|
| [docs/CONTAINER_CONTRACT.md](docs/CONTAINER_CONTRACT.md) | Commands, flags, documents, exit codes |
| [docs/CAPABILITIES.md](docs/CAPABILITIES.md) | Capability matrix |
| [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) | Self-test results |
| [docs/FIXTURES.md](docs/FIXTURES.md) | The shared test device and the EDS/DCF fixtures |
| [INTEROPERABILITY.md](INTEROPERABILITY.md) | How to test an implementation against these images |
| [TESTING.md](TESTING.md) | Running the tests |
| [LIMITATIONS.md](LIMITATIONS.md) | What is not covered, stack by stack |
| [SECURITY.md](SECURITY.md) | Reporting, image hardening, safety of CAN traffic |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Adding an adapter, changing the contract |
| [RELEASE.md](RELEASE.md) / [CHANGELOG.md](CHANGELOG.md) | Releases |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Upstream licences |

## Licence

The adapters, scripts, schemas and fixtures of this repository are MIT
licensed ([LICENSE](LICENSE)). Each image also contains its upstream stack
under that stack's licence ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
