#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Runs a command and repeats it when it failed for a reason that is not ours:
# a registry or network that is overloaded, rate limiting or timing out.
#
# Usage: retry.sh <command> [args...]
#
# A failure whose output shows none of the transient signatures below is
# returned at once: a broken build must not be built four times. stdout and
# stderr of the command pass through unchanged.
#
# Environment: RETRY_ATTEMPTS (default 5), RETRY_DELAY seconds before the
#              second attempt (default 10; doubles each time, at most 120).

set -uo pipefail

[[ $# -ge 1 ]] || { echo "usage: retry.sh <command> [args...]" >&2; exit 2; }

attempts="${RETRY_ATTEMPTS:-5}"
delay="${RETRY_DELAY:-10}"

# What registries, proxies and resolvers say when the problem is theirs.
transient='toomanyrequests|Too Many Requests|rate limit|Rate exceeded'
transient+='|50[0-9] (Internal Server Error|Bad Gateway|Service Unavailable|Gateway Time-?out)'
transient+='|error code: 5[0-9][0-9]|status code:? 5[0-9][0-9]|unexpected status.* 5[0-9][0-9]'
transient+='|context deadline exceeded|Client\.Timeout exceeded|TLS handshake timeout|i/o timeout'
transient+='|connection reset by peer|connection refused|unexpected EOF|broken pipe'
transient+='|failed to fetch oauth token|failed to authorize|failed to do request'
transient+='|[Tt]emporary failure in name resolution|no such host|server misbehaving'
transient+='|net/http: request canceled|TLS handshake|could not resolve host|Could not resolve host'
# The same from inside a build: apt, git, Maven and the Go module proxy.
transient+='|Temporary failure resolving|Could not connect to|Connection timed out|Connection reset'
transient+='|Failed to fetch http|Could not transfer artifact|Transfer failed|proxy\.golang\.org.*(timeout|5[0-9][0-9])'
transient+='|The requested URL returned error: (429|5[0-9][0-9])|RPC failed|early EOF'

log=$(mktemp)
trap 'rm -f "${log}"' EXIT

attempt=1
while true; do
    : > "${log}"
    # Keep the two streams apart for the caller and record both.
    "$@" > >(tee -a "${log}") 2> >(tee -a "${log}" >&2)
    code=$?
    # Let the two tee processes finish writing.
    wait 2>/dev/null
    [[ "${code}" -eq 0 ]] && exit 0

    if ! grep -qE "${transient}" "${log}"; then
        exit "${code}"
    fi
    if (( attempt >= attempts )); then
        echo "[retry] still failing after ${attempts} attempts: $*" >&2
        exit "${code}"
    fi
    echo "[retry] attempt ${attempt} of ${attempts} failed on what looks like a registry or network problem;" \
         "again in ${delay}s: $1 ${2:-} ..." >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$(( delay * 2 > 120 ? 120 : delay * 2 ))
done
