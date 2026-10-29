#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# ghcr-login.sh USER TOKEN_FILE: log in to ghcr.io with the token in
# TOKEN_FILE. A command of its own, so that scripts/retry.sh can repeat it
# with the token read again on every attempt.
set -euo pipefail
exec docker login ghcr.io --username "$1" --password-stdin < "$2"
