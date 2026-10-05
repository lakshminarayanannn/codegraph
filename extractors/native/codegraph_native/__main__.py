"""python -m codegraph_native --config <codegraph.yaml> [--compdb compile_commands.json] [--out <dir>]
                              [--workers N] [--no-cache]

Parses every non-/clr translation unit of the compilation database with libclang (SPEC 6).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codegraph_common.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m codegraph_native")
    parser.add_argument("--config", required=True)
    parser.add_argument("--compdb", help="compile_commands.json (default: native.compdb from the config)")
    parser.add_argument("--out", help="output folder (default: <output_dir>/native)")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    config = Config(args.config)
    compdb_path = Path(args.compdb) if args.compdb else config.resolve(config.native["compdb"])
    if not compdb_path.is_file():
        print(f"[native] {compdb_path} not found: generate it with extractors/native/make_compdb.ps1 (Windows) "
              f"or python -m codegraph_native.vcxproj_compdb", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else config.output_dir / "native"

    from .extract import run

    run(config, compdb_path, out_dir, args.workers, use_cache=not args.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
