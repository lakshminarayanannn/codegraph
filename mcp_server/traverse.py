"""Bounded breadth-first traversals, one level per query.

Variable-length Cypher patterns (`-[:CALLS*1..8]->`) enumerate every path and explode on large call
graphs. Instead each level is one indexed query over the current frontier; the BFS keeps the first
(shortest) way it reached each node, so results are deterministic and every caller appears once with
a shortest path. Neighbour lists are cached, so tools that run many traversals (diff_impact) query
each node's neighbours only once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import ReadOnlyGraph, brief, label, node_fields

# Relationship sets used by the tools. Only these strings ever reach Cypher (no user input).
CALLER_RELS = "CALLS|DISPATCHES_TO|REFERENCES_METHOD|SUBSCRIBES|CALLS_NATIVE|PINVOKES|RESOLVES_TO"
CALLEE_RELS = "CALLS|DISPATCHES_TO|REFERENCES_METHOD|CALLS_NATIVE|PINVOKES|RESOLVES_TO"
NATIVE_PATH_RELS = "CALLS|DISPATCHES_TO|REFERENCES_METHOD|PINVOKES|RESOLVES_TO|CALLS_NATIVE"

_STEP = {
    "in": "UNWIND $frontier AS fid MATCH (x:Node {{id: fid}})<-[r:{rels}]-(n:Node) WHERE NOT n:External "
          "RETURN fid AS from, type(r) AS rel, r.file AS file, r.line AS line, r.confidence AS confidence, "
          "r.kind AS kind, r.via AS via, {fields} AS node",
    "out": "UNWIND $frontier AS fid MATCH (x:Node {{id: fid}})-[r:{rels}]->(n:Node) WHERE NOT n:External "
           "RETURN fid AS from, type(r) AS rel, r.file AS file, r.line AS line, r.confidence AS confidence, "
           "r.kind AS kind, r.via AS via, {fields} AS node",
}

MAX_VISITED = 20000   # hard stop for one traversal, whatever the depth

# Hops that connect two views of the same call rather than making a new call: zero depth cost.
CONNECTOR_RELS = {"DISPATCHES_TO", "PINVOKES", "RESOLVES_TO"}


@dataclass
class Hit:
    node: dict[str, Any]
    depth: int
    parent: str | None            # the node one step closer to the start
    edge: dict[str, Any]          # rel, file, line, confidence, kind, via of the step from parent


@dataclass
class Traversal:
    start: list[str]
    direction: str                # "in" (callers: towards the start) or "out" (callees: away from it)
    hits: dict[str, Hit] = field(default_factory=dict)
    truncated: bool = False

    def path(self, node_id: str) -> list[str]:
        """The shortest path as readable hops, in call order:
        callers ("in"):  ["<caller>", "-CALLS@a.cs:12-> <callee>", ..., "-...-> <start>"]
        callees ("out"): ["<start>", "-CALLS@a.cs:12-> <callee>", ..., "-...-> <reached node>"]"""
        chain = []
        current: str | None = node_id
        while current is not None and current in self.hits:
            chain.append(current)
            current = self.hits[current].parent
        if self.direction == "in":
            # chain = [caller, ..., start]; the edge of each step is stored on the node farther from the start.
            return [label(self.hits[chain[0]].node)] + [
                f"-{_hop(self.hits[chain[i]].edge)}-> {label(self.hits[chain[i + 1]].node)}" for i in range(len(chain) - 1)]
        chain.reverse()  # [start, ..., reached]
        return [label(self.hits[chain[0]].node)] + [
            f"-{_hop(self.hits[node].edge)}-> {label(self.hits[node].node)}" for node in chain[1:]]

    def confidence(self, node_id: str) -> str:
        """heuristic if any step on the shortest path is heuristic."""
        current: str | None = node_id
        while current is not None and current in self.hits:
            hit = self.hits[current]
            if hit.edge.get("confidence") == "heuristic":
                return "heuristic"
            current = hit.parent
        return "exact"

    def entry(self, node_id: str, with_path: bool = False) -> dict[str, Any]:
        """One reached node as a tree entry: `next` is the neighbour one step towards the start (absent when that
        is the start itself), `rel`/`at` the edge to it. Follow `next` to rebuild any path; with_path adds it."""
        hit = self.hits[node_id]
        entry = {**brief(hit.node), "depth": hit.depth}
        if hit.parent is not None and hit.parent not in self.start:
            entry["next"] = hit.parent
        edge = hit.edge
        if edge.get("rel") and edge["rel"] != "CALLS":
            entry["rel"] = edge["rel"]
        if edge.get("file"):
            entry["at"] = f"{edge['file']}:{edge['line']}" if edge.get("line") else edge["file"]
        for key in ("kind", "via"):
            if edge.get(key) and edge[key] not in ("direct", "call"):
                entry[key] = edge[key]
        if self.confidence(node_id) == "heuristic":
            entry["confidence"] = "heuristic"
        if with_path:
            entry["path"] = self.path(node_id)
        return entry


class Traverser:
    def __init__(self, graph: ReadOnlyGraph):
        self.graph = graph
        self._cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def neighbours(self, ids: list[str], rels: str, direction: str) -> dict[str, list[dict[str, Any]]]:
        missing = [i for i in ids if (i, rels, direction) not in self._cache]
        for chunk_start in range(0, len(missing), 500):
            chunk = missing[chunk_start:chunk_start + 500]
            for i in chunk:
                self._cache[(i, rels, direction)] = []
            rows = self.graph.query(_STEP[direction].format(rels=rels, fields=node_fields("n")), {"frontier": chunk})
            for row in rows:
                self._cache[(row["from"], rels, direction)].append(row)
        return {i: self._cache[(i, rels, direction)] for i in ids}

    def bfs(self, start: list[str], rels: str, direction: str, max_depth: int,
            start_nodes: dict[str, dict[str, Any]] | None = None) -> Traversal:
        """Breadth-first by call depth. Connector hops (dispatch to an implementation, P/Invoke to its export,
        export to its function) cost nothing: depth counts real calls, so a path through an interface or into
        native code is not cut short by the plumbing between the calls."""
        traversal = Traversal(start=start, direction=direction)
        for node_id in start:
            traversal.hits[node_id] = Hit(node=(start_nodes or {}).get(node_id, {"id": node_id}), depth=0, parent=None, edge={})
        frontier = self._close_over_connectors(traversal, sorted(start), rels, direction, 0)
        for depth in range(1, max_depth + 1):
            if not frontier or self._full(traversal):
                break
            found = self._step(traversal, frontier, rels, direction, depth, connectors=False)
            frontier = self._close_over_connectors(traversal, found, rels, direction, depth)
        return traversal

    def _close_over_connectors(self, traversal: Traversal, frontier: list[str], rels: str, direction: str, depth: int) -> list[str]:
        """Adds everything reachable through connector hops at the same depth; returns the widened frontier."""
        widened, current = list(frontier), frontier
        while current and not self._full(traversal):
            current = self._step(traversal, current, rels, direction, depth, connectors=True)
            widened += current
        return sorted(set(widened))

    def _step(self, traversal: Traversal, frontier: list[str], rels: str, direction: str, depth: int, connectors: bool) -> list[str]:
        found: dict[str, Hit] = {}
        for origin, rows in self.neighbours(frontier, rels, direction).items():
            for row in sorted(rows, key=lambda r: (r["node"]["id"], r["rel"], r["line"] or 0)):
                node_id = row["node"]["id"]
                if (row["rel"] in CONNECTOR_RELS) != connectors or node_id in traversal.hits or node_id in found:
                    continue
                found[node_id] = Hit(node=row["node"], depth=depth, parent=origin,
                                     edge={k: row[k] for k in ("rel", "file", "line", "confidence", "kind", "via")})
        traversal.hits.update(found)
        return sorted(found)

    @staticmethod
    def _full(traversal: Traversal) -> bool:
        if len(traversal.hits) >= MAX_VISITED:
            traversal.truncated = True
        return traversal.truncated


def _hop(edge: dict[str, Any]) -> str:
    """"CALLS@src/a.cs:12" (call site), "DISPATCHES_TO", "CALLS_NATIVE@interop/x (heuristic)"..."""
    text = edge.get("rel") or "?"
    if edge.get("file"):
        text += "@" + (f"{edge['file']}:{edge['line']}" if edge.get("line") else edge["file"])
    if edge.get("via") and edge["via"] != "direct":
        text += f" via {edge['via']}"
    if edge.get("confidence") == "heuristic":
        text += " (heuristic)"
    return text
