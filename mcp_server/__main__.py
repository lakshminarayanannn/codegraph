"""python -m mcp_server --config <codegraph.yaml>   (stdio transport, for Claude Code)

Register with:  claude mcp add codegraph -- <python> -m mcp_server --config <path to codegraph.yaml>
"""
from __future__ import annotations

import argparse
import sys

from codegraph_common.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_server")
    parser.add_argument("--config", required=True)
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args = parser.parse_args()

    from .server import build_server

    server = build_server(Config(args.config))
    print("[mcp_server] codegraph MCP server ready", file=sys.stderr, flush=True)
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
