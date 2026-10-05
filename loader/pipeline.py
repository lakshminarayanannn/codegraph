"""Load pipelines: full rebuild (SPEC 8.2), status, and a canonical dump used to compare graphs.

The incremental pipeline (SPEC 8.3) lives in incremental.py and reuses the same merge.
"""
from __future__ import annotations

import contextlib
import json
import time
from collections import defaultdict
from itertools import chain
from pathlib import Path
from typing import Any

from codegraph_common.config import Config
from codegraph_common.jsonl import write_json
from codegraph_common.log import log

from .admin import admin_import
from .csvout import CsvWriter
from .git_history import collect, head_commit
from .merge import GitHistory, Merger, RunInfo
from .neo import BATCH, Graph, create_edges, delete_all, upsert_nodes


class LoadError(RuntimeError):
    """A gate failed (missing bridge nodes, ...): nothing was imported."""


def new_run(config: Config, mode: str) -> RunInfo:
    commit, dirty = head_commit(config.repo_root)
    return RunInfo(run_id=time.strftime("%Y%m%d-%H%M%S"), commit=commit, started_at=_now(), mode=mode, dirty=dirty)


def git_history(config: Config, skip: bool) -> GitHistory | None:
    if skip:
        return None
    settings = config.git_history
    return collect(config.repo_root, settings["window_days"], settings["co_change_min"], settings["co_change_max_files"])


def full(config: Config, method: str | None = None, allow_missing_bridge: bool = False, skip_git: bool = False) -> dict[str, Any]:
    """Merge every extractor output and rebuild the whole graph."""
    clock = time.time()
    run = new_run(config, "full")
    merger = Merger(config, run, allow_missing_bridge)
    merger.load_nodes()
    history = git_history(config, skip_git)
    method = method or config.neo4j.import_method

    if method == "admin":
        graph = _admin_load(config, merger, history)
    elif method == "bolt":
        graph = _bolt_load(config, merger, history)
    else:
        raise ValueError(f"unknown import method {method!r} (admin or bolt)")

    try:
        report = finish_run(config, graph, run, merger)
    finally:
        graph.close()
    log("loader", f"full {method} load done in {time.time() - clock:.1f}s: "
                  f"{sum(merger.report.node_counts.values())} nodes, {sum(merger.report.edge_counts.values())} relationships")
    return report


def _admin_load(config: Config, merger: Merger, history: GitHistory | None) -> Graph:
    writer = CsvWriter(config.import_dir)
    for edge in chain(merger.edges(), merger.derived_edges(history)):
        writer.write_edge(merger.finalize_edge(edge))
    nodes = merger.finalize_nodes(history)
    _fail_on_errors(config, merger)
    for label, records in nodes.items():
        writer.write_nodes(label, records)
    writer.close()
    admin_import(config, writer.node_files, writer.rel_files, config.import_dir / "import.report")
    graph = Graph(config.neo4j)
    graph.wait_until_available()
    graph.apply_schema()
    return graph


def _bolt_load(config: Config, merger: Merger, history: GitHistory | None) -> Graph:
    # Pass 1 only observes (stubs, derived properties); nodes must exist before edges can MATCH them.
    for _ in chain(merger.edges(), merger.derived_edges(history)):
        pass
    nodes = merger.finalize_nodes(history)
    _fail_on_errors(config, merger)
    graph = Graph(config.neo4j)
    graph.wait_until_available()
    delete_all(graph)
    graph.apply_schema()
    for label, records in nodes.items():
        upsert_nodes(graph, label, records)
    # Pass 2 streams the same (deterministic) edges into Neo4j, batched per relationship type.
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in chain(merger.edges(), merger.derived_edges(history)):
        record = merger.finalize_edge(edge)
        pending[record["type"]].append(record)
        if len(pending[record["type"]]) >= BATCH:
            create_edges(graph, record["type"], pending.pop(record["type"]))
    for rel_type, rows in pending.items():
        create_edges(graph, rel_type, rows)
    return graph


def _fail_on_errors(config: Config, merger: Merger) -> None:
    if merger.report.errors:
        write_json(config.import_dir / "merge_report.json", merger.report.to_dict())
        raise LoadError("; ".join(merger.report.errors))


def finish_run(config: Config, graph: Graph, run: RunInfo, merger: Merger) -> dict[str, Any]:
    """Writes the :IndexRun node and out/import/merge_report.json."""
    report = merger.report.to_dict()
    report["run"] = {"runId": run.run_id, "commit": run.commit, "mode": run.mode, "dirty": run.dirty}
    warnings = list(merger.report.warnings) + _extractor_warnings(config)
    rate = report["bridgeResolution"]["matchRate"]
    if rate is not None and rate < 0.9:
        warnings.append(f"bridge native-name match rate {rate:.0%} is below the 90% gate; "
                        f"see merge_report.json bridgeResolution.unresolvedNames")
    report["warnings"] = warnings
    graph.run(
        "MERGE (r:IndexRun:Node {id: $id}) SET r += $props",
        id=f"run:{run.run_id}",
        props={
            "runId": run.run_id, "commit": run.commit, "startedAt": run.started_at, "finishedAt": _now(),
            "mode": run.mode, "dirty": run.dirty, "name": f"index run {run.run_id}",
            "toolVersions": json.dumps(_tool_versions(config, graph)),
            "counts": json.dumps({"nodes": report["nodes"], "edges": report["edges"]}),
            "warnings": json.dumps(warnings),
        },
    )
    write_json(config.import_dir / "merge_report.json", report)
    for warning in warnings:
        log("loader", f"warning: {warning}")
    return report


def _extractor_warnings(config: Config) -> list[str]:
    """Surfaces extractor gates (compile errors, native diagnostics) in the run record."""
    warnings = []
    for origin, folder in config.extractor_dirs().items():
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        failed = (data.get("gate") or {}).get("failedProjects") or []
        if failed:
            warnings.append(f"{origin}: compile errors above max_compile_errors in {', '.join(failed)} (edges may be missing)")
        for warning in data.get("warnings") or []:
            warnings.append(f"{origin}: {warning}")
    return warnings


def _tool_versions(config: Config, graph: Graph) -> dict[str, Any]:
    versions: dict[str, Any] = {"loader": "1.1.0"}
    for origin, folder in config.extractor_dirs().items():
        manifest = folder / "manifest.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            versions[origin] = {k: data.get(k) for k in ("tool", "version", "roslynVersion", "decompilerVersion",
                                                          "libclangVersion", "runtime") if data.get(k)}
    with contextlib.suppress(Exception):  # not critical: some editions restrict dbms procedures
        versions["neo4j"] = graph.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v")[0]["v"]
    return versions


def status(config: Config) -> dict[str, Any]:
    graph = Graph(config.neo4j, writer=False)
    try:
        rows = graph.run("MATCH (r:IndexRun) RETURN r ORDER BY r.startedAt DESC LIMIT 1")
    finally:
        graph.close()
    if not rows:
        return {"indexed": False}
    run = dict(rows[0]["r"])
    for key in ("counts", "warnings", "toolVersions"):  # stored as JSON strings (SPEC 4.2)
        if isinstance(run.get(key), str):
            run[key] = json.loads(run[key])
    head, dirty = head_commit(config.repo_root)
    return {"indexed": True, "run": run, "head": head, "stale": head != run.get("commit"), "workingTreeDirty": dirty}


def dump(config: Config, path: Path) -> None:
    """Canonical JSON of the whole graph minus run stamps: two equal graphs give byte-identical dumps."""
    graph = Graph(config.neo4j, writer=False)
    volatile = {"commit", "seenRun"}
    try:
        nodes = graph.run("MATCH (n:Node) WHERE NOT n:IndexRun RETURN n.id AS id, labels(n) AS labels, properties(n) AS props")
        edges = graph.run("MATCH (a:Node)-[r]->(b:Node) RETURN a.id AS start, type(r) AS type, b.id AS end, properties(r) AS props")
    finally:
        graph.close()
    canonical_nodes = sorted(
        ({"id": n["id"], "labels": sorted(n["labels"]), "props": {k: v for k, v in sorted(n["props"].items()) if k not in volatile}}
         for n in nodes), key=lambda n: n["id"])
    canonical_edges = sorted(
        ({"start": e["start"], "type": e["type"], "end": e["end"],
          "props": {k: v for k, v in sorted(e["props"].items()) if k not in volatile}} for e in edges),
        key=lambda e: (e["type"], e["start"], e["end"], json.dumps(e["props"], sort_keys=True)))
    write_json(path, {"nodes": canonical_nodes, "edges": canonical_edges})
    log("loader", f"dumped {len(canonical_nodes)} nodes and {len(canonical_edges)} relationships to {path}")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
