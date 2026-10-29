#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# release-notes.sh vX.Y.Z: print the "## vX.Y.Z" section of RELEASE.md, up to
# its closing "---" line: the body of the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:?usage: release-notes.sh vX.Y.Z}"
notes=$(awk -v h="## ${version}" '
    $0 == h { found = 1; next }
    found && $0 == "---" { exit }
    found { print }
' RELEASE.md)
if [[ -z "${notes//[[:space:]]/}" ]]; then
    echo "release-notes: RELEASE.md has no \"## ${version}\" section closed by ---" >&2
    exit 1
fi
printf '%s\n' "$notes"
