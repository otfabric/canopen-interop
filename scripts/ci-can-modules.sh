#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# ci-can-modules.sh: make the CAN modules loadable on a GitHub-hosted Ubuntu
# runner: can, can-raw and vcan. They are in the kernel's linux-modules-extra
# package, which runners do not install.
set -euo pipefail

kernel=$(uname -r)
if ! sudo modprobe -a can can-raw vcan 2>/dev/null; then
    echo "Installing linux-modules-extra-${kernel}"
    sudo apt-get update -q
    sudo apt-get install -y -q "linux-modules-extra-${kernel}"
    sudo modprobe -a can can-raw vcan
fi
echo "can, can-raw, vcan loaded on ${kernel}"
