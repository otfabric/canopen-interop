#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare the pins of versions.yaml with the latest upstream releases.

For every implementation: the newest PyPI release (`package`), the newest
version tag of its GitHub `repository` (CANopenNode publishes tags, not
GitHub releases) or the newest GitLab release, and for canopennode also the CANopenLinux master
commit and the newest CANopenEditor tag; for every base image: the digest its
tag points to now (with --images, through `docker buildx imagetools
inspect`). Writes a Markdown table to stdout (or --output) and, under GitHub
Actions, `behind=<n>` to $GITHUB_OUTPUT. With --rows, also one JSON row per
pin in the format of otfabric/.github/actions/upstream-row (the Upstream
workflow checks the base images with otfabric/.github/actions/upstream-images
instead of --images). Never changes a pin: bumping is a reviewed change.

    scripts/latest-upstream.py [--images] [--output table.md] [--rows DIR]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "canopen-interop-upstream-check",
                                               "Accept": "application/json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse_versions():
    """A small reader of versions.yaml (no PyYAML needed): the fields used here."""
    impls, images, cur = {}, {}, None
    section = None
    for line in open(os.path.join(ROOT, "versions.yaml")):
        line = line.split(" #")[0].rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            section = line.rstrip(":")
            continue
        m = re.match(r"^  (\w[\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip().strip('"')
            if section == "implementations" and not val:
                cur = impls.setdefault(key, {})
            elif section == "base_images":
                images[key] = val
            continue
        m = re.match(r"^    (\w+):\s*(.*)$", line)
        if m and section == "implementations" and cur is not None:
            cur[m.group(1)] = m.group(2).strip().strip('"')
    return impls, images


def norm(v):
    return v.lstrip("v")


def vkey(tag):
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def newest_tag(repository):
    if "gitlab.com" in repository:
        # GitLab releases, newest first (lely-core's v3.x tags are an
        # abandoned 2021 line; its releases continue on v2.3.x).
        path = urllib.parse.quote(repository.replace("https://gitlab.com/", ""), safe="")
        return get_json("https://gitlab.com/api/v4/projects/%s/releases?per_page=1" % path)[0]["tag_name"]
    else:
        repo = repository.replace("https://github.com/", "")
        tags = [t["name"] for t in get_json("https://api.github.com/repos/%s/tags?per_page=100" % repo)]
    tags = [t for t in tags if re.fullmatch(r"v?\d+(\.\d+)*", t)]
    return max(tags, key=vkey)


def latest_of(impl):
    if impl.get("package"):
        return get_json("https://pypi.org/pypi/%s/json" % impl["package"])["info"]["version"], "PyPI"
    return newest_tag(impl["repository"]), "release" if "gitlab.com" in impl["repository"] else "tag"


def head_of(repository, branch="master"):
    repo = repository.replace("https://github.com/", "")
    return get_json("https://api.github.com/repos/%s/commits/%s" % (repo, branch))["sha"]


def image_digest(ref):
    name = ref.split("@")[0]
    mirror = name if name.startswith("mcr.microsoft.com/") else \
        os.environ.get("BASE_REGISTRY", "docker.io/library") + "/" + name
    out = subprocess.run(["docker", "buildx", "imagetools", "inspect", mirror, "--format", "{{json .Manifest}}"],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-200:])
    return json.loads(out.stdout)["digest"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", action="store_true", help="also check the base image digests (needs docker)")
    ap.add_argument("--output")
    ap.add_argument("--rows", help="write one JSON row per pin into this directory")
    args = ap.parse_args()
    impls, images = parse_versions()
    rows, records, behind = [], [], 0

    def record(name, kind, source, pinned, latest, status, detail=""):
        records.append({"component": name, "kind": kind, "source": source, "pinned": pinned,
                        "latest": latest, "status": status, "detail": detail, "test_outcome": "",
                        "test_result": "", "test_cached": False, "known": ""})

    def compare(name, pinned, fetch, same=lambda a, b: norm(a) == norm(b), kind="library"):
        nonlocal behind
        try:
            latest, source = fetch()
        except Exception as e:  # report, do not fail the whole check
            rows.append((name, pinned, "?", "error: %s" % e))
            record(name, kind, "", pinned, "", "error", str(e)[-160:])
            return
        ok = same(latest, pinned)
        if not ok:
            behind += 1
        rows.append((name, pinned, latest, "up to date" if ok else "behind (%s)" % source))
        record(name, kind, source, pinned, latest, "current" if ok else "behind")

    for name, impl in sorted(impls.items()):
        compare(name, impl.get("version", ""), lambda impl=impl: latest_of(impl))
        if impl.get("canopenlinux_revision"):
            compare("canopennode: CANopenLinux master", impl["canopenlinux_revision"][:12],
                    lambda impl=impl: (head_of(impl["canopenlinux_repository"])[:12], "master commit"),
                    lambda a, b: a == b, kind="commit")
        if impl.get("canopeneditor_version"):
            compare("canopennode: CANopenEditor", impl["canopeneditor_version"],
                    lambda impl=impl: (newest_tag(impl["canopeneditor_repository"]), "tag"), kind="tool")
    if args.images:
        for name, ref in sorted(images.items()):
            pinned = ref.split("@")[-1][:19] + "…"
            try:
                d = image_digest(ref)
            except Exception as e:
                rows.append(("base image " + name, pinned, "?", "error: %s" % e))
                continue
            current = ref.split("@")[-1] == d
            if not current:
                behind += 1
            rows.append(("base image " + ref.split("@")[0], pinned, d[:19] + "…",
                         "up to date" if current else "tag moved (rebuild with the new digest)"))
    table = "| Component | Pinned | Latest | Status |\n|---|---|---|---|\n" + \
        "".join("| %s | %s | %s | %s |\n" % r for r in rows)
    text = "%d pin(s) behind the latest upstream.\n\n%s" % (behind, table)
    if args.rows:
        os.makedirs(args.rows, exist_ok=True)
        for r in records:
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", r["component"]).strip("-").lower()
            with open(os.path.join(args.rows, "row-%s.json" % name), "w") as f:
                json.dump(r, f)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
    sys.stdout.write(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("behind=%d\n" % behind)


if __name__ == "__main__":
    main()
