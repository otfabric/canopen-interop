# Releases

Each section is the body of its GitHub release (`scripts/release-notes.sh`).
A release is a pushed tag `vX.Y.Z` equal to `interop_version` in
versions.yaml: the Release workflow builds and self-tests every image
natively on amd64 and arm64, pushes `<image>:vX.Y.Z-<arch>`, joins them into
the multi-arch `<image>:vX.Y.Z` and publishes the release with the digests.

## v0.3.0

**Date:** 2026-10-31
**Previous release:** v0.2.0

### Added

- Adds `results.json` in the interop-result-v1 format.
- Policy 1.2: build output in `bin/` and `release/`, and shared CI at v3.3.0.

---

## v0.2.0

**Date:** 2026-10-29
**Previous release:** v0.1.0

Container contract 1.1: additions only, so that CANopen software such as
go-canopen can be cross-checked on LSS, multiplexed PDOs, the CiA 302
boot-up with concise DCFs and the SYNC window. Every 1.0 command, flag and
document is unchanged; documents still carry `"schemaVersion": "1.0"`.

* **LSS (CiA 305)**: `lss --step ...` runs LSS master steps in one run
  (switch global/selective, configure node-ID, configure and activate bit
  timing, store, inquire identity and node-ID, fastscan, assign-all) with
  the masters of all three stacks: CANopenNode's CiA 309-3 gateway
  (`lss_switch_glob`, `lss_switch_sel`, `lss_set_node`, `lss_conf_bitrate`,
  `lss_activate_bitrate`, `lss_store`, `lss_inquire_addr`, `lss_get_node`,
  `_lss_fastscan`, `lss_allnodes`), lely's coapp `LssMaster` (in the CiA 302
  boot-up) and python canopen's `network.lss`. `device --node-id 255
  --serial N` starts an unconfigured LSS slave (CANopenNode, lely) that
  reports the node-ID it takes (`node-id` events, `nodeId` in the result)
  and the bit rate it is asked to activate (`bit-rate`).
* **RPDO sender and SYNC window**: `rpdo-send` (python canopen) packs and
  sends a device's RPDO, optionally between two SYNCs; devices declare
  `syncWindowRpdo` (lely discards a synchronous RPDO received after the
  window 0x1007, CANopenNode does not; CiA 301 allows both).
* **MPDO**: `mpdo-send` / `mpdo-receive` (lely): DAM- and SAM-MPDO
  producer and consumer, with the object scanner (0x1FA0) and dispatching
  (0x1FD0) lists.
* **CiA 302 boot-up**: `boot` (lely) boots a network from the slaves' DCFs
  with a concise DCF per slave (0x1F22, written by Lely's dcfgen) and
  reports each 'boot slave' result with the CiA 302-2 error status.
* **Concise DCF**: `cdcf` writes lely's concise DCF of a DCF, from dcfgen
  (configuration entries) or `co_dev_write_dcf` (every sub-object of a
  range), for byte-for-byte comparisons.
* **Fixtures** (schema 1.1): 0x1007 and `LSS_Supported=1` in the shared
  device, `mpdo-device.eds`, `interop-device-other-product.eds`.
* **Self-test**: suites rpdo, lss (every LSS master × every LSS slave), mpdo,
  boot (a network of all three devices), cdcf: 54 pass, 6 known, 14 skip of
  the new suites; 389 pass, 15 known, 32 skip in all, no failure (Linux 7.0,
  arm64; CI on amd64 and arm64). New known
  differences: lely's LSS slave activates a configured node-ID only at NMT
  reset communication and answers fastscan whatever its node-ID, so
  CANopenNode's `lss_allnodes` keeps assigning node-IDs to the first lely
  slave.
* **Images**: CANopenNode v4.1 with the upstream fix for
  `lss_activate_bitrate` (commit cf85a4c, not in a release yet); lely with
  dcfgen (PyYAML, empy); the python canopen device processes NMT resets
  before the next NMT command (a master's start right after its reset was
  undone before). Upstream pins unchanged: v4.1, v2.3.5 and 2.4.1 are the
  latest releases.
* **Not covered**: CANopen FD (CiA 1301, USDO) — none of the pinned stacks,
  nor their development branches, implements it
  ([LIMITATIONS.md](LIMITATIONS.md)).

---

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
