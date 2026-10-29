#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# check-versions.sh: fail when versions.yaml disagrees with the build inputs
# (Dockerfiles, requirements.txt) or the contract.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 - <<'PY'
import re, sys

def parse(path):
    """The subset of YAML versions.yaml uses: nested maps by indentation, scalars, [lists]."""
    root, stack = {}, [(-1, None)]
    stack[0] = (-1, root)
    for raw in open(path):
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        value = value.strip()
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = value.strip('"')
    return root

v = parse("versions.yaml")
errors = []
def need(path, text, what):
    if text not in open(path).read():
        errors.append("%s: %s not found (%s)" % (path, text, what))

contract = open("docs/CONTAINER_CONTRACT.md").read()
if "Version: **%s**" % v["contract_version"] not in contract:
    errors.append("docs/CONTAINER_CONTRACT.md does not say Version: **%s**" % v["contract_version"])

registries = {"BASE_REGISTRY": "", "DOTNET_REGISTRY": "mcr.microsoft.com/"}
bases = v["base_images"]
for name, impl in v["implementations"].items():
    df = "adapters/%s/Dockerfile" % name
    text = open(df).read()
    need(df, "UPSTREAM_REVISION=" + impl["revision"], "revision")
    for reg, ref in re.findall(r"FROM \$\{(\w+)\}/(\S+)", text):
        full = registries.get(reg, "") + ref
        if full not in bases.values():
            errors.append("%s: base image %s is not in versions.yaml base_images" % (df, full))
    if "package" in impl:
        need("adapters/%s/requirements.txt" % name, "%s==%s" % (impl["package"], impl["version"]), "package pin")
    if name == "canopennode":
        need(df, "CANOPENNODE_VERSION=" + impl["version"], "version")
        need(df, "UPSTREAM_VERSION=" + impl["version"], "version")
        need(df, "CANOPENNODE_SHA256=" + impl["source_sha256"], "source hash")
        need(df, "CANOPENLINUX_REVISION=" + impl["canopenlinux_revision"], "CANopenLinux revision")
        need(df, "CANOPENLINUX_SHA256=" + impl["canopenlinux_sha256"], "CANopenLinux hash")
        need(df, "CANOPENEDITOR_VERSION=" + impl["canopeneditor_version"], "CANopenEditor version")
        need(df, "CANOPENEDITOR_SHA256=" + impl["canopeneditor_sha256"], "CANopenEditor hash")
    if name == "lely":
        need(df, "LELY_VERSION=" + impl["version"], "version")
        need(df, "UPSTREAM_VERSION=" + impl["version"], "version")
        need(df, "LELY_SHA256=" + impl["source_sha256"], "source hash")

if errors:
    print("versions.yaml disagrees with the build inputs:")
    for e in errors:
        print("  " + e)
    sys.exit(1)
print("versions.yaml agrees with %d adapters and the contract" % len(v["implementations"]))
PY
