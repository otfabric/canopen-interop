#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Shared helpers for scripts/smoke.sh and scripts/interop.sh. Source, do not
# execute.
#
# The engine of the cross-stack test is scripts/selftest.py: every container
# runs in the host network namespace on a vcan interface.

REGISTRY="${REGISTRY:-ghcr.io/otfabric}"
VERSION="${VERSION:-dev}"
export REGISTRY VERSION

log() { echo "[$(basename "$0" .sh)] $*" >&2; }

# image_of <adapter>: ADAPTER_IMAGE_<adapter> (a hyphen written as "_") names an
# image outside the release set, which this repository's scripts know nothing
# about. scripts/selftest.py reads the same variables.
image_of() {
    local override="ADAPTER_IMAGE_${1//-/_}"
    if [[ -n "${!override:-}" ]]; then echo "${!override}"; else echo "${REGISTRY}/canopen-interop-$1:${VERSION}"; fi
}

require_tools() {
    local t docker
    read -r -a docker <<< "${DOCKER:-docker}"
    for t in "${docker[0]}" python3; do
        command -v "$t" >/dev/null || { log "missing required tool: $t"; exit 2; }
    done
}
