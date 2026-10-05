"""Finds the code that produced a log line or exception message (SPEC 9).

1. strip the configured log prefix, 2. take the three longest words, 3. ask the Lucene full-text
index for up to 200 candidate messages, 4. test each candidate's regex against the line, 5. rank:
regex matches first, then by literal characters (more literal text = more specific), then by id.
Everything is deterministic: no embeddings, no model calls.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .templates import search_tokens, strip_prefix

# Full-text pre-filter plus the emitting method(s) of each candidate message.
CANDIDATES_QUERY = """
CALL db.index.fulltext.queryNodes('message_text', $query, {limit: $limit}) YIELD node, score
WHERE $kind IS NULL OR node.kind = $kind
OPTIONAL MATCH (m)-[e:EMITS]->(node)
RETURN node.id AS id, node.template AS template, node.regex AS regex, node.literalChars AS literalChars,
       node.kind AS kind, node.level AS level, score,
       collect({id: m.id, name: m.name, fqn: m.fqn, signature: m.signature, file: e.file, line: e.line,
                lang: m.lang, assembly: m.assembly, exceptionType: e.exceptionType, via: e.via}) AS emitters
"""

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def lucene_query(tokens: list[str]) -> str:
    return " OR ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) for t in tokens)


def find_sources(
    run_query: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    text: str,
    prefix_regex: str | None,
    kind: str | None = None,
    limit: int = 10,
    candidate_limit: int = 200,
    exception_type: str | None = None,
) -> dict[str, Any]:
    """Ranked candidate messages for a log line. `run_query` executes a read-only Cypher query."""
    line = strip_prefix(text, prefix_regex)
    tokens = search_tokens(line)
    if not tokens:
        return {"query": line, "tokens": [], "results": [], "note": "no searchable words in the input"}

    rows = run_query(CANDIDATES_QUERY, {"query": lucene_query(tokens), "limit": candidate_limit, "kind": kind})
    ranked = []
    for row in rows:
        emitters = [e for e in row["emitters"] if e.get("id")]
        if exception_type:
            emitters = [e for e in emitters if _type_matches(e.get("exceptionType"), exception_type)]
            if not emitters:
                continue
        matched = bool(row["regex"]) and re.search(row["regex"], line) is not None
        ranked.append({
            "messageId": row["id"],
            "template": row["template"],
            "kind": row["kind"],
            "level": row["level"],
            "matched": matched,
            "literalChars": row["literalChars"] or 0,
            "score": round(row["score"], 3),
            "emitters": sorted(emitters, key=lambda e: (e.get("file") or "", e.get("line") or 0)),
        })

    # Deterministic ranking (SPEC 9, step 4).
    ranked.sort(key=lambda r: (not r["matched"], -r["literalChars"], r["messageId"]))
    return {"query": line, "tokens": tokens, "results": ranked[:limit]}


def _type_matches(actual: str | None, wanted: str) -> bool:
    """"InvalidOperationException" matches dn:T:System.InvalidOperationException."""
    if not actual:
        return False
    simple = actual.split(":")[-1]
    return simple == wanted or simple.endswith("." + wanted)
