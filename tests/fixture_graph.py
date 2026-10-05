"""Shared helpers for the fixture tests: run the merge over the extractor output (no Neo4j needed) and
describe edges as one readable line each, the format of tests/expected_edges.yaml."""
from __future__ import annotations

from itertools import chain
from pathlib import Path

from codegraph_common.config import Config
from loader.merge import Merger, RunInfo

REPO = Path(__file__).resolve().parent.parent
FIXTURE_CONFIG = REPO / "fixtures" / "MiniPlant" / "codegraph.yaml"

# Owners whose output differs by platform: the C++/CLI DLL is the C# stand-in off Windows. Their edges are
# checked through the must_have list only (tests/expected_edges.yaml).
PLATFORM_OWNERS = {"MiniPlant.Interop"}


def merged_graph(config: Config | None = None) -> tuple[dict[str, dict], list[dict]]:
    """(nodes by id, edges) exactly as the loader would import them."""
    config = config or Config(FIXTURE_CONFIG)
    merger = Merger(config, RunInfo("test", "test", "test", "test"), allow_missing_bridge=True)
    merger.load_nodes()
    edges = [merger.finalize_edge(e) for e in chain(merger.edges(), merger.derived_edges(None))]
    nodes = {n["id"]: {**n, "label": label} for label, records in merger.finalize_nodes(None).items() for n in records}
    return nodes, edges


def describe(edge: dict) -> str:
    """"CALLS dn:M:A -> dn:M:B [kind=call via=lambda]": the identity of an edge, without locations."""
    details = []
    for key in ("kind", "via", "rel", "callbackKind", "handler", "level", "exceptionType"):
        value = edge.get(key)
        if value is not None and value != "direct" and not (edge["type"] == "REFERENCES" and key == "kind"):
            details.append(f"{key}={value}")
    suffix = f" [{' '.join(details)}]" if details else ""
    return f"{edge['type']} {edge['start']} -> {edge['end']}{suffix}"


def comparable(edges: list[dict], nodes: dict[str, dict]) -> list[str]:
    """Edges between indexed (non-External) nodes, minus platform-specific owners and transitive references."""
    result = set()
    for edge in edges:
        if edge["type"] == "REFS_EXTERNAL" or edge.get("owner") in PLATFORM_OWNERS:
            continue
        if edge["type"] == "REFERENCES" and edge.get("kind") == "transitive":
            continue
        if nodes.get(edge["end"], {}).get("label") == "External" or nodes.get(edge["start"], {}).get("assembly") in PLATFORM_OWNERS:
            continue
        result.add(describe(edge))
    return sorted(result)
