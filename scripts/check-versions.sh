#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# check-versions.sh: fail when versions.yaml disagrees with the build inputs
# (Dockerfiles, requirements.txt), the contract or the tools of the workflows
# and the Makefile, or when a build input is not pinned by hash: every
# implementation records source_sha256, every Python requirement carries
# --hash=sha256: and is installed with --require-hashes.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 - <<'PY'
import glob, os, re, sys

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

def logical_lines(path):
    """The lines of a file with backslash continuations joined."""
    lines, cur = [], ""
    for raw in open(path):
        raw = raw.rstrip("\r\n")
        if raw.endswith("\\"):
            cur += raw[:-1] + " "
            continue
        lines.append(cur + raw)
        cur = ""
    if cur:
        lines.append(cur)
    return lines

def requirements(path):
    """(spec, [sha256, ...]) for every requirement of a requirements file."""
    out = []
    for line in logical_lines(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append((line.split()[0], re.findall(r"--hash=sha256:([0-9a-f]{64})(?![0-9a-f])", line)))
    return out

def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()

v = parse("versions.yaml")
errors = []
def need(path, text, what):
    if text not in open(path).read():
        errors.append("%s: %s not found (%s)" % (path, text, what))

SHA256 = re.compile(r"^[0-9a-f]{64}$")
PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^=\s]+$")

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
    if not SHA256.match(impl.get("source_sha256", "")):
        errors.append("versions.yaml: %s has no source_sha256 (64 hex digits)" % name)
    if "package" in impl:
        need("adapters/%s/requirements.txt" % name, "%s==%s" % (impl["package"], impl["version"]), "package pin")
    req = "adapters/%s/requirements.txt" % name
    if os.path.exists(req):
        artefacts = []
        for spec, digests in requirements(req):
            if not PINNED.match(spec):
                errors.append("%s: %s is not pinned to one version" % (req, spec))
            if not digests:
                errors.append("%s: %s has no --hash=sha256:" % (req, spec))
            if "package" in impl and norm(spec.partition("==")[0]) == norm(impl["package"]):
                artefacts = digests
        if "package" in impl and impl.get("source_sha256") not in artefacts:
            errors.append("%s: source_sha256 of %s (versions.yaml) is not a hash of %s==%s"
                          % (req, name, impl["package"], impl["version"]))
        installs = [l for l in logical_lines(df) if "pip install" in l]
        if not installs:
            errors.append("%s: no pip install" % df)
        for l in installs:
            for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps"):
                if flag not in l.split():
                    errors.append("%s: pip install without %s" % (df, flag))
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

for name, impl in v.get("own_implementations", {}).items():
    df = impl["build"] + "/Dockerfile"
    for arg, base in (("GOLANG_DIGEST", "golang"), ("DEBIAN_DIGEST", "debian-trixie")):
        ref = bases[base]
        need(df, "ARG %s=%s" % (arg, ref.split("@")[1]), "base image " + base)
        need(df, "/" + ref.split("@")[0] + "@${%s}" % arg, "base image " + base)

# Tools: every pip/pipx install of the workflows and the Makefile names a
# version of versions.yaml tools (or requirements-dev.txt, which must), and
# every tool image they run is the pinned one.
tools = v.get("tools") or {}
if not tools:
    errors.append("versions.yaml: no tools")
packages = {norm(k): t for k, t in tools.items() if "@" not in t}
images = {t.split("@")[0].rsplit(":", 1)[0]: t for t in tools.values() if "@" in t}
for t in images.values():
    if not re.search(r"@sha256:[0-9a-f]{64}$", t):
        errors.append("versions.yaml: tool image %s is not pinned by digest" % t)
for spec, _ in requirements("requirements-dev.txt"):
    name, _, version = spec.partition("==")
    if not PINNED.match(spec) or packages.get(norm(name)) != version:
        errors.append("requirements-dev.txt: %s is not the version of versions.yaml tools" % spec)
for path in ["Makefile"] + sorted(glob.glob(".github/workflows/*.yml")):
    for line in logical_lines(path):
        for m in re.finditer(r"\bpipx? install\b(.*)", line):
            words = iter(m.group(1).replace("'", " ").replace('"', " ").split())
            for w in words:
                if w in ("&&", "||", ";", "|"):
                    break
                if w in ("-r", "--requirement"):
                    f = next(words, "")
                    if f.rsplit("/", 1)[-1] != "requirements-dev.txt":
                        errors.append("%s: pip install -r %s (want requirements-dev.txt)" % (path, f))
                    continue
                if w.startswith("-"):
                    continue
                name, _, version = w.partition("==")
                if not PINNED.match(w) or packages.get(norm(name)) != version:
                    errors.append("%s: %s is installed without the version of versions.yaml tools" % (path, w))
    text = open(path).read()
    for image, pinned in images.items():
        for ref in re.findall(r"(?:^|[\s/])(%s[:@][^\s'\"]+)" % re.escape(image), text, re.M):
            if ref != pinned:
                errors.append("%s: %s (versions.yaml tools: %s)" % (path, ref, pinned))
    for ref in re.findall(r"(?:\$\(TOOLS_REGISTRY\)|\$\{\{? *(?:env\.)?TOOLS_REGISTRY *\}?\})/library/([^\s'\"]+)", text):
        if ref not in bases.values():
            errors.append("%s: tool image library/%s is not in versions.yaml base_images" % (path, ref))

if errors:
    print("versions.yaml disagrees with the build inputs:")
    for e in errors:
        print("  " + e)
    sys.exit(1)
print("versions.yaml agrees with %d adapters, the own images, the tools and the contract" % len(v["implementations"]))
PY
