#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# smoke.sh [adapter...]: the contract checks that need no CANopen peer. For
# every image: print-capabilities and print-version documents, print-fixture,
# exit 2 with an error document for an unsupported operation (when the image
# has one) and for a usage error, exit 4 for a missing interface, eds-check of
# the shared EDS and a concise DCF (when supported). Documents go to $REPORTS/smoke (validate
# with validate-documents.py).
#
# Environment: DOCKER (default docker), IMAGE_TEMPLATE (as selftest.py),
#              REPORTS (default reports).
set -euo pipefail
cd "$(dirname "$0")/.."
read -r -a DOCKER <<< "${DOCKER:-docker}"
DEFAULT_TEMPLATE='ghcr.io/otfabric/canopen-interop-{adapter}:dev'
TEMPLATE="${IMAGE_TEMPLATE:-$DEFAULT_TEMPLATE}"
OUT="${REPORTS:-reports}/smoke"
mkdir -p "$OUT"
[[ $# -gt 0 ]] || set -- canopennode lely pythoncanopen
fail=0
expect() { # expect <exit> <code> <file> <description>
    if [[ $code -ne $1 ]] || ! grep -q "\"code\":\"$2\"" "$3"; then
        echo "FAIL $a: $4 should exit $1 with code $2 (exit $code)"; fail=1
    fi
}
for a in "$@"; do
    img=$(printf %s "$TEMPLATE" | sed "s/{adapter}/$a/")
    run() { "${DOCKER[@]}" run --rm --network none "$img" "$@"; }
    run print-capabilities > "$OUT/$a.capabilities.json"
    run print-version > "$OUT/$a.version.json"
    if ! run print-fixture eds/interop-device.eds | cmp -s - fixtures/eds/interop-device.eds; then
        echo "FAIL $a: print-fixture differs from fixtures/eds/interop-device.eds"; fail=1
    fi
    ops=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["operations"]))' "$OUT/$a.capabilities.json")
    for op in device sdo-upload sdo-download nmt listen pdo-configure pdo-receive sync eds-check cia402 \
              lss rpdo-send mpdo-send mpdo-receive boot cdcf; do
        [[ " $ops " == *" $op "* ]] && continue
        set +e; run "$op" > "$OUT/$a.unsupported.jsonl" 2>/dev/null; code=$?; set -e
        expect 2 unsupported "$OUT/$a.unsupported.jsonl" "unsupported operation $op"
        break
    done
    set +e; run sdo-upload --no-such-flag > "$OUT/$a.usage.jsonl" 2>/dev/null; code=$?; set -e
    expect 2 usage "$OUT/$a.usage.jsonl" "an unknown flag"
    set +e; run sdo-upload --interface nosuchcan0 --node-id 5 --index 0x1000 > "$OUT/$a.interface.jsonl" 2>/dev/null; code=$?; set -e
    expect 4 interface "$OUT/$a.interface.jsonl" "a missing interface"
    if [[ " $ops " == *" eds-check "* ]]; then
        set +e; run eds-check --eds /fixtures/eds/interop-device.eds > "$OUT/$a.eds-check.jsonl" 2>/dev/null; code=$?; set -e
        if [[ $code -ne 0 ]] || ! grep -q '"valid":true' "$OUT/$a.eds-check.jsonl"; then
            echo "FAIL $a: eds-check of the shared EDS (exit $code)"; fail=1
        fi
    fi
    if [[ " $ops " == *" cdcf "* ]]; then
        set +e; run cdcf --dcf /fixtures/eds/interop-device-node5.dcf --node-id 5 --generator objects \
            --range 0x1017-0x1017 > "$OUT/$a.cdcf.jsonl" 2>/dev/null; code=$?; set -e
        # one entry: 0x1017:00, 2 bytes, 1000 (the DCF's ParameterValue)
        if [[ $code -ne 0 ]] || ! grep -q '"data":"0100000017100002000000e803"' "$OUT/$a.cdcf.jsonl"; then
            echo "FAIL $a: cdcf of 0x1017 of interop-device-node5.dcf (exit $code)"; fail=1
        fi
    fi
    echo "ok   $a: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["upstream"], d["version"])' "$OUT/$a.version.json") [$ops]"
done
exit $fail
