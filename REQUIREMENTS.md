# Requirements

What this repository must do and must not do.
[docs/CONTAINER_CONTRACT.md](docs/CONTAINER_CONTRACT.md) and
[docs/FIXTURES.md](docs/FIXTURES.md) are the specifications that follow from
it.

## Purpose

Independent, reproducible interoperability validation for CANopen on Linux
SocketCAN: established third-party implementations, packaged as containers
that behave the same way, so that a library can be tested against
implementations it does not own.

It is not another CANopen implementation and not a general protocol-testing
framework.

## R1 - Repository isolation

- R1.1 This repository does not import, build, link against or depend on any
  library under test, and contains no test logic or expectation specific to
  one. The one exception is `own/` with its workflow: images of OT Fabric's own
  stacks, published for others to test against (`own/go-canopen/README.md`). The
  rest of the repository does not know them, and they are not part of a release.
  CI fails when `adapters/`, `fixtures/`, `schemas/`, `scripts/`, `tests/` or
  the `Makefile` name go-canopen or canopenctl.
- R1.2 A consumer depends on the published images and the documented contract
  only. It does not need a checkout of this repository to run its tests.
- R1.3 No third-party code from here is copied into a consumer, and the
  contract does not shape a consumer's public API.

| Responsibility | Here | Consumer |
|----------------|:----:|:--------:|
| Third-party implementations, adapters, Dockerfiles | yes | no |
| EDS/DCF fixtures and their generator | yes | no |
| Container contract, capability declarations | yes | no |
| Image builds and publishing, licence compliance | yes | no |
| The self-test between the stacks (`scripts/selftest.py`) | yes | no |
| Scenarios and assertions about a library | no | yes |
| Compatibility claims and release qualification of a library | no | yes |

## R2 - Reference implementations

- R2.1 In scope: CANopenNode, Lely lely-core and python canopen
  ([versions.yaml](versions.yaml)).
- R2.2 Each implementation is its own image. No image contains two.
- R2.3 An adapter uses the implementation's public API or tools as an
  application would; it never builds or parses a CANopen frame itself. What a
  stack cannot do is declared `false` in its capabilities and answered
  `unsupported`.

## R3 - One contract

- R3.1 Every image has the same commands, flags, documents and exit codes
  ([docs/CONTAINER_CONTRACT.md](docs/CONTAINER_CONTRACT.md)); a smoke test
  checks each image on its own (`scripts/smoke.sh`).
- R3.2 Every wait is bounded. stdout carries the documents; diagnostics go
  to stderr.

## R4 - Honest results

- R4.1 A result is pass, fail, unsupported, not-tested or known-difference;
  only pass counts as passing. A known difference needs its evidence in
  [tests/known-differences.tsv](tests/known-differences.tsv), and a waiver
  that no longer reproduces fails the run.

## R5 - Reproducibility

- R5.1 Upstreams are pinned by commit and source hash, base images by
  digest; [versions.yaml](versions.yaml) mirrors every pin and
  `make validate` fails when it disagrees with the build files.
- R5.2 Images are built natively for linux/amd64 and linux/arm64 and tested
  on both, on a vcan interface, before they are published.

## R6 - Licensing

- R6.1 The repository's own code is MIT-licensed.
- R6.2 Every redistributed component is in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and `licenses/`; an image
  that may not be redistributed is not published (`published:` in
  versions.yaml).
