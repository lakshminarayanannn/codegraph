"""The MCP tools (SPEC 10): fixed, parameterized, read-only queries over the code graph.

Every public method returns a JSON-serializable dict ending with index_commit / stale / indexed_at.
Symbol arguments take an id from find_symbol, a fully-qualified name, or a short name; an ambiguous
name returns candidates instead of a guess. Depths and limits are clamped server-side.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from codegraph_common import ids
from codegraph_common.config import Config
from logmatch.matcher import find_sources

from . import diff as gitdiff
from .graph import ReadOnlyGraph, brief, clamp, compact, node_fields
from .resolve import find, resolve, unresolved
from .traverse import CALLEE_RELS, CALLER_RELS, NATIVE_PATH_RELS, Traversal, Traverser

F = node_fields("n")
CODE_EXTENSIONS = (".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx", ".inl")


class Unresolved(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("error"))
        self.payload = payload


class Tools:
    def __init__(self, graph: ReadOnlyGraph, config: Config):
        self.graph = graph
        self.config = config
        self.max_depth = int(config.limits["max_depth"])
        self.max_results = int(config.limits["max_results"])

    # ---- plumbing ----------------------------------------------------------------------------

    def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        """Runs one tool and appends the index stamp; ambiguous symbols become a normal result."""
        try:
            payload = getattr(self, tool)(**arguments)
        except Unresolved as error:
            payload = error.payload
        except ValueError as error:
            payload = {"error": str(error)}
        payload.update(self.graph.index_info())
        return payload

    def _symbol(self, symbol: str, kind: str | None = None) -> dict[str, Any]:
        node, candidates = resolve(self.graph, symbol, kind)
        if node is None:
            raise Unresolved(unresolved(symbol, candidates))
        return node

    def _one(self, query: str, **params: Any) -> dict[str, Any] | None:
        rows = self.graph.query(query, params)
        return rows[0] if rows else None

    @staticmethod
    def _reached(traversal: Traversal, predicate: Any = None) -> list[tuple[str, Any]]:
        """Every node the traversal reached except the start, nearest first. Depth 0 = reached through a
        connector hop only (the interface method a call goes through, the export a P/Invoke resolves to)."""
        items = [(i, h) for i, h in traversal.hits.items()
                 if i not in traversal.start and (predicate is None or predicate(h.node))]
        return sorted(items, key=lambda item: (item[1].depth, item[1].node.get("fqn") or "", item[0]))

    # ---- navigation and understanding --------------------------------------------------------

    def find_symbol(self, name: str, kind: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {"query": name, "results": find(self.graph, name, kind, clamp(limit, 20, self.max_results))}

    def outline(self, target: str, limit: int = 200) -> dict[str, Any]:
        limit = clamp(limit, 200, 1000)
        normalized = target.replace("\\", "/").removeprefix("./")
        if "/" in normalized or normalized.lower().endswith(CODE_EXTENSIONS):
            return self._outline_file(normalized, limit)
        assembly = self._one("MATCH (a:Assembly {name: $q}) RETURN a.name AS name", q=target)
        if assembly:
            return self._outline_assembly(assembly["name"], limit)
        node = self._symbol(target)
        if node["label"] not in ("Type", "NativeType"):
            owner = self._one("MATCH (n:Node {id: $id})<-[:HAS_MEMBER]-(t) RETURN t.id AS id", id=node["id"])
            if owner is None:
                return {"target": node, "note": "not a type and has no containing type"}
            node = {"id": owner["id"]}
        return self._outline_type(node["id"], limit)

    def _outline_type(self, type_id: str, limit: int) -> dict[str, Any]:
        # COLLECT subqueries (Neo4j 5.6+) keep each list independent: no row multiplication between them.
        row = self._one(f"""
            MATCH (n:Node {{id: $id}})
            RETURN {F} AS type,
              COLLECT {{ MATCH (n)-[:HAS_MEMBER]->(m) WHERE NOT coalesce(m.isAccessor, false)
                         RETURN {node_fields('m')} ORDER BY m.lineStart, m.name LIMIT $limit }} AS members,
              COLLECT {{ MATCH (n)-[b:INHERITS|IMPLEMENTS|WRAPS]->(base) RETURN {{rel: type(b), id: base.id, fqn: base.fqn}} }}
              + COLLECT {{ MATCH (n)-[x:REFS_EXTERNAL]->(ext) WHERE x.rel IN ['INHERITS', 'IMPLEMENTS']
                           RETURN {{rel: x.rel, id: ext.id, fqn: ext.fqn, external: true}} }} AS bases,
              COLLECT {{ MATCH (d)-[:INHERITS|IMPLEMENTS]->(n) RETURN d.fqn ORDER BY d.fqn LIMIT 50 }} AS derived
            """, id=type_id, limit=limit)
        if row is None:
            return {"error": f"unknown type {type_id}"}
        return {
            "type": compact(row["type"]),
            "bases": [b for b in row["bases"] if b.get("id")],
            "derived": sorted(d for d in row["derived"] if d),
            "members": [compact(m) for m in row["members"] if m.get("id")],
        }

    def _outline_file(self, path: str, limit: int) -> dict[str, Any]:
        key = path.lower()
        rows = self.graph.query(f"""
            MATCH (f:File {{id: $fid}})<-[:DEFINED_IN]-(n)
            OPTIONAL MATCH (n)-[:HAS_MEMBER]->(m) WHERE m.fileKey = $key AND NOT coalesce(m.isAccessor, false)
            WITH n, m ORDER BY m.lineStart
            WITH n, collect({node_fields('m')})[..$limit] AS members
            RETURN {F} AS type, members ORDER BY n.lineStart
            """, {"fid": ids.file_id(path), "key": key, "limit": limit})
        free = self.graph.query(f"MATCH (n:NativeFunction {{fileKey: $key}}) WHERE n.typeId IS NULL "
                                f"RETURN {F} AS node ORDER BY n.lineStart LIMIT $limit", {"key": key, "limit": limit})
        if not rows and not free:
            return {"file": path, "error": "no indexed symbols in this file (check the path is repo-relative)"}
        return {
            "file": path,
            "types": [{"type": compact(r["type"]), "members": [compact(m) for m in r["members"] if m.get("id")]} for r in rows],
            "functions": [compact(r["node"]) for r in free],
        }

    def _outline_assembly(self, name: str, limit: int) -> dict[str, Any]:
        types = self.graph.query(f"""
            MATCH (a:Assembly {{name: $name}})-[:CONTAINS]->(n) WHERE n.typeId IS NULL
            RETURN {F} AS type, COUNT {{ (n)-[:HAS_MEMBER]->() }} AS members ORDER BY n.fqn LIMIT $limit
            """, {"name": name, "limit": limit})
        info = self._one("""
            MATCH (a:Assembly {name: $name}) OPTIONAL MATCH (a)-[r:REFERENCES]->(b:Assembly)
            RETURN a.kind AS kind, a.layer AS layer, a.targetFramework AS targetFramework, a.isTestAssembly AS isTestAssembly,
                   collect({name: b.name, kind: r.kind}) AS references
            """, name=name) or {}
        return {"assembly": name, **{k: v for k, v in info.items() if k != "references"},
                "references": sorted((r for r in info.get("references", []) if r.get("name")), key=lambda r: r["name"]),
                "types": [{**compact(t["type"]), "members": t["members"]} for t in types],
                "truncated": len(types) >= limit}

    def dependencies_of(self, symbol: str, level: str = "type") -> dict[str, Any]:
        node = self._symbol(symbol)
        rows = self.graph.query(f"""
            MATCH (s:Node {{id: $id}})
            OPTIONAL MATCH (s)-[:HAS_MEMBER]->(member)
            WITH s, [s] + collect(member) AS sources
            UNWIND sources AS src
            MATCH (src)-[r:CALLS|READS|WRITES|REFERENCES_METHOD|INHERITS|IMPLEMENTS|PINVOKES|CALLS_NATIVE|WRAPS|READS_CONFIG|REFS_EXTERNAL]->(n)
            WHERE n.id <> s.id AND coalesce(n.typeId, '') <> s.id
            RETURN coalesce(r.rel, type(r)) AS rel, n:External AS external, {F} AS node, count(*) AS uses
            """, {"id": node["id"]})
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            target = row["node"]
            key = target.get("assembly") or "?" if level == "assembly" else _type_of(target)
            group = groups.setdefault(key, {"target": key, "external": row["external"], "uses": 0, "rels": Counter(), "examples": []})
            group["uses"] += row["uses"]
            group["rels"][row["rel"]] += row["uses"]
            if len(group["examples"]) < 5:
                group["examples"].append(target.get("fqn") or target["id"])
        ordered = sorted(groups.values(), key=lambda g: (g["external"], -g["uses"], g["target"]))
        for group in ordered:
            group["rels"] = dict(group["rels"])
        return {"symbol": node, "level": level,
                "dependencies": [g for g in ordered if not g["external"]],
                "external": [{k: g[k] for k in ("target", "uses")} for g in ordered if g["external"]][:30]}

    def callers_of(self, symbol: str, max_depth: int = 4, limit: int = 100) -> dict[str, Any]:
        node = self._symbol(symbol)
        depth = clamp(max_depth, 4, self.max_depth)
        traversal = Traverser(self.graph).bfs([node["id"]], CALLER_RELS, "in", depth)
        reached = self._reached(traversal)
        limit = clamp(limit, 100, self.max_results)
        return {"symbol": node, "max_depth": depth, "total": len(reached), "truncated": traversal.truncated or len(reached) > limit,
                "callers": [traversal.entry(i) for i, _ in reached[:limit]]}

    def callees_of(self, symbol: str, max_depth: int = 3, limit: int = 200) -> dict[str, Any]:
        node = self._symbol(symbol)
        depth = clamp(max_depth, 3, min(6, self.max_depth))
        traversal = Traverser(self.graph).bfs([node["id"]], CALLEE_RELS, "out", depth)
        reached = self._reached(traversal)
        limit = clamp(limit, 200, self.max_results)
        return {"symbol": node, "max_depth": depth, "total": len(reached), "truncated": traversal.truncated or len(reached) > limit,
                "callees": [traversal.entry(i) for i, _ in reached[:limit]]}

    def implementations_of(self, symbol: str) -> dict[str, Any]:
        node = self._symbol(symbol)
        if node["label"] in ("Type", "NativeType"):
            traversal = Traverser(self.graph).bfs([node["id"]], "INHERITS|IMPLEMENTS", "in", 10)
            return {"symbol": node, "implementations": [traversal.entry(i) for i, _ in self._reached(traversal)]}
        rows = self.graph.query(f"MATCH (s:Node {{id: $id}})-[r:DISPATCHES_TO]->(n) RETURN {F} AS node, r.confidence AS confidence "
                                f"ORDER BY n.fqn", {"id": node["id"]})
        declared = self.graph.query(f"MATCH (s:Node {{id: $id}})-[r:IMPLEMENTS_METHOD|OVERRIDES]->(n) "
                                    f"RETURN type(r) AS rel, {F} AS node ORDER BY n.fqn", {"id": node["id"]})
        return {"symbol": node,
                "implementations": [{**compact(r["node"]), "confidence": r["confidence"]} for r in rows],
                "implements_or_overrides": [{"rel": r["rel"], **compact(r["node"])} for r in declared]}

    def path_to_native(self, symbol: str, max_depth: int = 8) -> dict[str, Any]:
        node = self._symbol(symbol)
        depth = clamp(max_depth, 8, self.max_depth)
        traversal = Traverser(self.graph).bfs([node["id"]], NATIVE_PATH_RELS, "out", depth)
        native = self._reached(traversal, lambda n: any(l in ("NativeFunction", "NativeExport") for l in n.get("labels", [])))
        # Report the first native node of each path (where execution crosses into native code).
        crossings = [i for i, h in native if not _is_native(traversal.hits[h.parent].node)]
        return {"symbol": node, "max_depth": depth, "native_reached": len(native),
                "crossings": [traversal.entry(i, with_path=True) for i in crossings[:50]]}

    def entry_points(self, kind: str | None = None, limit: int = 200) -> dict[str, Any]:
        limit = clamp(limit, 200, 1000)
        rows = self.graph.query(f"""
            MATCH (n:Method) WHERE n.isEntryPoint = true AND ($kind IS NULL OR n.entryKind = $kind)
            RETURN {F} AS node, n.entrySource AS source, n.entryConfidence AS confidence
            ORDER BY n.entryKind, n.fqn LIMIT $limit
            """, {"kind": kind, "limit": limit})
        counts = self.graph.query("MATCH (n:Method) WHERE n.isEntryPoint = true RETURN n.entryKind AS kind, count(*) AS n ORDER BY kind")
        return {"kind": kind, "counts": {r["kind"]: r["n"] for r in counts},
                "entry_points": [{**brief(r["node"]), **({"confidence": r["confidence"]} if r["confidence"] else {})} for r in rows]}

    def entry_points_reaching(self, symbol: str, max_depth: int = 8) -> dict[str, Any]:
        node = self._symbol(symbol)
        depth = clamp(max_depth, 8, self.max_depth)
        traversal = Traverser(self.graph).bfs([node["id"]], CALLER_RELS, "in", depth)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node_id, hit in self._reached(traversal, lambda n: n.get("isEntryPoint")):
            grouped[hit.node.get("entryKind") or "unknown"].append(traversal.entry(node_id, with_path=True))
        return {"symbol": node, "max_depth": depth, "truncated": traversal.truncated,
                "by_kind": {kind: {"count": len(items), "entry_points": items[:20]} for kind, items in sorted(grouped.items())}}

    def assembly_graph(self, layer: str | None = None, include_transitive: bool = False) -> dict[str, Any]:
        rows = self.graph.query("""
            MATCH (a:Assembly) OPTIONAL MATCH (a)-[r:REFERENCES]->(b:Assembly)
            WHERE $transitive OR r.kind <> 'transitive'
            RETURN a.name AS name, a.kind AS kind, a.layer AS layer, a.layerIndex AS layerIndex, a.isTestAssembly AS test,
                   collect(CASE WHEN b IS NULL THEN NULL ELSE {to: b.name, kind: r.kind, layer: b.layer} END) AS refs
            ORDER BY coalesce(a.layerIndex, 99), a.name
            """, {"transitive": include_transitive})
        assemblies = []
        for row in rows:
            refs = [r for r in row["refs"] if r]
            if layer and row["layer"] != layer and not any(r["layer"] == layer for r in refs):
                continue
            assemblies.append({"name": row["name"], "kind": row["kind"], "layer": row["layer"], "test": row["test"],
                               "references": sorted(refs, key=lambda r: r["to"])})
        return {"layers": [l["name"] for l in self.config.layers], "assemblies": assemblies}

    # ---- change planning, review and testing -------------------------------------------------

    def impact_of_change(self, symbol: str, change_kind: str = "behaviour", max_depth: int = 4) -> dict[str, Any]:
        node = self._symbol(symbol)
        if change_kind not in ("signature", "removal", "behaviour"):
            raise ValueError("change_kind must be signature, removal or behaviour")
        traverser = Traverser(self.graph)
        if change_kind == "behaviour":
            depth = clamp(max_depth, 4, self.max_depth)
            traversal = traverser.bfs([node["id"]], CALLER_RELS, "in", depth)
            reached = self._reached(traversal)
            return {"symbol": node, "change_kind": change_kind, "max_depth": depth,
                    "note": "transitive callers MAY be affected (behaviour change keeps every caller compiling)",
                    "affected_count": len(reached),
                    "assemblies": dict(Counter(h.node.get("assembly") for _, h in reached).most_common()),
                    "entry_points": [traversal.entry(i, with_path=True) for i, h in reached if h.node.get("isEntryPoint")][:50],
                    "tests": [traversal.entry(i) for i, h in reached if h.node.get("isTest")][:50],
                    "affected": [traversal.entry(i) for i, _ in reached[:150]]}

        # signature / removal: the whole override/implementation family changes together, and every
        # direct user of any member of the family must change.
        family = self._family(traverser, node["id"])
        users = traverser.neighbours(sorted(family), "CALLS|REFERENCES_METHOD|READS|WRITES|INHERITS|IMPLEMENTS", "in")
        must_change: dict[str, dict[str, Any]] = {}
        for target, rows in users.items():
            for row in rows:
                user = row["node"]
                if user["id"] in family:
                    continue
                entry = must_change.setdefault(user["id"], {**brief(user), "assembly": user.get("assembly"), "uses": []})
                entry["uses"].append({"of": target, "rel": row["rel"], "at": f"{row['file']}:{row['line']}" if row["file"] else None})
        family_nodes = self.graph.query(f"UNWIND $ids AS i MATCH (n:Node {{id: i}}) RETURN {F} AS node ORDER BY n.fqn", {"ids": sorted(family)})
        changed = sorted(must_change.values(), key=lambda e: (e.get("assembly") or "", e.get("fqn") or ""))
        return {"symbol": node, "change_kind": change_kind,
                "note": "these MUST change: every override/implementation in the family and every direct user",
                "family": [compact(r["node"]) for r in family_nodes],
                "assemblies": sorted({e.get("assembly") for e in changed} | {r["node"].get("assembly") for r in family_nodes} - {None}),
                "tests": [e for e in changed if e.get("isTest")],
                "must_change": changed[:300], "total": len(changed)}

    def _family(self, traverser: Traverser, start: str) -> set[str]:
        """Connected component over IMPLEMENTS_METHOD / OVERRIDES (both directions)."""
        family, frontier = {start}, [start]
        while frontier and len(family) < 500:
            found = set()
            for direction in ("in", "out"):
                for rows in traverser.neighbours(frontier, "IMPLEMENTS_METHOD|OVERRIDES", direction).values():
                    found.update(r["node"]["id"] for r in rows)
            frontier = sorted(found - family)
            family |= found
        return family

    def diff_impact(self, base_ref: str, head_ref: str = "HEAD", max_depth: int = 8) -> dict[str, Any]:
        depth = clamp(max_depth, 8, self.max_depth)
        changes = gitdiff.changed_ranges(self.config.repo_root, base_ref, head_ref)
        traverser = Traverser(self.graph)
        changed_methods: list[dict[str, Any]] = []
        changed_declarations: list[dict[str, Any]] = []
        unmapped: list[str] = []
        for change in changes:
            symbols = self._symbols_in_file(change.path)
            hits = [s for s in symbols if any(s["line"] <= end and (s["lineEnd"] or s["line"]) >= start
                                              for start, end in change.ranges)]
            if not hits:
                unmapped.append(change.path)
            for symbol in hits:
                view = {**compact(symbol), "change": change.status}
                (changed_methods if symbol["callable"] else changed_declarations).append(view)

        truncated = len(changed_methods) > 300
        results = []
        for method in changed_methods[:300]:
            traversal = traverser.bfs([method["id"]], CALLER_RELS, "in", depth)
            reached = self._reached(traversal)
            tests = [h.node for _, h in reached if h.node.get("isTest")]
            entries = Counter(h.node.get("entryKind") for _, h in reached if h.node.get("isEntryPoint"))
            if method.get("isEntryPoint"):
                entries[method.get("entryKind")] += 1
            results.append({**method, "entry_points": dict(entries), "tests": len(tests),
                            "test_examples": sorted({t.get("fqn") for t in tests})[:5]})
        head = gitdiff_head(self.config, head_ref)
        index = self.graph.index_info()
        warning = None
        if head and index.get("index_commit") not in (None, "nogit", head):
            warning = f"the index is at {index.get('index_commit')}, not {head_ref} ({head}): line ranges may be off"
        return {"base": base_ref, "head": head_ref, "files_changed": len(changes), "warning": warning,
                "changed_methods": results, "truncated": truncated,
                "public_api_changed": [r["fqn"] for r in results if r.get("isPublicApi")],
                "untested": [r["fqn"] for r in results if r["tests"] == 0],
                "changed_declarations": changed_declarations[:100], "unmapped_files": unmapped[:100]}

    def _symbols_in_file(self, path: str) -> list[dict[str, Any]]:
        key = path.lower()
        rows = self.graph.query(f"""
            MATCH (n:Method {{fileKey: $key}}) RETURN {F} AS node, n.lineEnd AS lineEnd, true AS callable
            UNION
            MATCH (n:NativeFunction {{fileKey: $key}}) RETURN {F} AS node, n.lineEnd AS lineEnd, true AS callable
            UNION
            MATCH (:File {{id: $fid}})<-[:DEFINED_IN]-(:Node)-[:HAS_MEMBER]->(n) WHERE n.fileKey = $key AND (n:Field OR n:Property OR n:Event)
            RETURN {F} AS node, n.lineEnd AS lineEnd, false AS callable
            """, {"key": key, "fid": ids.file_id(path)})
        return [{**r["node"], "lineEnd": r["lineEnd"], "callable": r["callable"]} for r in rows
                if r["node"].get("line") and not r["node"].get("isImplicit")]

    def tests_reaching(self, symbol: str, max_depth: int = 8, limit: int = 100) -> dict[str, Any]:
        node = self._symbol(symbol)
        depth = clamp(max_depth, 8, self.max_depth)
        traversal = Traverser(self.graph).bfs([node["id"]], CALLER_RELS, "in", depth)
        tests = self._reached(traversal, lambda n: n.get("isTest"))
        limit = clamp(limit, 100, self.max_results)
        return {"symbol": node, "max_depth": depth, "total": len(tests), "truncated": traversal.truncated,
                "tests": [traversal.entry(i, with_path=True) for i, _ in tests[:limit]]}

    def co_changed_files(self, file: str, limit: int = 30) -> dict[str, Any]:
        path = file.replace("\\", "/").removeprefix("./")
        rows = self.graph.query("""
            MATCH (f:File {id: $fid})-[r:CO_CHANGES_WITH]-(o:File)
            RETURN o.path AS file, r.count AS commits_together, o.commitCount AS commits ORDER BY r.count DESC, o.path LIMIT $limit
            """, {"fid": ids.file_id(path), "limit": clamp(limit, 30, self.max_results)})
        info = self._one("MATCH (f:File {id: $fid}) RETURN f.commitCount AS commits, f.lastCommitDate AS last", fid=ids.file_id(path))
        return {"file": path, "commits": (info or {}).get("commits"), "last_commit": (info or {}).get("last"),
                "confidence": "heuristic", "note": "historical correlation from git, not a code dependency",
                "co_changed": rows}

    # ---- architecture health -----------------------------------------------------------------

    def layer_violations(self, examples: int = 5) -> dict[str, Any]:
        rows = self.graph.query("""
            MATCH (a:Assembly)-[r:REFERENCES]->(b:Assembly)
            WHERE a.layerIndex IS NOT NULL AND b.layerIndex IS NOT NULL AND a.layerIndex < b.layerIndex AND r.kind <> 'transitive'
            RETURN a.name AS from, a.layer AS fromLayer, b.name AS to, b.layer AS toLayer, r.kind AS kind ORDER BY a.name, b.name
            """)
        for row in rows:
            row["evidence"] = self.graph.query("""
                MATCH (n:Method {assembly: $a})-[r:CALLS|READS|WRITES|REFERENCES_METHOD|INHERITS|IMPLEMENTS]->(t:Node {assembly: $b})
                RETURN n.fqn AS from, type(r) AS rel, t.fqn AS to, r.file + ':' + toString(r.line) AS at ORDER BY n.fqn, t.fqn LIMIT $k
                """, {"a": row["from"], "b": row["to"], "k": clamp(examples, 5, 50)})
        return {"layers": [l["name"] for l in self.config.layers], "violations": rows,
                "note": "a lower layer must not depend on a higher one (layers in codegraph.yaml, lowest first)"}

    def unreferenced_symbols(self, assembly: str | None = None, kind: str = "method", limit: int = 200) -> dict[str, Any]:
        limit = clamp(limit, 200, 1000)
        if kind == "type":
            rows = self.graph.query(f"""
                MATCH (n:Type) WHERE ($asm IS NULL OR n.assembly = $asm) AND n.accessibility <> 'public'
                  AND n.kind <> 'delegate'
                  AND NOT EXISTS {{ (n)<-[:INHERITS|IMPLEMENTS]-() }}
                  AND NOT EXISTS {{ MATCH (n)-[:HAS_MEMBER]->(e) WHERE e.isEntryPoint OR e.isTest }}
                  AND NOT EXISTS {{ MATCH (n)-[:HAS_MEMBER]->(m)<-[:CALLS|READS|WRITES|REFERENCES_METHOD|DISPATCHES_TO]-(c)
                                    WHERE coalesce(c.typeId, '') <> n.id }}
                RETURN {F} AS node ORDER BY n.assembly, n.fqn LIMIT $limit
                """, {"asm": assembly, "limit": limit})
        else:
            rows = self.graph.query(f"""
                MATCH (n:Method) WHERE ($asm IS NULL OR n.assembly = $asm) AND n.accessibility <> 'public'
                  AND NOT n.isEntryPoint AND NOT n.isTest AND NOT n.isImplicit
                  AND NOT (n.isCtor AND n.isStatic)
                  AND coalesce(size(n.attributes), 0) = 0
                  AND NOT EXISTS {{ (n)<-[:CALLS|REFERENCES_METHOD|DISPATCHES_TO]-() }}
                  AND NOT EXISTS {{ (n)-[:OVERRIDES|IMPLEMENTS_METHOD]->() }}
                  AND NOT EXISTS {{ (n)-[x:REFS_EXTERNAL]->() WHERE x.rel IN ['OVERRIDES', 'IMPLEMENTS_METHOD'] }}
                RETURN {F} AS node ORDER BY n.assembly, n.fqn LIMIT $limit
                """, {"asm": assembly, "limit": limit})
        return {"kind": kind, "assembly": assembly, "candidates": [brief(r["node"]) for r in rows],
                "note": "candidates only: code used through reflection, DI, XAML or serialization also appears here (SPEC 12)"}

    def hotspots(self, metric: str = "fan_in", limit: int = 50) -> dict[str, Any]:
        limit = clamp(limit, 50, self.max_results)
        if metric == "churn":
            rows = self.graph.query("MATCH (f:File) WHERE f.commitCount IS NOT NULL RETURN f.path AS file, f.commitCount AS commits, "
                                    "f.lastCommitDate AS last ORDER BY f.commitCount DESC, f.path LIMIT $limit", {"limit": limit})
            return {"metric": metric, "results": rows}
        if metric not in ("fan_in", "fan_out"):
            raise ValueError("metric must be fan_in, fan_out or churn")
        pattern = "(n)<-[:CALLS|REFERENCES_METHOD|DISPATCHES_TO]-()" if metric == "fan_in" else "(n)-[:CALLS|REFERENCES_METHOD]->()"
        rows = self.graph.query(f"""
            MATCH (n:Method) WITH n, COUNT {{ {pattern} }} AS degree WHERE degree > 0
            RETURN {F} AS node, degree ORDER BY degree DESC, n.fqn LIMIT $limit
            """, {"limit": limit})
        return {"metric": metric, "results": [{**brief(r["node"]), metric: r["degree"]} for r in rows]}

    # ---- debugging, data and config flow -----------------------------------------------------

    def find_log_source(self, log_line: str, limit: int = 10) -> dict[str, Any]:
        return find_sources(self.graph.query, log_line, self.config.log_prefix_regex, kind="log",
                            limit=clamp(limit, 10, 50))

    def find_exception_source(self, message: str, exception_type: str | None = None, limit: int = 10) -> dict[str, Any]:
        return find_sources(self.graph.query, message, self.config.log_prefix_regex, kind="exception",
                            limit=clamp(limit, 10, 50), exception_type=exception_type)

    def writers_of(self, symbol: str) -> dict[str, Any]:
        return self._accesses(symbol, "WRITES")

    def readers_of(self, symbol: str) -> dict[str, Any]:
        return self._accesses(symbol, "READS")

    def _accesses(self, symbol: str, rel: str) -> dict[str, Any]:
        node = self._symbol(symbol)
        rows = self.graph.query(f"""
            MATCH (s:Node {{id: $id}})<-[r:{rel}]-(n)
            RETURN {F} AS node, r.file AS file, r.line AS line, r.count AS count, r.via AS via, r.implicit AS implicit
            ORDER BY n.fqn
            """, {"id": node["id"]})
        return {"symbol": node, rel.lower(): [
            {**brief(r["node"]), "at": f"{r['file']}:{r['line']}" if r["file"] else None,
             **({"count": r["count"]} if r["count"] else {}), **({"via": r["via"]} if r["via"] not in (None, "direct") else {}),
             **({"initializer": True} if r["implicit"] else {})} for r in rows]}

    def config_usage(self, key: str) -> dict[str, Any]:
        rows = self.graph.query(f"""
            MATCH (c:ConfigKey) WHERE c.key = $key OR (NOT EXISTS {{ MATCH (:ConfigKey {{key: $key}}) }} AND toLower(c.key) CONTAINS toLower($key))
            MATCH (c)<-[r:READS_CONFIG]-(n)
            RETURN c.key AS key, c.accessor AS accessor, {F} AS node, r.file AS file, r.line AS line ORDER BY c.key, n.fqn LIMIT 500
            """, {"key": key})
        usage: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = usage.setdefault(row["key"], {"key": row["key"], "accessor": row["accessor"], "readers": []})
            entry["readers"].append({**brief(row["node"]), "at": f"{row['file']}:{row['line']}"})
        return {"query": key, "keys": list(usage.values())}

    # ---- housekeeping ------------------------------------------------------------------------

    def index_status(self) -> dict[str, Any]:
        import json

        row = self._one("MATCH (r:IndexRun) RETURN r ORDER BY r.startedAt DESC LIMIT 1")
        runs = self._one("MATCH (r:IndexRun) RETURN count(r) AS n") or {"n": 0}
        if not row:
            return {"indexed": False, "hint": "run scripts/full_index first"}
        run = dict(row["r"])
        for key in ("counts", "warnings", "toolVersions"):
            if isinstance(run.get(key), str):
                run[key] = json.loads(run[key])
        return {"indexed": True, "runs": runs["n"], "last_run": run}


def gitdiff_head(config: Config, ref: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(["git", "-C", str(config.repo_root), "rev-parse", ref], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _type_of(node: dict[str, Any]) -> str:
    """Containing type's name for grouping: "Ns.Type.Member" -> "Ns.Type", "ns::Cls::fn" -> "ns::Cls"."""
    labels = node.get("labels") or []
    fqn = node.get("fqn") or node["id"]
    if any(l in ("Type", "NativeType", "ConfigKey", "Assembly") for l in labels):
        return fqn
    if "::" in fqn:
        return fqn.rsplit("::", 1)[0]
    return fqn.rsplit(".", 1)[0] if "." in fqn else fqn


def _is_native(node: dict[str, Any]) -> bool:
    return any(l in ("NativeFunction", "NativeExport") for l in node.get("labels") or [])
