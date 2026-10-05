#!/usr/bin/env bash
# One-time setup on macOS / Linux: .NET 10 SDK, Python environment, Neo4j, passwords, extractor build.
# Idempotent: re-running skips what is already there.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

# 1. .NET 10 SDK (user-local install when no dotnet is on PATH)
if command -v dotnet >/dev/null 2>&1 && dotnet --list-sdks | grep -q '^10\.'; then
  DOTNET=dotnet
else
  if [ ! -x "$HOME/.dotnet/dotnet" ] || ! "$HOME/.dotnet/dotnet" --list-sdks | grep -q '^10\.'; then
    echo "== installing .NET 10 SDK into ~/.dotnet"
    curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
    bash /tmp/dotnet-install.sh --channel 10.0 --install-dir "$HOME/.dotnet"
  fi
  DOTNET="$HOME/.dotnet/dotnet"
  echo "   add to your shell profile: export DOTNET_ROOT=\$HOME/.dotnet PATH=\$HOME/.dotnet:\$PATH"
fi

# 2. Python environment
if [ ! -x .venv/bin/python ]; then
  echo "== creating .venv"
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[fast,test]"

# 3. Passwords (git-ignored .env, read by every Python tool)
if [ ! -f .env ]; then
  python3 - <<'EOF'
import secrets
with open(".env", "w") as f:
    f.write("# Local secrets for codegraph (git-ignored).\n")
    f.write(f"CODEGRAPH_NEO4J_PASSWORD=cg-{secrets.token_urlsafe(12)}\n")
    f.write(f"CODEGRAPH_NEO4J_RO_PASSWORD=ro-{secrets.token_urlsafe(12)}\n")
EOF
  chmod 600 .env
  echo "== wrote .env with generated Neo4j passwords"
fi
# shellcheck disable=SC1091
set -a; . ./.env; set +a

# 4. Neo4j (Homebrew on macOS; elsewhere print instructions)
if ! command -v neo4j-admin >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "== installing Neo4j (Homebrew)"
    HOMEBREW_NO_AUTO_UPDATE=1 brew install neo4j
    neo4j-admin dbms set-initial-password "$CODEGRAPH_NEO4J_PASSWORD"
  else
    echo "!! Neo4j not found: install Neo4j Community 5.26+ (Java 21), then run"
    echo "   neo4j-admin dbms set-initial-password \"\$CODEGRAPH_NEO4J_PASSWORD\" && neo4j start"
  fi
fi
if command -v neo4j >/dev/null 2>&1; then
  neo4j status >/dev/null 2>&1 || neo4j start
fi

# 5. Build the extractors
echo "== building extractors"
"$DOTNET" build extractors/CodeGraph.sln -c Release -v:quiet -nologo

echo
echo "Ready. Try the fixture:  scripts/build_fixture.sh && scripts/full_index.sh fixtures/MiniPlant/codegraph.yaml"
echo "Then register the MCP server:"
echo "  claude mcp add codegraph -- $HERE/.venv/bin/python -m mcp_server --config $HERE/fixtures/MiniPlant/codegraph.yaml"
