# canopen-interop-otfabric

[go-canopen](https://github.com/otfabric/go-canopen), OT Fabric's own CANopen
stack, behind the [container contract](../../docs/CONTAINER_CONTRACT.md), so
that other stacks can test against it. Anything that uses a
`canopen-interop-<adapter>` image can use this one in its place. Unlike those,
it is not a reference: its tags are go-canopen versions, and no harness release
contains it.

**For testing only.** It runs CANopen devices and masters without any access
control on the CAN interface it is given. Run it only on a virtual (vcan) or
isolated bus. Old images stay available, so check
[go-canopen advisories](https://github.com/otfabric/go-canopen/security/advisories)
for the version you pull.

**Not an independent implementation.** The image declares
`upstream.independentEngine: false`, and `results.json` records
`independent: false`. A result against it says nothing independent about
go-canopen when OT Fabric produces it. A result you publish, naming the
implementation and version, is independent evidence, and we would like to hear
about it.

## Use

```sh
sudo scripts/setup-vcan.sh vcan0
docker run -d --name go-canopen --network host ghcr.io/otfabric/canopen-interop-otfabric:<tag> \
  device --interface vcan0 --node-id 5 --eds /fixtures/eds/interop-device.eds --timeout-ms 600000
docker run --rm --network host ghcr.io/otfabric/canopen-interop-otfabric:<tag> \
  sdo-upload --interface vcan0 --node-id 5 --index 0x1018 --subindex 1
```

`<tag>` is a go-canopen version such as `v0.4.1`, or `latest`, the newest one
published. A rebuild of a version replaces its tag, so pin the digest if you need
an exact build. The digest is in the summary of the
[workflow run](https://github.com/otfabric/canopen-interop/actions/workflows/own-go-canopen.yml),
and `docker run --rm <image> print-version` prints the versions.

| | |
|---|---|
| Upstream | go-canopen, the latest release when the image was built |
| Licence | MIT; every module's licence is in the image under `/licenses` |
| Adapter name | `otfabric` (the name in documents, as the schema allows) |
| Language | Go |
| Protocol engine | OT Fabric's own (go-canopen on go-canbus SocketCAN) |

## Why it is apart from the other adapters

`REQUIREMENTS.md` R1: nothing under `adapters/`, `fixtures/`, `schemas/`,
`scripts/`, `tests/` or the top-level `Makefile` knows a library under test,
and CI enforces that. This directory and
`.github/workflows/own-go-canopen.yml` are the one exception. The scripts take
the image through `ADAPTER_IMAGE_otfabric`.

No release loop can form (`otfabric/engineering` ADR-0012). This image is built
by hand, from the latest go-canopen release, and nothing is committed: this
repository has no pin of go-canopen to bump. No release or required check of
go-canopen needs the image, and go-canopen commits no pin of it. It is not in a
harness release.

## Publishing

Run the **Own stack (go-canopen)** workflow by hand, from `main`. It finds the
latest go-canopen release and the latest harness release, and prints them. It
builds the image, loads the CAN modules and creates `vcan0` on the runner, runs
the smoke test and the cross-stack self-test (every suite of
`scripts/selftest.py`, against the references of that harness release) on both
architectures, then publishes `:<go-canopen version>` and `:latest`. Run from a
branch, it stops before publishing. Nothing runs it automatically, so after a
go-canopen release the image stays on the old version until someone does.

```sh
make -f own/go-canopen/Makefile image smoke                        # from the repository root, no CAN needed
make vcan && make -f own/go-canopen/Makefile interop VCAN=vcan0    # Linux with the CAN modules
```

On macOS (Docker Desktop has no CAN modules) the interop target runs in a Linux
VM, as [TESTING.md](../../TESTING.md) describes.

## What it does

Every operation and feature of contract 1.2 is declared `true`: go-canopen's
public API has each service, and the adapter only translates the contract to
it (it never builds or parses a CANopen frame itself).

| Operation | go-canopen |
|---|---|
| `device` | `node.Node` from the EDS (`eds.File.Dictionary`); LSS slave with `node.WithLSS` and a memory `store` |
| `sdo-upload`, `sdo-download` | `sdo.Client` (`--block`: `sdo.BlockAlways`) |
| `nmt` | `nmt.Master` |
| `listen` | `nmt.HeartbeatConsumer`, `emcy.Consumer` |
| `pdo-configure` | `pdo.ReadConfig`, `pdo.Configure` over SDO |
| `pdo-receive`, `rpdo-send` | `master.Master` `ListenTPDO`, `RPDOSender`; the mapped objects' data types from the shared test device (`/fixtures/eds/interop-device.eds`) |
| `sync` | `sync.Producer` |
| `eds-check` | `eds.File.Validate`, `BuildDictionary` |
| `cia402` | `cia402.Controller` (`master.Master.Drive`) |
| `lss` | `lss.Master` (`FastscanSelect`, `AssignAll`) |
| `mpdo-send`, `mpdo-receive` | a self-starting `node.Node` of `mpdo-device.eds` with its `mpdo.Producer` / `mpdo.Consumer` |
| `boot` | `master.Master` as CiA 302 manager (`WithDictionary`, `AddManagerObjects`, network list 0x1F81, concise DCFs 0x1F22) |
| `cdcf` | `cdcf.Encode` of `canopen.ParseValue` entries or of the DCF's dictionary |

`listen` reports a heartbeat when the state differs from the node's last one
(go-canopen's `EventStateChange`), as the python canopen adapter does.
