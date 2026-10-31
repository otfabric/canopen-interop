#!/usr/bin/env bash
# Print the next vX.Y.Z tag.
#
#   next-version.sh [patch|minor|major] [current]
#
# current defaults to the highest existing vX.Y.Z git tag. patch is the
# default bump. Fails when current is missing or not vX.Y.Z.
set -euo pipefail
bump="${1:-patch}"
current="${2:-}"

case "${bump}" in
  patch|minor|major) ;;
  *) echo "next-version: bump must be patch, minor or major (got ${bump})" >&2; exit 2 ;;
esac

if [[ -z "${current}" ]]; then
  current="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1 || true)"
fi
if [[ ! "${current}" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "next-version: no current vX.Y.Z tag${current:+ (got ${current})}" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
case "${bump}" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
esac
printf 'v%s.%s.%s\n' "${major}" "${minor}" "${patch}"
