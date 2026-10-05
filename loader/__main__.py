"""Loader command line.

  python -m loader full        --config <yaml> [--method admin|bolt] [--allow-missing-bridge] [--skip-git]
  python -m loader incremental --config <yaml> [--dry-run] [--skip-extract]
  python -m loader status      --config <yaml>
  python -m loader dump        --config <yaml> --out <file.json>
  python -m loader users       --config <yaml>      create the read-only account for the MCP server
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codegraph_common.config import Config
from codegraph_common.log import log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m loader", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["full", "incremental", "status", "dump", "users"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--method", choices=["admin", "bolt"])
    parser.add_argument("--allow-missing-bridge", action="store_true",
                        help="warn instead of failing when C# references C++/CLI symbols the bridge did not produce")
    parser.add_argument("--skip-git", action="store_true", help="skip the git history pass")
    parser.add_argument("--dry-run", action="store_true", help="incremental: only print the plan")
    parser.add_argument("--skip-extract", action="store_true",
                        help="incremental: do not run extractors (their output is already up to date)")
    parser.add_argument("--out", help="dump: output file")
    args = parser.parse_args(argv)
    config = Config(args.config)

    from . import pipeline  # imported late so `--help` works without the neo4j driver installed

    try:
        if args.command == "full":
            pipeline.full(config, args.method, args.allow_missing_bridge, args.skip_git)
        elif args.command == "incremental":
            from .incremental import incremental
            incremental(config, dry_run=args.dry_run, skip_extract=args.skip_extract,
                        allow_missing_bridge=args.allow_missing_bridge, skip_git=args.skip_git)
        elif args.command == "status":
            print(json.dumps(pipeline.status(config), indent=2, default=str))
        elif args.command == "dump":
            pipeline.dump(config, Path(args.out or config.output_dir / "graph_dump.json"))
        elif args.command == "users":
            from .users import ensure_reader
            ensure_reader(config)
    except pipeline.LoadError as error:
        log("loader", f"FAILED: {error}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
