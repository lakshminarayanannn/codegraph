"""Full import with neo4j-admin (SPEC 8.2): stop Neo4j, bulk-import the CSV files, start Neo4j.

Community Edition has one user database and neo4j-admin's full import needs it offline, so the
server is stopped for the import. This is the fastest way to load a large graph.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from codegraph_common.config import Config
from codegraph_common.log import log


def admin_import(config: Config, node_files: list[Path], rel_files: list[Path], report_file: Path) -> None:
    settings = config.neo4j
    neo4j, admin = settings.tool("neo4j"), settings.tool("neo4j-admin")

    log("loader", "stopping Neo4j for the bulk import")
    subprocess.run([neo4j, "stop"], check=False, capture_output=True, text=True)

    # The database name goes first: --nodes/--relationships take several files and would swallow it.
    command = [
        admin, "database", "import", "full", settings.database,
        "--overwrite-destination=true",
        "--id-type=string",
        "--array-delimiter=;",
        f"--report-file={report_file}",
        *[f"--nodes={path}" for path in node_files],
        *[f"--relationships={path}" for path in rel_files],
    ]
    log("loader", f"neo4j-admin import: {len(node_files)} node file(s), {len(rel_files)} relationship file(s)")
    result = subprocess.run(command, capture_output=True, text=True)
    (report_file.parent / "neo4j-admin.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        subprocess.run([neo4j, "start"], check=False, capture_output=True)
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-25:])
        raise RuntimeError(f"neo4j-admin import failed (exit {result.returncode}):\n{tail}")
    summary = [line for line in result.stdout.splitlines() if "Imported" in line or "nodes" in line.lower()][-4:]
    for line in summary:
        log("loader", "  " + line.strip())

    log("loader", "starting Neo4j")
    started = subprocess.run([neo4j, "start"], capture_output=True, text=True)
    if started.returncode != 0 and "already running" not in (started.stdout + started.stderr):
        raise RuntimeError(f"neo4j start failed: {started.stdout} {started.stderr}")
