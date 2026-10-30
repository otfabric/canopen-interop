#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Writes and judges interop-result-v1 documents (results.json).

The schema is schemas/interop-result-v1.schema.json, copied from
otfabric/engineering. Only the outcome "pass" counts as passing. A run fails
on a fail or infra-error (when a gate is set, only one that involves the gate),
on a waiver that no longer reproduces, and on fewer passes than the floor: an
all-skipped run is never green.

    interop_result.py <results.json> [--floor N]

checks a document against the schema (when the jsonschema package is
installed) and the rules above; the exit status is 1 when the run failed.
"""

import datetime
import json
import os
import platform
import re
import subprocess
import sys

OUTCOMES = ("pass", "fail", "unsupported", "not-tested", "known-difference", "infra-error")
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas", "interop-result-v1.schema.json")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def version_field(root, key):
    """A top-level "key: value" of versions.yaml, or None."""
    with open(os.path.join(root, "versions.yaml")) as f:
        m = re.search(r'^%s: "([^"]+)"' % re.escape(key), f.read(), re.M)
    return m.group(1) if m else None


def git_sha(root):
    p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "0000000"


def host():
    h = {"os": platform.system().lower(), "arch": platform.machine(), "kernel": platform.release()}
    if os.environ.get("RUNNER_NAME"):
        h["runner"] = os.environ["RUNNER_NAME"]
    return h


def repo_digest(image, docker=("docker",)):
    """The image@sha256 reference of a pulled image; None for a local build,
    which has no registry digest."""
    p = subprocess.run(list(docker) + ["image", "inspect", "--format", "{{json .RepoDigests}}", image],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    digests = json.loads(p.stdout or "[]")
    return digests[0] if digests else None


def summarize(results):
    summary = {o: 0 for o in OUTCOMES}
    for r in results:
        summary[r["outcome"]] += 1
    return summary


def problems(doc, floor=1):
    """Why the run failed; an empty list when it passed."""
    gate = doc.get("gate")
    out = []
    for r in doc["results"]:
        if r["outcome"] in ("fail", "infra-error") and (not gate or gate in r["roles"].values()):
            out.append("%s %s (%s): %s" % (r["outcome"], r["id"], ", ".join(r["roles"].values()), r.get("reason", "")[:200]))
    for w in doc.get("waivers", []):
        if not w["reproduced"]:
            out.append("stale waiver %s: it no longer reproduces; remove it" % w["id"])
    passes = sum(1 for r in doc["results"] if r["outcome"] == "pass")
    if passes < floor:
        out.append("%d passing results, the floor is %d: this run proves nothing" % (passes, floor))
    return out


def write(path, doc):
    """Fills in the summary and writes the document."""
    doc["summary"] = summarize(doc["results"])
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")


def schema_errors(doc):
    """Schema violations; empty without the jsonschema package."""
    try:
        import jsonschema
    except ImportError:
        return []
    with open(SCHEMA) as f:
        schema = json.load(f)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return ["%s: %s" % ("/".join(str(p) for p in e.absolute_path), e.message) for e in validator.iter_errors(doc)]


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    floor = int(argv[argv.index("--floor") + 1]) if "--floor" in argv else 1
    with open(argv[0]) as f:
        doc = json.load(f)
    bad = ["schema: " + e for e in schema_errors(doc)] + problems(doc, floor)
    print("%s: %s" % (argv[0], ", ".join("%d %s" % (doc["summary"][o], o) for o in OUTCOMES)))
    for p in bad:
        print("FAIL " + p, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
