"""Merge step (SPEC 8.1): node priority, stubs, REFS_EXTERNAL, dispatch derivation, bridge name resolution."""
import json
from itertools import chain
from pathlib import Path

import pytest

from codegraph_common.config import Config
from loader.csvout import CsvWriter, _cell
from loader.merge import Merger, RunInfo, native_qualified_name


def write(folder: Path, owner: str, nodes: list[dict], edges: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{owner}.nodes.jsonl").write_text("".join(json.dumps(n) + "\n" for n in nodes))
    (folder / f"{owner}.edges.jsonl").write_text("".join(json.dumps(e) + "\n" for e in edges))


@pytest.fixture
def config(tmp_path: Path) -> Config:
    (tmp_path / "codegraph.yaml").write_text(
        "repo_root: .\noutput_dir: out\nlayers:\n  - {name: low, assemblies: ['Low*']}\n  - {name: high, assemblies: ['High*']}\n")
    return Config(tmp_path / "codegraph.yaml")


def merge(config: Config, allow_missing_bridge: bool = True):
    merger = Merger(config, RunInfo("run1", "abc", "now", "full"), allow_missing_bridge)
    merger.load_nodes()
    edges = [merger.finalize_edge(e) for e in chain(merger.edges(), merger.derived_edges(None))]
    nodes = {n["id"]: {**n, "label": label} for label, records in merger.finalize_nodes(None).items() for n in records}
    return merger, nodes, edges


def method(node_id: str, assembly: str = "Low", **extra) -> dict:
    return {"label": "Method", "id": node_id, "name": node_id.split(".")[-1], "assembly": assembly, **extra}


def edge(rel: str, start: str, end: str, owner: str = "Low", **extra) -> dict:
    return {"type": rel, "start": start, "end": end, "owner": owner, **extra}


def test_real_node_beats_stub_and_source_beats_bridge(config):
    out = config.output_dir
    write(out / "csharp", "Low", [method("dn:M:A.f"), {"label": "External", "id": "dn:M:B.g", "name": "g"}], [])
    write(out / "bridge", "B", [method("dn:M:B.g", "B", lang="cppcli"), method("dn:M:A.f", "B", lang="cppcli")], [])
    _, nodes, _ = merge(config)
    assert nodes["dn:M:B.g"]["label"] == "Method" and nodes["dn:M:B.g"]["lang"] == "cppcli"
    assert nodes["dn:M:A.f"]["assembly"] == "Low"  # the C# definition wins over the bridge's copy


def test_edges_into_unknown_or_external_nodes_become_refs_external(config):
    out = config.output_dir
    write(out / "csharp", "Low", [method("dn:M:A.f"), {"label": "External", "id": "dn:M:System.Console.WriteLine", "name": "WriteLine"}],
          [edge("CALLS", "dn:M:A.f", "dn:M:System.Console.WriteLine", kind="call"),
           edge("CALLS", "dn:M:A.f", "dn:M:Nowhere.h", kind="call"),
           edge("REFERENCES", "asm:Low", "asm:mscorlib")])
    merger, nodes, edges = merge(config)
    calls = {e["end"]: e for e in edges}
    assert calls["dn:M:System.Console.WriteLine"]["type"] == "REFS_EXTERNAL"
    assert calls["dn:M:System.Console.WriteLine"]["rel"] == "CALLS"
    assert nodes["dn:M:Nowhere.h"]["label"] == "External"
    assert "asm:mscorlib" not in calls  # references to assemblies nobody indexed are dropped
    assert merger.report.to_dict()["counters"]["stubs_created"] == 1


def test_dispatch_is_derived_transitively(config):
    out = config.output_dir
    ids = ["dn:M:I.m", "dn:M:Base.m", "dn:M:Mid.m", "dn:M:Leaf.m"]
    write(out / "csharp", "Low", [method(i) for i in ids], [
        edge("IMPLEMENTS_METHOD", "dn:M:Base.m", "dn:M:I.m"),
        edge("OVERRIDES", "dn:M:Mid.m", "dn:M:Base.m"),
        edge("OVERRIDES", "dn:M:Leaf.m", "dn:M:Mid.m"),
    ])
    _, _, edges = merge(config)
    dispatch = {(e["start"], e["end"]) for e in edges if e["type"] == "DISPATCHES_TO"}
    assert dispatch == {("dn:M:I.m", "dn:M:Base.m"), ("dn:M:I.m", "dn:M:Mid.m"), ("dn:M:I.m", "dn:M:Leaf.m"),
                        ("dn:M:Base.m", "dn:M:Mid.m"), ("dn:M:Base.m", "dn:M:Leaf.m"), ("dn:M:Mid.m", "dn:M:Leaf.m")}


def test_bridge_names_resolve_against_native_symbols(config):
    out = config.output_dir
    native = [
        {"label": "NativeFunction", "id": "cpp:ctor", "qualifiedName": "Ns::Cls::Cls", "paramCount": 0, "assembly": "Nat"},
        {"label": "NativeFunction", "id": "cpp:f1", "qualifiedName": "Ns::Cls::Go", "paramCount": 1, "assembly": "Nat"},
        {"label": "NativeFunction", "id": "cpp:f2", "qualifiedName": "Ns::Cls::Go", "paramCount": 2, "assembly": "Nat"},
        {"label": "NativeFunction", "id": "cpp:m", "mangledName": "?X@@YAXXZ", "qualifiedName": "X", "assembly": "Nat"},
        {"label": "NativeType", "id": "cpp:t", "fqn": "Ns::Cls", "assembly": "Nat"},
    ]
    write(out / "native", "Nat", native, [])
    write(out / "bridge", "Wrap", [method("dn:M:W.a", "Wrap"), {"label": "Type", "id": "dn:T:W", "assembly": "Wrap"}], [
        edge("CALLS_NATIVE", "dn:M:W.a", "cpp:unresolved:Ns.Cls.{ctor}", "Wrap", nativeName="Ns.Cls.{ctor}", nativeParamCount=0),
        edge("CALLS_NATIVE", "dn:M:W.a", "cpp:unresolved:Ns.Cls.Go", "Wrap", nativeName="Ns.Cls.Go", nativeParamCount=2),
        edge("CALLS_NATIVE", "dn:M:W.a", "cpp:unresolved:?X@@YAXXZ", "Wrap", nativeName="?X@@YAXXZ", nativeParamCount=0),
        edge("CALLS_NATIVE", "dn:M:W.a", "cpp:unresolved:Gone.Fn", "Wrap", nativeName="Gone.Fn", nativeParamCount=0),
        edge("WRAPS", "dn:T:W", "cpp:unresolved-type:Ns.Cls", "Wrap", nativeName="Ns.Cls"),
    ])
    merger, nodes, edges = merge(config)
    targets = {e["end"]: e.get("confidence") for e in edges if e["type"] in ("CALLS_NATIVE", "WRAPS")}
    assert targets == {"cpp:ctor": "exact", "cpp:f2": "exact", "cpp:m": "exact", "cpp:unresolved:Gone.Fn": "heuristic",
                       "cpp:t": "exact"}
    assert nodes["cpp:unresolved:Gone.Fn"]["resolved"] is False
    assert merger.report.to_dict()["bridgeResolution"]["matchRate"] == 0.75


def test_missing_bridge_nodes_fail_loudly(config):
    write(config.output_dir / "csharp", "Low", [method("dn:M:A.f"),
          {"label": "External", "id": "dn:M:W.g", "name": "g", "expectedFrom": "bridge"}],
          [edge("CALLS", "dn:M:A.f", "dn:M:W.g", kind="call")])
    merger, _, _ = merge(config, allow_missing_bridge=False)
    assert merger.report.errors and "bridge" in merger.report.errors[0]


def test_derived_properties(config):
    write(config.output_dir / "csharp", "Low", [
        {"label": "Assembly", "id": "asm:LowCore", "name": "LowCore"},
        {"label": "Message", "id": "msg:1", "template": "Moving {0}\nnow", "templateStyle": "dotnet", "file": "A.cs"},
        method("dn:M:A.h"), method("dn:M:A.s"),
    ], [edge("SUBSCRIBES", "dn:M:A.s", "dn:E:Ui.Click", handler="dn:M:A.h", uiEvent=True)])
    _, nodes, _ = merge(config)
    assert nodes["asm:LowCore"]["layer"] == "low" and nodes["asm:LowCore"]["layerIndex"] == 0
    assert nodes["msg:1"]["template"] == "Moving {0}\\nnow"  # stored one-line for the CSV importer
    assert nodes["msg:1"]["regex"] is not None and nodes["msg:1"]["fileKey"] == "a.cs"
    assert nodes["dn:M:A.h"]["entryKind"] == "ui_handler"
    assert nodes["dn:M:A.s"]["isTest"] is False  # boolean defaults are filled


def test_native_qualified_names():
    assert native_qualified_name("MiniPlant.Native.NativeStage.Home") == "MiniPlant::Native::NativeStage::Home"
    assert native_qualified_name("Ns.Cls.{ctor}") == "Ns::Cls::Cls"
    assert native_qualified_name("std.vector<int,std::allocator<int> >.{dtor}") == "std::vector<int,std::allocator<int> >::~vector"
    assert native_qualified_name("plain_c_function") == "plain_c_function"


def test_csv_cells_and_headers(tmp_path):
    assert _cell('say "hi", ok', "string") == '"say ""hi"", ok"'
    assert _cell(["A", "B"], "string[]") == '"A;B"'
    assert _cell([3, 1], "int[]") == "3;1"
    assert _cell(True, "boolean") == "true" and _cell(None, "string") == ""
    writer = CsvWriter(tmp_path / "import")
    writer.write_nodes("Method", [{"id": "dn:M:A.f", "name": "f", "isTest": False, "lineStart": 3}])
    writer.write_edge({"type": "CALLS", "start": "dn:M:A.f", "end": "dn:M:A.g", "line": 4, "lines": [4, 9]})
    writer.close()
    header = (tmp_path / "import" / "nodes_Method.csv").read_text().splitlines()[0]
    assert header.startswith("id:ID,:LABEL,name:string") and "isTest:boolean" in header and "lineStart:int" in header
    row = (tmp_path / "import" / "rels_CALLS.csv").read_text().splitlines()[1]
    assert row.startswith('"dn:M:A.f","dn:M:A.g",CALLS') and ",4;9," in row
