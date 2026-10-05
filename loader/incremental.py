"""Incremental update (SPEC 8.3): re-extract only what changed and patch the graph in place.

1. Changed files since the last :IndexRun commit (git diff + untracked files).
2. Map them to owners: C# projects (from the extractor's per-project .meta.json), native modules, C++/CLI.
3. Re-run the extractors for those owners only. If a project's symbols were removed or renamed, the
   projects whose edges point at the old IDs are re-extracted too (their calls now bind differently).
4. Run the same merge as a full load over all extractor output (so both paths make identical decisions),
   then apply only the changed owners to Neo4j: delete their relationships, upsert their nodes, create
   their relationships, delete their nodes that disappeared. Global derived data (DISPATCHES_TO,
   CO_CHANGES_WITH, derived entry points, git file statistics) is recomputed for the whole graph.

The fixture test (tests/test_incremental.py) checks that the result equals a full rebuild.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from itertools import chain
from pathlib import Path
from typing import Any

from codegraph_common.config import CHECKOUT, Config
from codegraph_common.jsonl import read_jsonl
from codegraph_common.log import log

from .git_history import git
from .merge import Merger
from .neo import Graph, create_edges, upsert_nodes
from .pipeline import LoadError, _fail_on_errors, finish_run, git_history, new_run

NATIVE_SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx", ".inl", ".vcxproj", ".props")
GLOBAL_DERIVED = {"DISPATCHES_TO", "CO_CHANGES_WITH"}
MEMBER_LABELS = {"Type", "Method", "Field", "Property", "Event"}


def incremental(config: Config, dry_run: bool = False, skip_extract: bool = False,
                allow_missing_bridge: bool = False, skip_git: bool = False) -> dict[str, Any]:
    graph = Graph(config.neo4j)
    try:
        last = graph.run("MATCH (r:IndexRun) RETURN r.commit AS commit, r.startedAt AS startedAt ORDER BY r.startedAt DESC LIMIT 1")
        if not last:
            raise LoadError("no previous index run in Neo4j: run a full index first")
        changed = changed_files(config, last[0]["commit"])
        plan = make_plan(config, changed)
        log("loader", f"incremental plan: {json.dumps(plan)}")
        if dry_run:
            print(json.dumps(plan, indent=2))
            return plan
        if not (plan["csharp"] or plan["removed"] or plan["native"] or plan["bridge"]):
            log("loader", "nothing to re-extract; refreshing derived data only")

        if not skip_extract:
            plan["csharp"] += reextract_csharp(config, plan["csharp"])
            if plan["native"]:
                run_tool(config, "native")
            if plan["bridge"] or plan["native"]:
                run_tool(config, "bridge")
        for owner in plan["removed"]:
            remove_owner_files(config, owner)

        owners = set(plan["csharp"]) | set(plan["removed"])
        if plan["native"]:
            owners |= owners_in(config.extractor_dirs()["native"])
        if plan["bridge"] or plan["native"]:
            owners |= owners_in(config.extractor_dirs()["bridge"])
        return apply(config, graph, sorted(owners), allow_missing_bridge, skip_git)
    finally:
        graph.close()


# ---- planning ------------------------------------------------------------------------------------

def changed_files(config: Config, base_commit: str | None) -> list[str]:
    repo = config.repo_root
    if base_commit and base_commit != "nogit" and git(repo, "cat-file", "-e", f"{base_commit}^{{commit}}") is not None:
        diff = git(repo, "diff", "--name-only", "--relative", base_commit) or ""
        untracked = git(repo, "ls-files", "--others", "--exclude-standard") or ""
        return sorted({line.strip() for line in (diff + "\n" + untracked).splitlines() if line.strip()})
    log("loader", "no usable git base commit: treating every indexed project as changed")
    return ["*"]


def make_plan(config: Config, changed: list[str]) -> dict[str, Any]:
    metas = _metas(config.extractor_dirs()["csharp"])
    by_file: dict[str, str] = {}
    project_dirs: list[tuple[str, str]] = []
    removed = []
    for owner, meta in metas.items():
        for path in meta.get("files", []):
            by_file[path.lower()] = owner
        project = meta.get("projectPath") or ""
        if project:
            if not (config.repo_root / project).exists():
                removed.append(owner)  # the project itself is gone
            by_file[project.lower()] = owner
            project_dirs.append((os.path.dirname(project).lower() + "/", owner))
    project_dirs.sort(key=lambda item: -len(item[0]))  # deepest project folder first

    csharp: set[str] = set()
    native = bridge = False
    cppcli_dirs = [str(c["source_dir"].relative_to(config.repo_root)).replace("\\", "/").lower() + "/"
                   for c in config.cppcli_assemblies if c["source_dir"] is not None and c["source_dir"].is_relative_to(config.repo_root)]
    for path in changed:
        if path == "*":
            csharp |= set(metas)
            native = bridge = True
            continue
        key = path.replace("\\", "/").lower()
        if key in by_file:
            csharp.add(by_file[key])
        elif key.endswith(".cs"):
            owner = next((o for folder, o in project_dirs if key.startswith(folder)), None)
            if owner:
                csharp.add(owner)  # a new file inside an existing project
        if key.endswith(NATIVE_SUFFIXES):
            native = True
            if any(key.startswith(folder) for folder in cppcli_dirs):
                bridge = True
    return {"changedFiles": len(changed), "csharp": sorted(csharp - set(removed)), "removed": sorted(removed),
            "native": native, "bridge": bridge}


# ---- extraction ----------------------------------------------------------------------------------

def reextract_csharp(config: Config, owners: list[str]) -> list[str]:
    """Runs the Roslyn extractor for the owners; returns dependents that had to be re-extracted as well."""
    if not owners:
        return []
    folder = config.extractor_dirs()["csharp"]
    before = {owner: _member_ids(folder, owner) for owner in owners}
    run_tool(config, "roslyn", [arg for owner in owners for arg in ("--project", owner)])
    removed = set().union(*(before[o] - _member_ids(folder, o) for o in owners))
    if not removed:
        return []
    dependents = sorted(o for o in owners_in(folder) if o not in owners and _points_at(folder, o, removed))
    if dependents:
        log("loader", f"{len(removed)} symbol(s) removed or renamed: re-extracting dependents {dependents}")
        run_tool(config, "roslyn", [arg for owner in dependents for arg in ("--project", owner)])
    return dependents


def run_tool(config: Config, name: str, extra: list[str] | None = None) -> None:
    command = tool_command(config, name) + ["--config", str(config.path)] + (extra or [])
    log("loader", f"running {name}: {' '.join(command)}")
    result = subprocess.run(command)
    if result.returncode == 3 and name == "roslyn":
        log("loader", "warning: Roslyn compile-error gate failed (output was written; edges may be missing)")
    elif result.returncode != 0:
        raise LoadError(f"{name} extractor failed with exit code {result.returncode}")


def tool_command(config: Config, name: str) -> list[str]:
    """Extractor command: tools.<name> in the config, else the build output inside this checkout."""
    configured = (config.raw.get("tools") or {}).get(name)
    if configured:
        return shlex.split(configured, posix=os.name != "nt")
    if name == "native":
        return [sys.executable, "-m", "codegraph_native"]
    project = {"roslyn": "CodeGraph.Roslyn", "bridge": "CodeGraph.Bridge"}[name]
    return ["dotnet", str(CHECKOUT / "extractors" / project / "bin" / "Release" / "net10.0" / f"{project}.dll")]


# ---- applying to Neo4j ---------------------------------------------------------------------------

def apply(config: Config, graph: Graph, owners: list[str], allow_missing_bridge: bool, skip_git: bool) -> dict[str, Any]:
    run = new_run(config, "incremental")
    merger = Merger(config, run, allow_missing_bridge)
    merger.load_nodes()
    history = git_history(config, skip_git)
    for _ in chain(merger.edges(), merger.derived_edges(history)):
        pass  # pass 1: stubs and derived properties
    nodes = merger.finalize_nodes(history)
    _fail_on_errors(config, merger)
    owned = set(owners)
    log("loader", f"applying {len(owned)} owner(s): {sorted(owned)}")

    # Pass 2: the changed owners' edges, plus the globally derived ones; remember which nodes they touch.
    pending: dict[str, list[dict[str, Any]]] = {}
    global_edges: dict[str, list[dict[str, Any]]] = {rel: [] for rel in GLOBAL_DERIVED}
    touched: set[str] = set()
    for edge in chain(merger.edges(), merger.derived_edges(history)):
        record = merger.finalize_edge(edge)
        if record["type"] in GLOBAL_DERIVED:
            global_edges[record["type"]].append(record)
        elif record.get("owner") in owned:
            pending.setdefault(record["type"], []).append(record)
        else:
            continue
        touched.update((record["start"], record["end"]))

    for owner in sorted(owned):
        graph.autocommit("MATCH (n:Node {assembly: $a})-[r]->() WHERE r.owner = $a AND NOT type(r) IN $skip "
                         "CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS", a=owner, skip=sorted(GLOBAL_DERIVED))
        graph.autocommit("MATCH ()-[r:IMPLEMENTS_METHOD {owner: $a}]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS", a=owner)

    # Owned nodes are rewritten; other nodes an edge touches are only created when missing (stubs, keys...).
    for label, records in nodes.items():
        upsert_nodes(graph, label, [n for n in records if n.get("assembly") in owned])
        upsert_nodes(graph, label, [n for n in records if n.get("assembly") not in owned and n["id"] in touched], only_create=True)
    for rel_type, rows in sorted(pending.items()):
        create_edges(graph, rel_type, rows)

    graph.autocommit("MATCH (n:Node) WHERE n.assembly IN $owners AND n.seenRun <> $run AND NOT n:IndexRun "
                     "CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS", owners=sorted(owned), run=run.run_id)
    _refresh_global(graph, nodes, global_edges)
    report = finish_run(config, graph, run, merger)
    log("loader", f"incremental update done: {len(owned)} owner(s)")
    return report


def _refresh_global(graph: Graph, nodes: dict[str, list[dict[str, Any]]], global_edges: dict[str, list[dict[str, Any]]]) -> None:
    """Data that any change can affect: dispatch edges, co-change edges, derived entry points, git stats, orphans."""
    for rel_type, rows in sorted(global_edges.items()):
        graph.autocommit(f"MATCH ()-[r:{rel_type}]->() CALL (r) {{ DELETE r }} IN TRANSACTIONS OF 10000 ROWS")
        create_edges(graph, rel_type, rows)

    derived = [{"id": n["id"], "kind": n["entryKind"], "confidence": n.get("entryConfidence")}
               for n in nodes.get("Method", []) if n.get("entrySource") == "derived"]
    graph.run("MATCH (m:Method {entrySource: 'derived'}) "
              "SET m.isEntryPoint = false, m.entryKind = null, m.entrySource = null, m.entryConfidence = null")
    graph.write_batches("UNWIND $rows AS row MATCH (m:Method:Node {id: row.id}) SET m.isEntryPoint = true, "
                        "m.entryKind = row.kind, m.entrySource = 'derived', m.entryConfidence = row.confidence", derived)

    stats = [{"id": n["id"], "count": n.get("commitCount"), "last": n.get("lastCommitDate")} for n in nodes.get("File", [])]
    graph.write_batches("UNWIND $rows AS row MATCH (f:File:Node {id: row.id}) "
                        "SET f.commitCount = row.count, f.lastCommitDate = row.last", stats)

    # Nodes nothing points at any more: stubs, config keys, messages, unresolved natives, git-only files.
    graph.autocommit("MATCH (n:Node) WHERE (n:External OR n:ConfigKey OR n:Message OR n.resolved = false "
                     "OR (n:File AND n.assembly IS NULL)) AND NOT (n)--() "
                     "CALL (n) { DELETE n } IN TRANSACTIONS OF 10000 ROWS")


# ---- helpers -------------------------------------------------------------------------------------

def _metas(folder: Path) -> dict[str, dict[str, Any]]:
    metas = {}
    for path in sorted(folder.glob("*.meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        metas[meta.get("owner", path.name[: -len(".meta.json")])] = meta
    return metas


def owners_in(folder: Path) -> set[str]:
    return set(_metas(folder)) if folder.is_dir() else set()


def _member_ids(folder: Path, owner: str) -> set[str]:
    path = folder / f"{owner}.nodes.jsonl"
    return {n["id"] for n in read_jsonl(path) if n["label"] in MEMBER_LABELS} if path.is_file() else set()


def _points_at(folder: Path, owner: str, ids: set[str]) -> bool:
    path = folder / f"{owner}.edges.jsonl"
    return path.is_file() and any(edge["end"] in ids for edge in read_jsonl(path))


def remove_owner_files(config: Config, owner: str) -> None:
    for folder in config.extractor_dirs().values():
        for suffix in (".nodes.jsonl", ".edges.jsonl", ".meta.json"):
            (folder / f"{owner}{suffix}").unlink(missing_ok=True)
    log("loader", f"removed output of deleted project {owner}")
