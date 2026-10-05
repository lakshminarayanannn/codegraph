"""Native extraction run: parse every non-/clr translation unit in parallel (with a per-unit cache), merge the
results by USR, assign each symbol to its module (DLL), add exports, and write per-module JSONL files."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from codegraph_common import ids
from codegraph_common.config import Config
from codegraph_common.jsonl import write_json, write_jsonl
from codegraph_common.log import log

from . import compdb, exports
from .clangtools import clang_version, resource_dir

CACHE_VERSION = "2"


def run(config: Config, compdb_path: Path, out_dir: Path, workers: int, use_cache: bool) -> dict[str, Any]:
    clock = time.time()
    settings = config.native
    repo_root = str(config.repo_root)
    units = compdb.load(compdb_path)
    clr_units = [u.file for u in units if u.is_clr]
    native_units = sorted((u for u in units if not u.is_clr), key=lambda u: u.file)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "clr_files.json", {"count": len(clr_units), "files": sorted(clr_units)})

    # clang-cl driver mode keeps MSVC flags and gives MSVC-mangled names (what the bridge and dumpbin see).
    base = ["--driver-mode=cl", f"--target={settings['target']}"] + [f"/clang:{a}" for a in settings["extra_args"]]
    version = clang_version()
    builtin = resource_dir(settings.get("resource_dir"), version)
    if builtin:
        base += [f"/clang:-resource-dir={builtin}"]
    cache_dir = out_dir / "cache"
    # "--" ends the options: in clang-cl mode a Unix path like /Users/x.cpp would otherwise parse as /U (undefine).
    jobs = [(u.file, base + u.args + ["--"], repo_root, config.native_log_functions, str(cache_dir), use_cache, version)
            for u in native_units]

    results: list[dict[str, Any]] = []
    hits = 0
    max_workers = workers or int(settings.get("max_workers") or 0) or os.cpu_count() or 1
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(_parse_cached, jobs, chunksize=1):
            hits += result.pop("cacheHit", False)
            results.append(result)
    log("native", f"parsed {len(results)} translation unit(s) ({hits} from cache), skipped {len(clr_units)} /clr unit(s)")

    merged = merge(results)
    modules = ModuleMap(config)
    report = write_modules(config, merged, modules, out_dir)

    diagnostics = [{"file": r["source"], "errors": r["errors"], "first": r.get("firstErrors", [])} for r in results if r["errors"]]
    with_errors = len(diagnostics)
    rate = with_errors / len(results) if results else 0.0
    write_json(out_dir / "diagnostics.json", {"translationUnits": len(results), "withErrors": with_errors,
                                              "rate": round(rate, 4), "units": diagnostics})
    warnings = []
    if rate >= 0.05:
        warnings.append(f"{with_errors}/{len(results)} translation units have errors ({rate:.0%} >= 5% gate): "
                        f"fix include paths in the compilation database (see out/native/diagnostics.json)")
    manifest = {
        "tool": "codegraph_native", "version": "1.1.0", "libclangVersion": version, "resourceDir": builtin,
        "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "durationSeconds": round(time.time() - clock, 1),
        "translationUnits": len(results), "cacheHits": hits, "clrUnitsSkipped": len(clr_units),
        "unitsWithErrors": with_errors, "externalCallsSkipped": sum(r.get("externalCalls", 0) for r in results),
        **report, "warnings": warnings,
    }
    write_json(out_dir / "manifest.json", manifest)
    for warning in warnings:
        log("native", f"warning: {warning}")
    return manifest


# ---- parsing with a per-unit cache ----------------------------------------------------------------

def _parse_cached(job: tuple) -> dict[str, Any]:
    """Cache key: source + arguments + libclang version. A hit also needs every included repo header unchanged."""
    source, args, repo_root, log_rules, cache_dir, use_cache, version = job
    key = hashlib.sha1(json.dumps([CACHE_VERSION, version, source, args, log_rules]).encode("utf-8")).hexdigest()
    cache_file = Path(cache_dir) / f"{key}.json"
    source_hash = _sha1(source)
    if use_cache and cache_file.is_file():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("sourceHash") == source_hash and all(
                _sha1(os.path.join(repo_root, path)) == digest for path, digest in cached["result"]["includes"].items()):
            return {**cached["result"], "cacheHit": True}

    from .walker import parse  # imported in the worker: libclang loads per process

    result = parse(source, args, repo_root, log_rules)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"sourceHash": source_hash, "result": result}), encoding="utf-8")
    return result


def _sha1(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha1(handle.read()).hexdigest()
    except OSError:
        return None


# ---- merging translation units --------------------------------------------------------------------

def merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    """One record per USR (a definition beats a declaration) and one edge per (type, start, end, kind, via)."""
    functions: dict[str, dict[str, Any]] = {}
    types: dict[str, dict[str, Any]] = {}
    messages: dict[str, dict[str, Any]] = {}
    files: set[str] = set()
    edges: dict[tuple, dict[str, Any]] = {}
    for result in results:
        for record in result["functions"]:
            existing = functions.get(record["id"])
            if existing is None or (record["isDefinition"] and not existing["isDefinition"]):
                functions[record["id"]] = record
        for record in result["types"]:
            types.setdefault(record["id"], record)
        for record in result["messages"]:
            messages.setdefault(record["id"], record)
        files.update(result["files"])
        for edge in result["edges"]:
            key = (edge["type"], edge["start"], edge["end"], edge.get("kind"), edge.get("via"))
            merged = edges.get(key)
            if merged is None:
                edges[key] = {**edge, "lines": {edge["line"]}}
            else:
                merged["lines"].add(edge["line"])
    known = set(functions) | set(types) | set(messages)
    # Overrides and bases outside the indexed code (SDK, std) are dropped: the targets have no node.
    kept = [e for e in edges.values() if e["end"] in known and e["start"] in known]
    return {"functions": functions, "types": types, "messages": messages, "files": files, "edges": kept}


# ---- modules (DLLs) -------------------------------------------------------------------------------

class ModuleMap:
    """Which module (DLL) a file belongs to: native.modules globs, else the nearest .vcxproj above the file."""

    def __init__(self, config: Config):
        self.root = config.repo_root
        self.rules = [(m["name"], [str(p) for p in m.get("paths", [])]) for m in config.native.get("modules") or []]
        self._cache: dict[str, str] = {}

    def of(self, relative_path: str) -> str:
        cached = self._cache.get(relative_path)
        if cached is None:
            cached = self._cache[relative_path] = self._find(relative_path)
        return cached

    def _find(self, relative_path: str) -> str:
        for name, patterns in self.rules:
            if any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns):
                return name
        folder = (self.root / relative_path).parent
        while True:
            projects = sorted(folder.glob("*.vcxproj"))
            if projects:
                return _target_name(projects[0])
            if folder == self.root or folder.parent == folder:
                return "native"
            folder = folder.parent


def _target_name(project: Path) -> str:
    """<TargetName> when the project sets one unconditionally, else the project file name."""
    try:
        for element in ElementTree.parse(project).iter():
            if element.tag.endswith("TargetName") and element.text and "$(" not in element.text:
                return element.text.strip()
    except ElementTree.ParseError:
        pass
    return project.stem


# ---- output ---------------------------------------------------------------------------------------

def write_modules(config: Config, merged: dict[str, Any], modules: ModuleMap, out_dir: Path) -> dict[str, Any]:
    nodes_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edges_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owner_of: dict[str, str] = {}

    def add_node(module: str, node: dict[str, Any]) -> None:
        nodes_by_module[module].append(node)
        owner_of[node["id"]] = module

    for record in merged["types"].values():
        module = modules.of(record["file"])
        add_node(module, {"label": "NativeType", "lang": "cpp", "assembly": module, "isDefinition": True,
                          **{k: v for k, v in record.items() if k != "dllexport" and v is not None}})
        edges_by_module[module].append({"type": "DEFINED_IN", "start": record["id"], "end": ids.file_id(record["file"]),
                                        "file": record["file"], "line": record["lineStart"], "lineEnd": record["lineEnd"]})
        container = record.get("typeId")
        if container:
            edges_by_module[module].append({"type": "HAS_MEMBER", "start": container, "end": record["id"]})
        else:
            edges_by_module[module].append({"type": "CONTAINS", "start": ids.assembly_id(module), "end": record["id"]})
    for record in merged["functions"].values():
        module = modules.of(record["file"])
        add_node(module, {"label": "NativeFunction", "lang": "cpp", "assembly": module, "fqn": record["qualifiedName"],
                          **{k: v for k, v in record.items() if k not in ("dllexport", "manglings") and v is not None}})
        if record.get("typeId") in merged["types"]:
            edges_by_module[module].append({"type": "HAS_MEMBER", "start": record["typeId"], "end": record["id"]})
    for record in merged["messages"].values():
        module = modules.of(record["file"])
        add_node(module, {"label": "Message", "lang": "cpp", "assembly": module, "name": record["template"][:120],
                          **{k: v for k, v in record.items() if v is not None}})
    for path in sorted(merged["files"]):
        module = modules.of(path)
        add_node(module, {"label": "File", "id": ids.file_id(path), "name": path.rsplit("/", 1)[-1], "fqn": path,
                          "path": path, "file": path, "lang": "cpp", "assembly": module,
                          "sha1": _sha1(str(config.repo_root / path))})
    for edge in merged["edges"]:
        module = owner_of.get(edge["start"], "native")
        lines = sorted(edge.pop("lines"))
        edge["line"] = lines[0]
        if len(lines) > 1:
            edge["lines"], edge["count"] = lines, len(lines)
        edges_by_module[module].append(edge)

    # Exports: dumpbin on the built DLLs, or __declspec(dllexport) in the source.
    if config.native.get("exports_from") == "dumpbin":
        names = {ids.normalize_dll(dll.name): exports.dumpbin_exports(dll) for dll in config.native_dlls if dll.is_file()}
    else:
        names = exports.source_exports(merged["functions"], lambda f: ids.normalize_dll(modules.of(f["file"])))
    export_nodes, resolves = exports.resolve(names, merged["functions"])
    module_by_dll = {ids.normalize_dll(m): m for m in set(owner_of.values())}
    for node in export_nodes:
        module = module_by_dll.get(node["dll"], node["dll"].rsplit(".", 1)[0])
        add_node(module, {**node, "assembly": module})
    for edge in resolves:
        edges_by_module[owner_of[edge["start"]]].append(edge)

    report: dict[str, Any] = {"modules": {}}
    for module in sorted(set(nodes_by_module) | set(edges_by_module)):
        nodes = nodes_by_module[module] + [{"label": "Assembly", "id": ids.assembly_id(module), "name": module, "fqn": module,
                                            "lang": "cpp", "assembly": module, "kind": "native"}]
        edges = [{**e, "owner": module} for e in edges_by_module[module]]
        nodes.sort(key=lambda n: (n["label"], n["id"]))
        edges.sort(key=lambda e: (e["type"], e["start"], e["end"], e.get("kind") or "", e.get("via") or ""))
        safe = "".join(c if c not in '<>:"/\\|?*' else "_" for c in module)
        write_jsonl(out_dir / f"{safe}.nodes.jsonl", nodes)
        write_jsonl(out_dir / f"{safe}.edges.jsonl", edges)
        meta = {"owner": module, "language": "cpp", "nodes": len(nodes), "edges": len(edges),
                "files": sorted({n["file"] for n in nodes if n["label"] == "File"})}
        write_json(out_dir / f"{safe}.meta.json", meta)
        report["modules"][module] = {"nodes": len(nodes), "edges": len(edges)}
        log("native", f"  {module}: {len(nodes)} nodes, {len(edges)} edges")
    report["exports"] = len(export_nodes)
    report["exportsResolved"] = len(resolves)
    return report
