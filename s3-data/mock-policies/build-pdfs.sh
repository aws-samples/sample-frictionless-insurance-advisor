#!/usr/bin/env bash
# Generate PDF versions of every Markdown sample policy in this folder.
# Pure-Python pipeline (no system deps); works on macOS, Linux, and Windows.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"

# markdown and fpdf2 come from the project's locked dev group (pyproject.toml, uv.lock):
# exact versions and file hashes, nothing is resolved at run time.
uv run --frozen --project "$ROOT" python "$DIR/build_pdfs.py"
