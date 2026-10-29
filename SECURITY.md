# Security

## Reporting

Report vulnerabilities privately through GitHub's security advisories of
this repository ("Report a vulnerability"), not in public issues. Problems
of an upstream stack belong to that project; tell us too when it affects the
images.

## The images

* Test tools: run them only against virtual CAN or a bench bus. They send
  whatever SDO writes and NMT commands they are given (a lely `listen` or
  `nmt` even resets every node of the network); the contract has no safety
  interlock. **Never point them at a vehicle, machine or plant bus.**
* They need `--network host` (CAN interfaces live in a network namespace) but
  no added capabilities: CAN raw sockets are unprivileged and the images run
  as an unprivileged user. Do not run them `--privileged`.
* Base images and every upstream package are pinned (digest, version,
  checksum); `scripts/check-versions.sh` keeps
  [versions.yaml](versions.yaml) in step with them.
* Adapters read fixtures and input files given on the command line and
  never execute input. JSON input is parsed with the language's standard
  parser; EDS files are parsed by the upstream stacks.
