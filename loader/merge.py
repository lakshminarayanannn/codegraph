"""Merge step (SPEC 8.1): one consistent graph from every extractor's per-owner JSONL files.

* Nodes: duplicate IDs are merged - a real node beats an :External stub, and between real records the
  defining extractor wins (C# for managed symbols, native for native ones, the bridge in between).
* Edges: bridge names are resolved against native symbols, every endpoint is checked, edges into
  :External nodes become REFS_EXTERNAL {rel: <original type>}, duplicates are dropped.
* Derived data: DISPATCHES_TO, REFERENCES to native modules, UI-handler and native-callback entry
  points, message regexes, layers, git history. Computing them here (not with Cypher after import)
  gives full and incremental loads one implementation, and the bulk importer writes them at full speed.

Edges are streamed (never all in memory); nodes are held in a dict keyed by ID.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codegraph_common import ids
from codegraph_common.config import Config
from codegraph_common.jsonl import read_jsonl
from codegraph_common.log import log
from codegraph_common.schema import NODE_DEFAULTS, VIA_TYPES
from logmatch.templates import compile_template

NATIVE_LABELS = {"NativeType", "NativeFunction", "NativeExport"}

# Lower rank wins when two extractors define the same node (SPEC 8.1: source-defined > bridge > external).
_RANK = {
    "managed": {"csharp": 0, "bridge": 1, "native": 2},
    "native": {"native": 0, "bridge": 1, "csharp": 2},
}

# Nodes not owned by one assembly: upserted (never deleted) by the incremental loader.
GLOBAL_LABELS = {"External", "ConfigKey", "NativeExport"}

EdgeSink = Callable[[dict[str, Any]], None]


@dataclass
class RunInfo:
    run_id: str
    commit: str
    started_at: str
    mode: str
    dirty: bool = False


@dataclass
class MergeReport:
    counters: Counter = field(default_factory=Counter)        # node loading
    edge_pass: Counter = field(default_factory=Counter)       # the latest pass over the edges
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bridge: Counter = field(default_factory=Counter)
    unresolved_native: Counter = field(default_factory=Counter)
    node_counts: Counter = field(default_factory=Counter)
    edge_counts: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        matched = self.bridge["exact"] + self.bridge["heuristic"]
        total = matched + self.bridge["unresolved"]
        return {
            "counters": dict(sorted((self.counters + self.edge_pass).items())),
            "nodes": dict(sorted(self.node_counts.items())),
            "edges": dict(sorted(self.edge_counts.items())),
            "bridgeResolution": {
                **dict(self.bridge),
                "matchRate": round(matched / total, 4) if total else None,
                "unresolvedNames": [name for name, _ in self.unresolved_native.most_common(100)],
            },
            "conflicts": self.conflicts[:50],
            "errors": self.errors,
            "warnings": self.warnings,
        }


class Merger:
    def __init__(self, config: Config, run: RunInfo, allow_missing_bridge: bool = False):
        self.config = config
        self.run = run
        self.allow_missing_bridge = allow_missing_bridge
        self.nodes: dict[str, dict[str, Any]] = {}
        self.origin: dict[str, str] = {}
        self.report = MergeReport()
        self.edge_files: list[tuple[str, Path]] = []
        self._native_index: tuple[dict, dict, dict] | None = None
        self._reset_observations()

    # ---- nodes -------------------------------------------------------------------------------

    def load_nodes(self) -> None:
        for origin, folder in self.config.extractor_dirs().items():
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.nodes.jsonl")):
                for record in read_jsonl(path):
                    self._add_node(record, origin)
            self.edge_files += [(origin, p) for p in sorted(folder.glob("*.edges.jsonl"))]
        log("loader", f"read {len(self.nodes)} nodes, {len(self.edge_files)} edge files")

    def _add_node(self, record: dict[str, Any], origin: str) -> None:
        node_id = record["id"]
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = record
            self.origin[node_id] = origin
            return
        if record["label"] == "External":
            if record.get("expectedFrom") and existing["label"] == "External":
                existing["expectedFrom"] = record["expectedFrom"]
            return
        if existing["label"] == "External":
            self.nodes[node_id] = record
            self.origin[node_id] = origin
            return

        # Two real definitions: keep the higher-priority extractor's record, log real disagreements.
        family = "native" if record["label"] in NATIVE_LABELS else "managed"
        keep_new = _RANK[family][origin] < _RANK[family][self.origin[node_id]]
        winner, loser = (record, existing) if keep_new else (existing, record)
        differing = sorted(k for k in ("label", "name", "kind", "signature")
                           if k in winner and k in loser and winner[k] != loser[k])
        if differing:
            self.report.counters["node_conflicts"] += 1
            if len(self.report.conflicts) < 50:
                self.report.conflicts.append({"id": node_id, "kept": self.origin[node_id] if not keep_new else origin,
                                              "properties": differing})
        else:
            self.report.counters["node_duplicates"] += 1
        if keep_new:
            # Fill gaps from the other record (e.g. a native export's dll from the C# side).
            for key, value in loser.items():
                record.setdefault(key, value)
            self.nodes[node_id] = record
            self.origin[node_id] = origin
        else:
            for key, value in loser.items():
                existing.setdefault(key, value)

    # ---- edges -------------------------------------------------------------------------------

    def _reset_observations(self) -> None:
        self._seen: set[tuple] = set()
        self._dispatch_pairs: list[tuple[str, str, str]] = []   # (implementation, declaration, edge type)
        self._ui_handlers: set[str] = set()
        self._callbacks: dict[str, str] = {}
        self._native_refs: set[tuple[str, str, str]] = set()     # (from assembly, to module, kind)
        self._referenced: set[str] = set()                         # every node some edge touches

    def edges(self) -> Iterator[dict[str, Any]]:
        """Validated, normalized edges in file order. Re-iterable: each call starts a fresh pass."""
        self._reset_observations()
        self.report.edge_pass = Counter()
        self.report.bridge = Counter()
        self.report.unresolved_native = Counter()
        self.report.edge_counts = Counter()
        for origin, path in self.edge_files:
            for edge in read_jsonl(path):
                normalized = self._normalize_edge(edge, origin)
                if normalized is not None:
                    yield normalized

    def _normalize_edge(self, edge: dict[str, Any], origin: str) -> dict[str, Any] | None:
        if edge.get("nativeName") and edge["end"].startswith(("cpp:unresolved:", "cpp:unresolved-type:")):
            self._resolve_native(edge)

        start = self.nodes.get(edge["start"])
        if start is None:
            self.report.edge_pass["dropped_dangling_start"] += 1
            return None
        if start["label"] == "External":
            self.report.edge_pass["dropped_external_start"] += 1
            return None

        end = self.nodes.get(edge["end"])
        if end is None and edge["type"] == "REFERENCES":
            self.report.edge_pass["dropped_reference_to_unindexed_assembly"] += 1
            return None  # mscorlib, NuGet packages...: only references between indexed assemblies matter
        if end is None:
            end = self._stub(edge["end"])
        if end["label"] == "External" and edge["type"] != "REFS_EXTERNAL":
            edge["rel"] = edge["type"]
            edge["type"] = "REFS_EXTERNAL"

        key = (edge["type"], edge["start"], edge["end"], edge.get("kind"), edge.get("via"), edge.get("handler"), edge.get("rel"))
        if key in self._seen:
            self.report.edge_pass["duplicate_edges"] += 1
            return None
        self._seen.add(key)

        self._observe(edge, start, end)
        return edge

    def _stub(self, node_id: str) -> dict[str, Any]:
        """An :External node for a target no extractor defined (e.g. a NuGet symbol the bridge saw)."""
        kind = {"dn:M:": "Method", "dn:T:": "Type", "dn:F:": "Field", "dn:P:": "Property", "dn:E:": "Event"}.get(node_id[:5])
        name = re.split(r"[.!:]", node_id.split("(")[0])[-1] or node_id
        stub = {"label": "External", "id": node_id, "name": name, "fqn": node_id.split(":", 2)[-1], "lang": "ext",
                "symbolKind": kind or "Unknown"}
        self.nodes[node_id] = stub
        self.origin[node_id] = "stub"
        self.report.edge_pass["stubs_created"] += 1
        return stub

    def _observe(self, edge: dict[str, Any], start: dict[str, Any], end: dict[str, Any]) -> None:
        """Remembers the few facts the derived data needs, so edges never have to be held in memory."""
        self._referenced.add(edge["start"])
        self._referenced.add(edge["end"])
        kind = edge["type"]
        rel = edge.get("rel", kind)
        if kind in ("IMPLEMENTS_METHOD", "OVERRIDES") and start["label"] == end["label"] and start["label"] in ("Method", "NativeFunction"):
            self._dispatch_pairs.append((edge["start"], edge["end"], kind))
        if rel == "SUBSCRIBES" and edge.get("uiEvent") and edge.get("handler"):
            self._ui_handlers.add(edge["handler"])
        if kind == "REFERENCES_METHOD" and edge.get("callbackKind"):
            self._callbacks[edge["end"]] = "exact" if edge["callbackKind"] == "pinvoke" else "heuristic"
        if kind == "PINVOKES" and start.get("assembly") and end.get("assembly"):
            self._native_refs.add((start["assembly"], end["assembly"], "pinvoke"))
        if kind == "CALLS_NATIVE" and start.get("assembly") and end.get("assembly"):
            self._native_refs.add((start["assembly"], end["assembly"], "ijw"))

    # ---- bridge: native name resolution (SPEC 7.3, step 4) -----------------------------------

    def _index(self) -> tuple[dict, dict, dict]:
        if self._native_index is None:
            by_mangled: dict[str, list[str]] = defaultdict(list)
            by_qualified: dict[str, list[str]] = defaultdict(list)
            types: dict[str, list[str]] = defaultdict(list)
            for node_id, node in self.nodes.items():
                if node["label"] == "NativeFunction" and node.get("resolved", True):
                    if node.get("mangledName"):
                        by_mangled[node["mangledName"]].append(node_id)
                    if node.get("qualifiedName"):
                        by_qualified[node["qualifiedName"]].append(node_id)
                elif node["label"] == "NativeType" and node.get("resolved", True) and node.get("fqn"):
                    types[node["fqn"]].append(node_id)
            self._native_index = (by_mangled, by_qualified, types)
        return self._native_index

    def _resolve_native(self, edge: dict[str, Any]) -> None:
        by_mangled, by_qualified, types = self._index()
        name = edge["nativeName"]
        is_type = edge["end"].startswith("cpp:unresolved-type:")
        if is_type:
            candidates = sorted(types.get(native_qualified_name(name), []))
        elif name.startswith("?"):
            candidates = sorted(by_mangled.get(name, []))
        else:
            candidates = sorted(by_qualified.get(native_qualified_name(name), []))
            if len(candidates) > 1 and edge.get("nativeParamCount") is not None:
                narrowed = [c for c in candidates if self.nodes[c].get("paramCount") == edge["nativeParamCount"]]
                candidates = narrowed or candidates

        if candidates:
            edge["end"] = candidates[0]
            edge["confidence"] = "exact" if len(candidates) == 1 else "heuristic"
            if len(candidates) > 1:
                edge["candidates"] = len(candidates)
            if not is_type:
                self.report.bridge[edge["confidence"]] += 1
            return

        # No native symbol: keep the edge, pointing at an explicit "unresolved" node (SPEC 7.3).
        if not is_type:
            self.report.bridge["unresolved"] += 1
            self.report.unresolved_native[name] += 1
        if edge["end"] not in self.nodes:
            qualified = native_qualified_name(name)
            self.nodes[edge["end"]] = {
                "label": "NativeType" if is_type else "NativeFunction",
                "id": edge["end"], "name": qualified.split("::")[-1], "fqn": qualified, "qualifiedName": qualified,
                "lang": "cpp", "resolved": False,
            }
            self.origin[edge["end"]] = "bridge"
        edge["confidence"] = "heuristic"

    # ---- derived edges -----------------------------------------------------------------------

    def derived_edges(self, git: GitHistory | None) -> Iterator[dict[str, Any]]:
        """DISPATCHES_TO, REFERENCES to native modules, CO_CHANGES_WITH (after an edges() pass)."""
        yield from self._dispatch_edges()
        yield from self._native_reference_edges()
        if git is not None:
            yield from self._co_change_edges(git)

    def _dispatch_edges(self) -> Iterator[dict[str, Any]]:
        """declaration -> every implementation and override below it (SPEC 8.2 step 5, same rule)."""
        implementations: dict[str, set[str]] = defaultdict(set)
        overriders: dict[str, set[str]] = defaultdict(set)
        for implementation, declaration, kind in self._dispatch_pairs:
            implementations[declaration].add(implementation)
            if kind == "OVERRIDES":
                overriders[declaration].add(implementation)
        for declaration in sorted(implementations):
            reached: set[str] = set()
            pending = sorted(implementations[declaration])
            while pending:
                target = pending.pop()
                if target in reached:
                    continue
                reached.add(target)
                pending.extend(sorted(overriders.get(target, ())))
            for target in sorted(reached):
                yield {"type": "DISPATCHES_TO", "start": declaration, "end": target,
                       "owner": self.nodes[target].get("assembly"), "confidence": "exact", "derived": True}

    def _native_reference_edges(self) -> Iterator[dict[str, Any]]:
        """Managed assembly -> native module it P/Invokes or calls through IJW (for assembly_graph and layers)."""
        assemblies = {n["name"].lower(): n["id"] for n in self.nodes.values() if n["label"] == "Assembly"}
        for source, target, kind in sorted(self._native_refs):
            start, end = assemblies.get(source.lower()), assemblies.get(target.lower())
            if start and end and start != end:
                yield {"type": "REFERENCES", "start": start, "end": end, "owner": source, "kind": kind,
                       "derived": True, "confidence": "exact"}

    def _co_change_edges(self, git: GitHistory) -> Iterator[dict[str, Any]]:
        for a, b, count in git.pairs:
            for path in (a, b):
                self._ensure_file_node(path)
            yield {"type": "CO_CHANGES_WITH", "start": ids.file_id(a), "end": ids.file_id(b), "count": count,
                   "confidence": "heuristic", "derived": True, "owner": "git"}

    def _ensure_file_node(self, path: str) -> None:
        node_id = ids.file_id(path)
        if node_id not in self.nodes:
            self.nodes[node_id] = {"label": "File", "id": node_id, "name": path.rsplit("/", 1)[-1], "fqn": path,
                                   "path": path, "file": path, "lang": _lang_of(path)}
            self.origin[node_id] = "git"

    # ---- final node properties ---------------------------------------------------------------

    def finalize_nodes(self, git: GitHistory | None) -> dict[str, list[dict[str, Any]]]:
        """Applies derived properties and defaults; returns nodes grouped by label, sorted by ID."""
        self._check_bridge_expectations()
        file_stats = git.files if git else {}
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node_id in sorted(self.nodes):
            node = dict(self.nodes[node_id])
            label = node.pop("label")
            if label == "External" and node_id not in self._referenced:
                continue  # a stub no edge uses (e.g. only named in a property): not worth a node
            if label == "Message":
                compiled = compile_template(node.get("template", ""), node.get("templateStyle", "dotnet"))
                node["regex"] = compiled.regex
                node["literalChars"] = compiled.literal_chars
            elif label == "Assembly":
                node["layer"], node["layerIndex"] = self.config.layer_of(node["name"])
            elif label == "File":
                stats = file_stats.get(node.get("path", "").lower())
                if stats:
                    node["commitCount"], node["lastCommitDate"] = stats
            elif label == "Method" and not node.get("isEntryPoint"):
                if node_id in self._ui_handlers:
                    node.update(isEntryPoint=True, entryKind="ui_handler", entrySource="derived", entryConfidence="exact")
                elif node_id in self._callbacks:
                    node.update(isEntryPoint=True, entryKind="native_callback", entrySource="derived",
                                entryConfidence=self._callbacks[node_id])
            for key, value in NODE_DEFAULTS.get(label, {}).items():
                node.setdefault(key, value)
            if node.get("file"):
                node["fileKey"] = node["file"].lower()
            node["commit"] = self.run.commit
            node["seenRun"] = self.run.run_id
            by_label[label].append(_canonical(node))
            self.report.node_counts[label] += 1
        return by_label

    def finalize_edge(self, edge: dict[str, Any]) -> dict[str, Any]:
        """Defaults and run stamp for one edge (applied right before it is written)."""
        if edge.get("type") in VIA_TYPES or edge.get("rel") in VIA_TYPES:
            edge.setdefault("via", "direct")
        edge.setdefault("confidence", "exact")
        edge["seenRun"] = self.run.run_id
        self.report.edge_counts[edge["type"]] += 1
        return _canonical(edge)

    def _check_bridge_expectations(self) -> None:
        """C# references into a C++/CLI assembly need the bridge's node; fail loudly otherwise (SPEC 5.3)."""
        missing = sorted(i for i, n in self.nodes.items() if n["label"] == "External" and n.get("expectedFrom") == "bridge")
        if missing:
            message = (f"{len(missing)} C++/CLI symbol(s) referenced from C# were not produced by the bridge "
                       f"extractor (first: {', '.join(missing[:5])}). Run the bridge on the built DLLs listed in "
                       f"cppcli_assemblies, or pass --allow-missing-bridge.")
            if self.allow_missing_bridge:
                self.report.warnings.append(message)
            else:
                self.report.errors.append(message)


def native_qualified_name(metadata_name: str) -> str:
    """C++/CLI metadata name -> C++ qualified name.

    "MiniPlant.Native.NativeStage.Home"  -> "MiniPlant::Native::NativeStage::Home"
    "MiniPlant.Native.NativeStage.{ctor}" -> "MiniPlant::Native::NativeStage::NativeStage"
    "std.vector<int,std::allocator<int> >.{dtor}" -> "std::vector<int,std::allocator<int> >::~vector"
    Only dots outside template arguments separate scopes.
    """
    if "::" in metadata_name and "." not in metadata_name:
        return metadata_name
    parts, depth, current = [], 0, []
    for char in metadata_name:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "." and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    if len(parts) > 1 and parts[-1] in ("{ctor}", "{dtor}"):
        class_name = parts[-2].split("<", 1)[0]
        parts[-1] = class_name if parts[-1] == "{ctor}" else "~" + class_name
    return "::".join(parts)


def _canonical(record: dict[str, Any]) -> dict[str, Any]:
    """Raw newlines in text would split CSV rows; store them as the two characters \\n (and \\r)."""
    for key, value in record.items():
        if isinstance(value, str) and ("\n" in value or "\r" in value or "\x00" in value):
            record[key] = value.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "")
    return record


def _lang_of(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"cs": "cs", "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "c": "cpp", "h": "cpp", "hpp": "cpp"}.get(suffix, "other")


@dataclass
class GitHistory:
    files: dict[str, tuple[int, str]]          # lower-case repo-relative path -> (commitCount, lastCommitDate)
    pairs: list[tuple[str, str, int]]          # (path a, path b, commits touching both)
