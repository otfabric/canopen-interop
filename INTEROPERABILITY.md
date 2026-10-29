# Interoperability testing with canopen-interop

## Testing a CANopen master (client)

1. Create a vcan interface (`make vcan` or `scripts/setup-vcan.sh vcan0`).
2. Start a reference device, with the values to check at the end:

   ```sh
   docker run --rm --network host --name dev ghcr.io/otfabric/canopen-interop-lely:v0.1.0 \
       device --interface vcan0 --node-id 5 --eds /fixtures/eds/interop-device.eds \
       --heartbeat-ms 100 --tick-ms 50 --dump 0x2000:00 --dump 0x2005:00 --timeout-ms 60000
   ```

   Wait for `{"type":"ready",...}` on stdout, run the implementation under
   test against node 5 (SDO, NMT, heartbeat consumer, PDOs of TPDO1, RPDO1
   writing 0x2001/0x2003), then `docker stop dev`: the result line holds the
   values the device's object dictionary ended with.
3. Repeat with every device image (`canopennode`, `lely`, `pythoncanopen`):
   their differences ([tests/known-differences.tsv](tests/known-differences.tsv),
   [LIMITATIONS.md](LIMITATIONS.md)) are where implementations disagree in
   practice.

## Testing a CANopen device (server)

Run the device with an object dictionary like `fixtures/eds/interop-device.eds`
and drive it with the master images: `sdo-upload`/`sdo-download` (add
`--block`), `nmt`, `listen` (start it before the device to see the boot-up),
`pdo-receive`, `sync`, `pdo-configure`. Every operation reports what the
upstream stack did, with exit codes for timeouts (3) and SDO aborts (5, with
`abortCode`).

## Pinning

Pin images by digest (the release notes and `manifest.json` of each GitHub
release list them), not by tag.

## What vcan can and cannot show

vcan is a software loopback: there are no bit timing, arbitration, error
frames or bus-off, and every frame arrives. It tests the CANopen protocol
layer (SDO, NMT, heartbeat, EMCY, PDO and SYNC framing and state machines),
not CAN controller behaviour or real-time constraints. Timing-dependent
behaviour (heartbeat consumer timeouts, SYNC windows, inhibit times) is
covered only loosely.
