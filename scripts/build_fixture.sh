#!/usr/bin/env bash
# Builds the MiniPlant fixture on machines without Visual Studio (macOS/Linux):
#   * the C# projects, with the C# stand-in for the C++/CLI wrapper,
#   * rewrites the stand-in into the C++/CLI IJW metadata shape (tests/tools/SimIjwRewriter),
#   * writes a compilation database for the native C++ project from its .vcxproj.
# On Windows use scripts/build_fixture.ps1, which builds the real C++/CLI and native projects with MSBuild.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="${1:-$HERE/fixtures/MiniPlant}"
PY="${CODEGRAPH_PYTHON:-$HERE/.venv/bin/python}"
DOTNET="${DOTNET:-dotnet}"

"$DOTNET" build "$FIXTURE/MiniPlant.Managed.slnf" -c Debug -p:Platform=x64 -v:quiet -nologo
"$DOTNET" build "$HERE/tests/tools/SimIjwRewriter/SimIjwRewriter.csproj" -c Release -v:quiet -nologo
"$DOTNET" "$HERE/tests/tools/SimIjwRewriter/bin/Release/net10.0/SimIjwRewriter.dll" "$FIXTURE/bin/MiniPlant.Interop.dll"
"$PY" -m codegraph_native.vcxproj_compdb --config "$FIXTURE/codegraph.yaml"
