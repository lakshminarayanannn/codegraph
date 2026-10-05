#!/usr/bin/env bash
# Full rebuild (SPEC 15): extract everything -> merge -> import -> derive -> verify counts.
#   scripts/full_index.sh <codegraph.yaml> [--allow-compile-errors] [--allow-missing-bridge]
# Windows: scripts/full_index.ps1 (same steps).
set -euo pipefail

CONFIG="${1:?usage: scripts/full_index.sh <codegraph.yaml> [--allow-compile-errors] [--allow-missing-bridge]}"
shift
ALLOW_COMPILE_ERRORS=0
LOADER_FLAGS=()
for arg in "$@"; do
  case "$arg" in
    --allow-compile-errors) ALLOW_COMPILE_ERRORS=1 ;;
    --allow-missing-bridge) LOADER_FLAGS+=("--allow-missing-bridge") ;;
    *) echo "unknown option $arg" >&2; exit 64 ;;
  esac
done

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${CODEGRAPH_PYTHON:-$HERE/.venv/bin/python}"
DOTNET="${DOTNET:-dotnet}"
BIN="$HERE/extractors"
start=$(date +%s)

echo "== 1/6 build extractors"
"$DOTNET" build "$BIN/CodeGraph.sln" -c Release -v:quiet -nologo

echo "== 2/6 C# (Roslyn)"
set +e
"$DOTNET" "$BIN/CodeGraph.Roslyn/bin/Release/net10.0/CodeGraph.Roslyn.dll" --config "$CONFIG"
code=$?
set -e
if [ $code -eq 3 ] && [ $ALLOW_COMPILE_ERRORS -eq 0 ]; then
  echo "compile-error gate failed (see out/csharp/manifest.json); rerun with --allow-compile-errors to continue" >&2
  exit 3
elif [ $code -ne 0 ] && [ $code -ne 3 ]; then
  exit $code
fi

echo "== 3/6 native C++ (libclang)"
COMPDB="$("$PY" -c "import sys; from codegraph_common.config import Config; c=Config(sys.argv[1]); print(c.resolve(c.native['compdb']))" "$CONFIG")"
if [ -f "$COMPDB" ]; then
  "$PY" -m codegraph_native --config "$CONFIG"
else
  echo "   no compilation database at $COMPDB: native code skipped (generate one: extractors/native/make_compdb.ps1)"
fi

echo "== 4/6 C++/CLI bridge"
"$DOTNET" "$BIN/CodeGraph.Bridge/bin/Release/net10.0/CodeGraph.Bridge.dll" --config "$CONFIG"

echo "== 5/6 merge + import + derive"
"$PY" -m loader full --config "$CONFIG" ${LOADER_FLAGS[@]+"${LOADER_FLAGS[@]}"}
"$PY" -m loader users --config "$CONFIG"

echo "== 6/6 verify"
"$PY" -m loader status --config "$CONFIG"
echo "full index finished in $(( $(date +%s) - start ))s"
