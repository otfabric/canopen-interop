#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# reference-images.sh <manifest.json> <adapter...>: the image of each adapter
# by the digest the manifest.json of a release names, one
# ADAPTER_IMAGE_<adapter>=<image>@sha256:<digest> line per adapter (for
# $GITHUB_ENV; scripts/lib.sh and scripts/selftest.py read these variables,
# a hyphen of the adapter written as "_").
# Fails when an adapter is missing or its image is not pinned by digest, so a
# tag pushed again after the release cannot change what runs.
#
# Environment: REGISTRY (default ghcr.io/otfabric).
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: reference-images.sh <manifest.json> <adapter...>" >&2; exit 2; }
exec python3 -I - "$@" <<'PY'
import json, os, re, sys

manifest, adapters = sys.argv[1], sys.argv[2:]
registry = os.environ.get("REGISTRY") or "ghcr.io/otfabric"
with open(manifest) as f:
    images = json.load(f).get("images") or {}
failed = False
for a in adapters:
    ref = images.get(a)
    want = r"^%s/canopen-interop-%s@sha256:[0-9a-f]{64}$" % (re.escape(registry), re.escape(a))
    if not isinstance(ref, str) or not re.match(want, ref):
        print("reference-images.sh: %s: %s in %s is not %s/canopen-interop-%s@sha256:<digest>"
              % (a, json.dumps(ref), manifest, registry, a), file=sys.stderr)
        failed = True
        continue
    print("ADAPTER_IMAGE_%s=%s" % (a.replace("-", "_"), ref))
sys.exit(1 if failed else 0)
PY
