# Changelog

Release notes with details: [RELEASE.md](RELEASE.md).

## v0.2.0

Container contract 1.1 (additions only): `lss` (LSS master steps of CiA 305:
switch global/selective, configure node-ID and bit timing, activate bit
timing, store, inquire, fastscan, assign-all) for all three stacks; LSS
slave devices (`device --node-id 255 --serial`, `node-id`/`bit-rate` events)
for CANopenNode and lely; `rpdo-send` with the SYNC window (python canopen);
`mpdo-send`/`mpdo-receive` (DAM and SAM-MPDO, lely); `boot` (CiA 302 boot-up
with concise DCFs, lely); `cdcf` (concise DCFs from lely's dcfgen and
co_dev_write_dcf). Fixtures: 0x1007 and LSS_Supported in the shared device,
`mpdo-device.eds`, `interop-device-other-product.eds`. Self-test suites
rpdo, lss, mpdo, boot, cdcf; new known differences of lely's LSS slave.
CANopenNode image: upstream fix for `lss_activate_bitrate`. Python canopen
device: NMT resets processed on the receive thread.

## v0.1.0

First release: container contract 1.0, JSON Schemas, three CANopen
reference images (CANopenNode v4.1 with CANopenLinux, Lely CANopen
lely-core v2.3.5, python canopen 2.4.1) as device and master, the shared
test device EDS and EDS/DCF fixtures, capability-driven self-test (object
dictionary, SDO incl. block transfer, NMT and heartbeat, EMCY, PDO and SYNC,
EDS checkers), CI on amd64 and arm64 with vcan, tag-driven multi-arch
release to GHCR, weekly upstream pin check.
