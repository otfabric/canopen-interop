# Capabilities

What each image declares in `print-capabilities` (contract 1.0). Every
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
| deviceRpdo | ✅ | ✅ | – | (go-canopen's tests, `--dump`) |
| deviceLimits | – | ✅ | – | sdo `abort-value-*` |
| listenBootup, listenHeartbeat, listenEmcy | ✅ | ✅ | ✅ | nmt, emcy |
| listenAllNodes | – | ✅ | ✅ | |
| pdoConfigure | – | – | ✅ | pdo `pdo-configure-tpdo2` |
| pdoReceive | – | ✅ | ✅ | pdo |
| sync | – | – | ✅ | pdo `sync-tpdo3` |
| edsCheck | – | ✅ | ✅ | eds |
| cia402 | – | – | ✅ | smoke only |

`deviceRpdo` is declared from the stacks' documented RPDO consumers
(CANopenNode CO_RPDO, lely co_rpdo) and checked by go-canopen's tests
through `--dump`; no image of this repository sends RPDOs.
