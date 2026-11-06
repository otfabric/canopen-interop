# Contributing

## Adding an adapter

1. `adapters/<name>/Dockerfile` (build context: the repository root) and the
   adapter program. Adapters reuse `adapters/common/coi.py` (command line,
   documents, exit codes); the CANopen work stays in the upstream stack.
2. Pin everything: base image by digest, upstream by version, revision and
   checksum; add the entry to [versions.yaml](versions.yaml) and make
   `scripts/check-versions.sh` check it.
3. Declare only capabilities a self-test scenario verifies; add the scenario
   when there is none. A difference between stacks that cannot be avoided
   goes to `tests/known-differences.tsv` with its explanation.
4. Add the upstream licence to [licenses/](licenses) and
   [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); ship source where the
   licence requires it.
5. Add it to `ADAPTERS` in the Makefile and `ALL_ADAPTERS` in
   `scripts/selftest.py`; `make ci` on Linux.

## OT Fabric's own stack (`own/`)

`own/go-canopen/` is go-canopen behind the contract, published by hand by the
**Own stack (go-canopen)** workflow as `canopen-interop-otfabric`
([own/go-canopen/README.md](own/go-canopen/README.md)). It is the one exception
to [REQUIREMENTS.md](REQUIREMENTS.md) R1: nothing under `adapters/`,
`fixtures/`, `schemas/`, `scripts/`, `tests/` or the `Makefile` may name
go-canopen or canopenctl (CI checks it); the scripts take the image through
`ADAPTER_IMAGE_otfabric`. It is not in `ADAPTERS` and not in a release. Check a
change with `make -f own/go-canopen/Makefile image smoke interop` (interop on
Linux with vcan).

## Changing fixtures

Edit `scripts/gen-eds-fixtures.py`, run `make fixtures`, rebuild the
canopennode image (its device object dictionary is generated from the EDS).

## Changing the contract

Additions bump the minor `contract_version`, breaking changes the major one
(and `schemaVersion` of the documents). Update the schemas, the contract
document and its version table, and every adapter.

## Conventions

MIT for original files with an `SPDX-License-Identifier: MIT` header;
commits small and reviewable; `make validate lint` before pushing.
