# Releases

Each section is the body of its GitHub release (`scripts/release-notes.sh`).
A release is a pushed tag `vX.Y.Z` equal to `interop_version` in
versions.yaml: the Release workflow builds and self-tests every image
natively on amd64 and arm64, pushes `<image>:vX.Y.Z-<arch>`, joins them into
the multi-arch `<image>:vX.Y.Z` and publishes the release with the digests.

## v0.1.0

**Date:** 2026-10-29
**Previous release:** none (first release)

First release: CANopen reference implementations in containers behind one
contract, for interoperability testing of CANopen software such as
go-canopen.

* **Container contract 1.0** ([docs/CONTAINER_CONTRACT.md](docs/CONTAINER_CONTRACT.md))
  and JSON Schemas: `device`, `sdo-upload`, `sdo-download` (expedited,
  segmented, `--block`), `nmt`, `listen` (boot-up, heartbeat state changes,
  EMCY), `pdo-configure`, `pdo-receive`, `sync`, `eds-check`, `cia402`;
  JSON Lines output, exit codes 0–5 with SDO abort codes.
* **Images** `ghcr.io/otfabric/canopen-interop-<adapter>` for linux/amd64 and
  linux/arm64:
  * `canopennode`: CANopenNode v4.1 with CANopenLinux `canopend`; the device's
    object dictionary is generated from the shared EDS by CANopenEditor
    v4.2.3; the master uses canopend's CiA 309-3 ASCII gateway.
  * `lely`: Lely CANopen lely-core v2.3.5 through a C++ helper on
    liblely-coapp (BasicSlave, AsyncMaster, Sdo) and liblely-co; Lely's
    dcf-tools for `eds-check`.
  * `pythoncanopen`: python canopen 2.4.1 on python-can 4.6.1 (LocalNode,
    RemoteNode, SYNC producer, BaseNode402, EDS importer).
* **Fixtures**: the shared test device `fixtures/eds/interop-device.eds`
  (CiA 301 communication objects, four RPDOs/TPDOs, test objects 0x2000–0x200A
  of every basic type, string, DOMAIN, read-only, write-only, range-limited)
  and EDS/DCF fixtures for CompactSubObj, ObjectLinks, DCF ParameterValue and
  an invalid file.
* **Self-test** on vcan, every master × every device: object dictionary,
  SDO (incl. block transfer and aborts) with the device's own view of the
  written values, NMT and heartbeat, EMCY, event-driven, configured and
  SYNC-driven TPDOs, EDS checkers. Stack differences found and documented in
  [tests/known-differences.tsv](tests/known-differences.tsv), among them:
  lely's SDO server rejects a DOMAIN download without size indication (as
  CANopenNode's gateway sends long data), CANopenNode's SDO server cannot
  upload 0 bytes, python canopen's LocalNode ignores the sub-index of a VAR,
  and the checkers disagree on CompactSubObj and ObjectLinks.
* **Not in v0.1.0** ([LIMITATIONS.md](LIMITATIONS.md)): lely `sync` and
  `pdo-configure`, canopennode PDO/SYNC/EDS operations, a CiA 402 drive to
  verify `cia402` against, RPDO senders.

---
