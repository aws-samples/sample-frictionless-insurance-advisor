#!/usr/bin/env bash
# Copies the sample policy documents from `s3-data/mock-policies/` into
# `react-frontend/public/mock-policies/` so Vite picks them up as static
# assets and they end up at /mock-policies/<file> on CloudFront.
#
# Run automatically as `prebuild` and on `npm run dev`. Safe to run anytime.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO_ROOT/s3-data/mock-policies"
DST="$REPO_ROOT/react-frontend/public/mock-policies"

if [ ! -d "$SRC" ]; then
  echo "❌  Source folder not found: $SRC" >&2
  exit 1
fi

mkdir -p "$DST"

# Sync only the files an end user should download (md + pdf).
# Strip macOS metadata if present.
shopt -s nullglob
copied=0
for src_file in "$SRC"/*.md "$SRC"/*.pdf; do
  cp -f "$src_file" "$DST/"
  copied=$((copied + 1))
done

# Drop any orphan files in dst that no longer exist in src (so deletions
# from s3-data propagate to the published bundle).
for dst_file in "$DST"/*.md "$DST"/*.pdf; do
  [ -e "$dst_file" ] || continue
  basename="$(basename "$dst_file")"
  if [ ! -e "$SRC/$basename" ]; then
    rm -f "$dst_file"
  fi
done

echo "📎  Synced $copied mock policy file(s) → react-frontend/public/mock-policies/"
