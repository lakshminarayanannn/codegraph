"""Logging to stderr only: stdout belongs to command output (and to the MCP protocol in the server)."""
from __future__ import annotations

import sys
import time


def log(component: str, message: str) -> None:
    print(f"[{component} {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)
