"""The fixture's extracted graph equals tests/expected_edges.yaml (SPEC 11.1)."""
from pathlib import Path

import pytest
import yaml
from fixture_graph import comparable, describe, merged_graph

EXPECTED = yaml.safe_load((Path(__file__).parent / "expected_edges.yaml").read_text())


@pytest.fixture(scope="module")
def graph(extracted):
    return merged_graph()


def test_must_have_edges(graph):
    nodes, edges = graph
    actual = {describe(e) for e in edges}
    missing = [line for line in EXPECTED["must_have"] if line not in actual]
    assert not missing, "missing edges:\n" + "\n".join(missing)


def test_exact_edge_set(graph):
    nodes, edges = graph
    actual = set(comparable(edges, nodes))
    expected = set(EXPECTED["exact"])
    missing, unexpected = sorted(expected - actual), sorted(actual - expected)
    assert not missing and not unexpected, (
        "missing:\n  " + "\n  ".join(missing) + "\nunexpected:\n  " + "\n  ".join(unexpected))


@pytest.mark.parametrize("expected", EXPECTED["nodes"], ids=lambda n: n["id"])
def test_node_properties(graph, expected):
    nodes, _ = graph
    node = nodes.get(expected["id"])
    assert node is not None, f"no node {expected['id']}"
    for key, value in expected.items():
        assert node.get(key) == value, f"{expected['id']}.{key} = {node.get(key)!r}, expected {value!r}"


@pytest.mark.parametrize("expected", EXPECTED["messages"], ids=lambda m: m["template"])
def test_message_templates(graph, expected):
    nodes, edges = graph
    emitted = [nodes[e["end"]] for e in edges if e["type"] == "EMITS" and e["start"] == expected["emitter"]]
    match = [m for m in emitted if m["template"] == expected["template"]]
    assert match, f"{expected['emitter']} emits {[m['template'] for m in emitted]}"
    assert match[0]["kind"] == expected["kind"] and match[0].get("level") == expected.get("level")
    assert match[0]["regex"] and match[0]["literalChars"] > 0


def test_code_edges_carry_file_and_line(graph):
    nodes, edges = graph
    for edge in edges:
        if edge["type"] in ("CALLS", "READS", "WRITES", "EMITS", "REFERENCES_METHOD", "SUBSCRIBES", "READS_CONFIG") \
                and nodes[edge["start"]].get("lang") in ("cs", "cpp"):
            assert edge.get("file") and edge.get("line", 0) > 0, f"no location on {describe(edge)}"


def test_bridge_edges_carry_il_offsets(graph):
    nodes, edges = graph
    native_calls = [e for e in edges if e["type"] == "CALLS_NATIVE"]
    assert native_calls and all(e.get("ilOffset") is not None for e in native_calls)
