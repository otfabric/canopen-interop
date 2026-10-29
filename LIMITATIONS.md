# Limitations

## Per stack

### canopennode (CANopenNode v4.1, CANopenLinux)

* **Device object dictionary is compiled.** `canopend-device` is built with
  the object dictionary of `fixtures/eds/interop-device.eds`; `device --eds`
  with another file ends with exit 2. CANopenEditor generates OD.c/OD.h from
  the EDS; the CANopenNode "count labels" (OD_CNT_*), which an EDS cannot
  carry, are added by `adapters/canopennode/od-counts.sh`, and the build
  maps two names CANopenLinux's main expects (`OD_PERSIST_COMM`,
  `x1018_identity`) to the generated ones. Storage (0x1010/0x1011), the
  gateway, the heartbeat consumer and the SDO client are compiled out of the
  device (the test device has no such objects).
* **Boot.** The device is built without `CO_NMT_STARTUP_TO_OPERATIONAL`, so
  it waits in pre-operational. With an error in the error register it does
  not stay operational (`CO_NMT_ERR_ON_ERR_REG`, CANopenLinux's default).
* **NMT reset node** ends canopend (CANopenLinux exits on "reset
  application"); the adapter restarts it, as a device's power cycle. Values
  written before are lost, as on a real reset.
* **EMCY**: CANopenNode reports error conditions (`CO_errorReport`) and
  derives the error register from the error status bit; the five
  manufacturer bytes are the bit and a 32-bit info code. Other
  `--emcy` values are `unsupported`.
* **LowLimit/HighLimit** are not checked (`deviceLimits` false).
* **Master**: canopend with CANopenNode's example object dictionary (node-ID
  0x7F) behind its ASCII gateway (on a local socket). It has no PDO, SYNC or
  EDS operations; `listen` needs `--node-id` (the heartbeat consumer
  0x1016 monitors configured nodes). On start it sends its own boot-up and an
  EMCY 0x5000 (no storage files): consumers of all nodes see node 0x7F.
  Downloads longer than the gateway's buffer are sent without size
  indication (see known differences).

* **LSS master (1.1)**: the gateway's LSS master (CO_LSSmaster) configures
  node-ID, bit timing and store only on a slave selected by
  `switch-selective` or `fastscan` (node-ID 255 also after
  `switch-global:configuration`), and activates bit timing only after
  `switch-global:configuration` (any other order: exit 5, gateway error
  102). It reports a slave's rejection without its error code (no
  `lssError`). `fastscan`/`assign-all` wait 100 ms per scanned bit.
  `lss_activate_bitrate` fails in v4.1 (token buffer of 20 bytes for a
  20-character command): the image applies the upstream fix (commit
  cf85a4c on master, 2026-05-31); no release contains it yet.
* **LSS slave (1.1)**: `canopend-device -i 0xFF`; the device application
  (app_interop.c) sets the serial number and accepts every bit rate of the
  CiA 301 table (CANopenLinux registers no bit rate callbacks, so
  CANopenNode would ignore "configure bit timing"); "store" keeps the
  values in RAM (CANopenLinux's callback), and an NMT reset node restarts
  canopend with its power-on node-ID. SYNC window: CO_SYNC uses 0x1007 to
  cancel pending synchronous TPDOs, not to discard RPDOs (`syncWindowRpdo`
  false, CiA 301 allows both).
* **No MPDO, no CiA 302 boot master, no concise DCF generator** in
  CANopenNode v4.1.

### lely (lely-core v2.3.5)

* **A lely master boots as a CiA 302 NMT master** and, while booting, sends
  NMT "reset communication" to all nodes. `sdo-upload`, `sdo-download` and
  `pdo-receive` therefore do **not** boot the master (they use the coapp
  `Sdo` client and the C RPDO service directly), but `nmt`
  and `listen` must: `listen` resets the network when it starts (start it
  before the device), `nmt` resets the network and sends the command 500 ms
  later.
* **Device**: the adapter loads the EDS with an added 0x1F80 (NMT startup)
  = 0x00000004, because a lely slave without it enters operational by
  itself; `--heartbeat-ms` is written as `ParameterValue` of 0x1017 so lely
  restores it on reset. An SDO upload of 0x1F80 therefore succeeds on the
  lely device only.
* **EMCY**: lely always sets bit 0 (generic error) of the error register.
* **pdo-receive** maps the remote objects into the master's scratch arrays
  0x5F00–0x5F03: objects of 8, 16, 32 or 64 bits only. No `pdo-configure`
  (coapp configures slaves through concise DCFs while booting them, which
  needs the network reset).
* **No `sync`** in v0.1.0: lely's SYNC producer service (`co_sync`) run
  without a booted master hung the helper's event loop in our tests; a
  booted master would reset the network first. Not declared.
* **LSS master (1.1)**: lely-core runs an LSS master only as part of a
  CiA 302 NMT master's boot-up (the LSS request of the 'reset
  communication' state, coapp `LssMaster::OnStart`): `lss` boots a master
  without slaves, runs the steps, and the master's start-up then sends NMT
  reset communication to all nodes, which also activates pending node-IDs
  (scenario `lss-activate-on-waiting` is skipped for it). coapp takes bit
  rates in bit/s: no automatic bit rate (index 9). `activate-bit-timing`
  needs a `configure-bit-timing` earlier in the same run. No assign-all
  service (`lssAssignAll` false).
* **LSS slave (1.1)**: the device's EDS must say `LSS_Supported=1` (lely
  creates no LSS service otherwise). A configured node-ID becomes active
  only at the next NMT reset communication, not when an unconfigured slave
  switches to LSS waiting, and the slave answers LSS fastscan whatever its
  node-ID (known differences). "store" (`BasicSlave::OnStore`) keeps the
  values for the run.
* **MPDO (1.1)**: the producer and consumer are devices of
  `fixtures/eds/mpdo-device.eds` that start themselves into operational
  (0x1F80 = 0); one MPDO producer (TPDO 1) or consumer (RPDO 1) per run, at
  most 4 dispatching entries. lely reads the block size of a dispatching
  entry (0x1FD0) from bit 54 instead of bit 56 (`co_dev_map_sam_mpdo`); the
  self-test uses block size 1 and single sub-objects, where this does not
  show. A SAM-MPDO carries up to 4 data bytes: objects of 64 bits cannot be
  sent.
* **boot (1.1)**: the master DCF and the concise DCFs are written by
  dcfgen (dcf-tools) from a YAML configuration the adapter builds from the
  flags; the boot time 0x1F89 is half of `--timeout-ms`, after which a
  mandatory slave that does not answer ends with error status B (lely
  retries it until then). A slave that sends another boot-up while the
  master runs is booted again (one more `boot` event).
* **cdcf (1.1)**: `dcfgen` writes a concise DCF only for slaves with
  configuration entries: `--sdo` is required; dcfgen packs REAL values as
  floats and everything else as integers of the object's size.
* **eds-check** uses Lely's Python dcf-tools (dcfchk's `lint` and its
  `Device` model), not the C parser of the library; `$NODEID` is evaluated
  with the DCF's node-ID, else 1.

### pythoncanopen (python canopen 2.4.1)

* **Device**: `LocalNode` has no SDO block transfer, no TPDO producer, no
  RPDO consumer and no limit checks (`deviceBlock`, `tick`, `deviceTpdo`,
  `deviceRpdo`, `deviceLimits` false). It leaves booting to the
  application: the adapter sends the boot-up (`NmtSlave`) at start and after
  an NMT reset command, and restores the EDS values on reset node.
* **Heartbeat events**: python canopen reports every heartbeat; the adapter
  reports a heartbeat event when the state differs from the node's previous
  one, like the other stacks' consumers.
* **cia402** is implemented with `BaseNode402` over SDO but no adapter
  provides a CiA 402 drive: the self-test cannot verify the state machine.
* **LSS (1.1)**: master only (`network.lss`), no slave, no assign-all.
  Each answer is awaited 0.3 s (fastscan waits that long for every bit
  no slave confirms). `switch-selective` sleeps 0.2 s after each of its four
  frames (python canopen's own pacing).
* **rpdo-send (1.1)**: the RPDO's mapping is read by SDO; every mapped
  object needs a `--set` value of the mapping's length.
* **Masters have no EDS**: `pdo-configure`/`pdo-receive` build the master's
  object dictionary from the mapping (lengths of 8/16/32/64 bits map to
  UNSIGNED types).

## Not covered

* **CANopen FD (CiA 1301)**: no USDO, no CAN FD PDOs. None of the pinned
  stacks implements CANopen FD, nor do their development branches as of
  2026-10-29 (CANopenNode master, lely-core master, python canopen
  master); the open-source landscape has none that could be pinned (the
  CANopen FD stacks found are commercial). vcan carries CAN FD frames, so
  a CANopen FD reference can be added when one exists.
* SRDO, TIME, SDO over other channels than the default SDO server, flying
  master, CiA 302 configuration manager with stored concise DCFs
  (0x1F20/0x1F22 written by SDO), SDO manager, node guarding.
* Bit timing is configured and activated by LSS, but vcan has no bit rate:
  the devices report the activation (`bit-rate` event) and keep running.
* MPDOs only between lely nodes; the SYNC window only for RPDOs of the
  devices (a TPDO that misses its window cannot be provoked on vcan).
* The `nmt` semantics of the lely master (network reset first) make NMT
  scenarios with lely as master check the final states, not exact event
  sequences.
