# Capabilities

What each reference image declares in `print-capabilities` (contract 1.1; 1.2 adds only the optional `upstream`, see the last section). Every
`true` is exercised by a self-test scenario ([TESTING.md](../TESTING.md));
`cia402` is verified only for its error path (no adapter provides a CiA 402
drive, see [LIMITATIONS.md](../LIMITATIONS.md)).

## Operations

| Operation | canopennode | lely | pythoncanopen |
|-----------|:-:|:-:|:-:|
| device | ✅ | ✅ | ✅ |
| sdo-upload, sdo-download | ✅ | ✅ | ✅ |
| nmt | ✅ | ✅ (booted master, see LIMITATIONS) | ✅ |
| listen | ✅ (with `--node-id`) | ✅ | ✅ |
| pdo-configure | – | – | ✅ |
| pdo-receive | – | ✅ | ✅ |
| sync | – | – | ✅ |
| eds-check | – | ✅ (dcf-tools) | ✅ (import_od) |
| cia402 | – | – | ✅ (BaseNode402) |
| lss (1.1) | ✅ (gateway `lss_*`) | ✅ (coapp LssMaster, in the CiA 302 boot-up) | ✅ (`network.lss`) |
| rpdo-send (1.1) | – | – | ✅ (RemoteNode RPDO, SYNC producer) |
| mpdo-send, mpdo-receive (1.1) | – | ✅ (co_tpdo/co_rpdo MPDO) | – |
| boot (1.1) | – | ✅ (CiA 302 NMT master, dcfgen) | – |
| cdcf (1.1) | – | ✅ (dcfgen, co_dev_write_dcf) | – |

## Features

| Feature | canopennode | lely | pythoncanopen | Evidence |
|---------|:-:|:-:|:-:|---|
| sdoExpedited | ✅ | ✅ | ✅ | sdo `upload-expedited-*`, `download-expedited-*` |
| sdoSegmented | ✅ | ✅ | ✅ | sdo `upload-segmented-*`, `download-segmented-*` |
| sdoBlock | ✅ | ✅ | ✅ | sdo `download-block-domain`, `upload-block-small` |
| deviceSegmented | ✅ | ✅ | ✅ | od, sdo |
| deviceBlock | ✅ | ✅ | – (SdoServer has no block transfer) | sdo |
| heartbeatProducer | ✅ | ✅ | ✅ | nmt |
| emcy | ✅ (register from error bit) | ✅ (bit 0 always set) | ✅ | emcy |
| tick | ✅ | ✅ | – (LocalNode has no TPDO producer) | pdo |
| deviceTpdo | ✅ | ✅ | – | pdo |
| deviceRpdo | ✅ | ✅ | – | rpdo `rpdo-event` (since 1.1) |
| deviceLimits | – | ✅ | – | sdo `abort-value-*` |
| listenBootup, listenHeartbeat, listenEmcy | ✅ | ✅ | ✅ | nmt, emcy |
| listenAllNodes | – | ✅ | ✅ | |
| pdoConfigure | – | – | ✅ | pdo `pdo-configure-tpdo2` |
| pdoReceive | – | ✅ | ✅ | pdo |
| sync | – | – | ✅ | pdo `sync-tpdo3` |
| edsCheck | – | ✅ | ✅ | eds |
| cia402 | – | – | ✅ | smoke only |
| lssMaster (1.1) | ✅ | ✅ | ✅ | lss `lss-inquire`, `lss-configure-node-id`, `lss-switch-selective-none` |
| lssBitTiming (1.1) | ✅ | ✅ (not index 9, automatic) | ✅ | lss `lss-bit-timing` |
| lssFastscan (1.1) | ✅ (`_lss_fastscan`) | ✅ | ✅ | lss `lss-fastscan`, `lss-fastscan-configured` |
| lssAssignAll (1.1) | ✅ (`lss_allnodes`) | – | – | lss `lss-assign-all` |
| lssSlave (1.1) | ✅ (`canopend -i 0xFF`) | ✅ (BasicSlave, LSS_Supported=1) | – | lss |
| lssSlaveBitTiming (1.1) | ✅ (device application callbacks) | ✅ | – | lss `lss-bit-timing` |
| lssSlaveStore (1.1) | ✅ (RAM) | ✅ (RAM) | – | lss `lss-configure-node-id` |
| rpdoSend (1.1) | – | – | ✅ | rpdo |
| mpdoDam, mpdoSam (1.1) | – | ✅ | – | mpdo `mpdo-dam`, `mpdo-sam` |
| boot (1.1) | – | ✅ | – | boot |
| cdcfDcfgen, cdcfObjects (1.1) | – | ✅ | – | cdcf, smoke |
| syncWindowRpdo (1.1) | – (TPDOs only) | ✅ | – (no RPDOs) | rpdo `rpdo-sync-after-window` |

No image declares CANopen FD (CiA 1301): none of the pinned stacks (nor
their development branches) implements USDO or CANopen FD PDOs
([LIMITATIONS.md](../LIMITATIONS.md)).

`deviceRpdo` is verified since 1.1 by the rpdo suite: python canopen's
`rpdo-send` sends RPDO 1 to each device and the values are read back.

## OT Fabric's own stack (`otfabric`, not a reference)

`ghcr.io/otfabric/canopen-interop-otfabric` ([own/go-canopen](../own/go-canopen/README.md))
declares every operation and every feature of contract 1.2 `true`, and
`upstream` with `independentEngine: false` (contract 1.2): its results are
recorded as `independent: false` and are no evidence about go-canopen from
an independent implementation. Every declaration is exercised by the same
suites of `scripts/selftest.py` against the three references (the own-stack
workflow runs them with `scripts/interop.sh otfabric canopennode lely
pythoncanopen`); `cia402` only by go-canopen's own interop suite, which has a
CiA 402 drive. Its scenarios that end as skip are those of a reference's
missing capability (for example a device without `deviceLimits`), and its
known differences are those of the references already listed in
`tests/known-differences.tsv`.
