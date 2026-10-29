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
* **Masters have no EDS**: `pdo-configure`/`pdo-receive` build the master's
  object dictionary from the mapping (lengths of 8/16/32/64 bits map to
  UNSIGNED types).

## Not covered

* LSS, SRDO, TIME, MPDO, SDO over other channels than the default SDO
  server, flying master, CiA 302 boot-up configuration, node guarding.
* RPDOs are not sent by any image; the device's RPDO consumer is verified
  by go-canopen's tests through `--dump`.
* CAN FD (CANopen FD, CiA 1301) and bit timing: vcan only.
* The `nmt` semantics of the lely master (network reset first) make NMT
  scenarios with lely as master check the final states, not exact event
  sequences.
