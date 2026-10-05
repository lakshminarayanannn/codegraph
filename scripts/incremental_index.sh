#!/usr/bin/env bash
# Incremental update (SPEC 8.3): re-extract changed projects/modules and patch the graph.
#   scripts/incremental_index.sh <codegraph.yaml> [--dry-run]
# Windows: scripts/incremental_index.ps1.
set -euo pipefail

CONFIG="${1:?usage: scripts/incremental_index.sh <codegraph.yaml> [--dry-run]}"
shift
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${CODEGRAPH_PYTHON:-$HERE/.venv/bin/python}"
DOTNET="${DOTNET:-dotnet}"

"$DOTNET" build "$HERE/extractors/CodeGraph.sln" -c Release -v:quiet -nologo
"$PY" -m loader incremental --config "$CONFIG" "$@"
