"""Symbol lookup: exact ID, then exact fully-qualified name, then exact short name, then full text.

Tools accept either an ID from find_symbol (always unambiguous) or a name. A name that matches several
symbols returns the candidates instead of guessing, so the agent can pick one and call again.
"""
from __future__ import annotations

import re
from typing import Any

from .graph import ReadOnlyGraph, compact, node_fields

SYMBOL_LABELS = ("Method", "Type", "Field", "Property", "Event", "NativeFunction", "NativeType", "NativeExport")
_LABEL_FILTER = " OR ".join(f"n:{label}" for label in SYMBOL_LABELS)
_FIELDS = node_fields("n")

_BY_ID = f"MATCH (n:Node {{id: $q}}) RETURN {_FIELDS} AS node"
_BY_FQN = f"MATCH (n:Node {{fqn: $q}}) WHERE {_LABEL_FILTER} RETURN {_FIELDS} AS node ORDER BY n.id LIMIT $limit"
_BY_NAME = f"MATCH (n:Node {{name: $q}}) WHERE {_LABEL_FILTER} RETURN {_FIELDS} AS node ORDER BY n.fqn, n.id LIMIT $limit"
_BY_SUFFIX = (f"MATCH (n:Node {{name: $name}}) WHERE ({_LABEL_FILTER}) AND (n.fqn ENDS WITH $suffix OR n.qualifiedName ENDS WITH $suffix) "
              f"RETURN {_FIELDS} AS node ORDER BY n.fqn, n.id LIMIT $limit")
_FULLTEXT = (f"CALL db.index.fulltext.queryNodes('symbol_text', $q, {{limit: $limit}}) YIELD node AS n, score "
             f"RETURN {_FIELDS} AS node, score ORDER BY score DESC, n.id")

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def find(graph: ReadOnlyGraph, query: str, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Ranked matches: [{..compact node.., "match": "id|fqn|name|suffix|fulltext"}]."""
    query = query.strip()
    results: dict[str, dict[str, Any]] = {}

    def add(rows: list[dict[str, Any]], how: str) -> None:
        for row in rows:
            node = compact(row["node"])
            if node and node["id"] not in results and (kind is None or _kind_matches(node, kind)):
                results[node["id"]] = {**node, "match": how}

    add(graph.query(_BY_ID, {"q": query}), "id")
    if not results:
        add(graph.query(_BY_FQN, {"q": query, "limit": limit}), "fqn")
    if not results:
        bare = query.split("(")[0]
        add(graph.query(_BY_NAME, {"q": bare, "limit": limit}), "name")
        if "." in bare or "::" in bare:
            # "StageBase.Move" or "NativeStage::Move": last segment is the name, the rest must match the end.
            name = re.split(r"\.|::", bare)[-1]
            suffix_dot = bare if bare.startswith(".") else "." + bare
            add(graph.query(_BY_SUFFIX, {"name": name, "suffix": suffix_dot, "limit": limit}), "suffix")
            add(graph.query(_BY_SUFFIX, {"name": name, "suffix": "::" + bare.replace(".", "::"), "limit": limit}), "suffix")
    if len(results) < limit:
        terms = [t for t in re.split(r"[\s.:()<>,]+", query) if t]
        if terms:
            lucene = " AND ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) + "*" for t in terms)
            add(graph.query(_FULLTEXT, {"q": lucene, "limit": limit}), "fulltext")
    return list(results.values())[:limit]


def resolve(graph: ReadOnlyGraph, symbol: str, kind: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(the one matching node, []) or (None, candidates). Only exact-id/fqn/name/suffix matches count as resolved."""
    matches = find(graph, symbol, kind=kind, limit=25)
    exact = [m for m in matches if m["match"] in ("id", "fqn", "name", "suffix")]
    if len(exact) == 1:
        return exact[0], []
    if not exact and len(matches) == 1:
        return matches[0], []
    return None, (exact or matches)[:15]


def unresolved(symbol: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"error": f"no symbol matches {symbol!r}", "hint": "try find_symbol with a shorter name"}
    return {"error": f"{symbol!r} is ambiguous: call again with one of these ids",
            "candidates": [{k: c[k] for k in ("id", "label", "fqn", "signature", "loc") if k in c} for c in candidates]}


def _kind_matches(node: dict[str, Any], kind: str) -> bool:
    wanted = kind.lower()
    return wanted in ((node.get("label") or "").lower(), (node.get("kind") or "").lower())
