#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Cross-stack test: the suites of scripts/selftest.py between the named
# adapters, each in the roles its capabilities declare, on a vcan interface
# (Linux; make vcan first).
#
# Usage: interop.sh <adapter> [<adapter> ...]      e.g. interop.sh canopennode lely pythoncanopen
#
# tests/known-differences.tsv lists the deviations of specific stacks; a case
# it explains is a known difference, never a pass. An adapter whose
# capabilities say upstream.independentEngine false is recorded as not
# independent in results.json.
#
# This tests the adapters against each other. It says nothing about any
# library that consumes the images.
#
# Environment: REGISTRY, VERSION (image tag, default dev), ADAPTER_IMAGE_<adapter>,
#              VCAN (default vcan0), DOCKER (default docker), SUITES (space-separated,
#              default every suite), REPORT_DIR (default reports).

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

[[ $# -ge 1 ]] || { echo "usage: interop.sh <adapter> [<adapter> ...]" >&2; exit 2; }
require_tools
for adapter in "$@"; do
    export "ADAPTER_IMAGE_${adapter//-/_}=$(image_of "${adapter}")"
done
export ADAPTERS="$*"
log "adapters: $* (${VERSION}) on ${VCAN:-vcan0}"
exec python3 "${SCRIPT_DIR}/selftest.py" --reports "${REPORT_DIR:-reports}"
