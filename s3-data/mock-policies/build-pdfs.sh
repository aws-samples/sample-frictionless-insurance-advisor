#!/usr/bin/env bash
# Generate PDF versions of every Markdown sample policy in this folder.
# Pure-Python pipeline (no system deps); works on macOS, Linux, and Windows.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

uv run --with markdown --with fpdf2 \
    python "$DIR/build_pdfs.py"
