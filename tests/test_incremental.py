"""SPEC 8.3 VERIFY: an incremental update gives exactly the graph a full rebuild gives.

Copies the fixture into a temporary git repository, indexes it, changes C# (edit, delete a method, add a
file) and native code, commits, runs `loader incremental`, dumps the graph, rebuilds from scratch, dumps
again, and compares. Slow (two full builds); restores the fixture graph afterwards.
"""
import json
import shutil
import subprocess

import pytest
from conftest import FIXTURE, FIXTURE_CONFIG, REPO, extract_fixture

pytestmark = [pytest.mark.neo4j, pytest.mark.dotnet]


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]})


def test_incremental_equals_full_rebuild(tmp_path, neo4j_config):
    from codegraph_common.config import Config
    from loader import pipeline
    from loader.incremental import incremental

    fixture = tmp_path / "MiniPlant"
    shutil.copytree(FIXTURE, fixture, ignore=shutil.ignore_patterns("bin", "obj"))
    config_path = fixture / "codegraph.yaml"
    config_path.write_text(FIXTURE_CONFIG.read_text().replace("../../out/fixture", str(tmp_path / "out"))
                           .replace("co_change_min: 2", "co_change_min: 1"))
    (fixture / ".gitignore").write_text("bin/\nobj/\n")
    git(fixture, "init", "-q")
    git(fixture, "add", "-A")
    git(fixture, "commit", "-qm", "initial")
    config = Config(config_path)
    try:
        extract_fixture(config_path, fixture)
        pipeline.full(config)

        # C#: edit a method body, delete a method, add a file; native: add a call.
        validation = fixture / "src/MiniPlant.Core/Recipe.Validation.cs"
        validation.write_text(validation.read_text().replace("return _points.Count <= MaxPoints;",
                                                             "return Count() <= MaxPoints;\n        }\n\n        public int Count()\n        {\n            return _points.Count;"))
        linear = fixture / "src/MiniPlant.Core/LinearStage.cs"
        text = linear.read_text()
        start = text.index("        // Never referenced")
        linear.write_text(text[:start] + text[text.index("        private static void OnNativePosition"):])
        (fixture / "src/MiniPlant.Core/StageMonitor.cs").write_text(
            "namespace MiniPlant.Core\n{\n    public class StageMonitor\n    {\n        public double Sample(IStage stage) => stage.ReadPosition();\n    }\n}\n")
        native = fixture / "native/MiniPlant.Native/NativeStage.cpp"
        native.write_text(native.read_text().replace("    NativeStageBase* self = this;", "    CheckLimits(0.0);\n    NativeStageBase* self = this;"))
        git(fixture, "add", "-A")
        git(fixture, "commit", "-qm", "change")

        subprocess.run(["bash", str(REPO / "scripts/build_fixture.sh"), str(fixture)], check=True, capture_output=True)
        incremental(config)
        pipeline.dump(config, tmp_path / "incremental.json")
        _check_git_tools(config)

        extract_fixture(config_path, fixture)
        pipeline.full(config)
        pipeline.dump(config, tmp_path / "full.json")

        incremental_graph = json.loads((tmp_path / "incremental.json").read_text())
        full_graph = json.loads((tmp_path / "full.json").read_text())
        assert "dn:M:MiniPlant.Core.Recipe.Count" in {n["id"] for n in full_graph["nodes"]}
        assert "dn:M:MiniPlant.Core.LinearStage.LegacyRecalibrate" not in {n["id"] for n in full_graph["nodes"]}
        _assert_same(incremental_graph, full_graph)
    finally:
        pipeline.full(Config(FIXTURE_CONFIG))  # put the shared fixture graph back for the other tests


def _check_git_tools(config) -> None:
    """The tools that need git history, on the temporary repository (index at HEAD after the incremental run)."""
    from mcp_server.graph import ReadOnlyGraph
    from mcp_server.tools import Tools

    graph = ReadOnlyGraph(config)
    try:
        tools = Tools(graph, config)
        review = tools.call("diff_impact", base_ref="HEAD~1", head_ref="HEAD")
        changed = {m["id"]: m for m in review["changed_methods"]}
        assert {"dn:M:MiniPlant.Core.Recipe.Validate", "dn:M:MiniPlant.Core.Recipe.Count",
                "dn:M:MiniPlant.Core.StageMonitor.Sample(MiniPlant.Core.IStage)"} <= set(changed)
        assert any(m.get("lang") == "cpp" and m["fqn"] == "MiniPlant::Native::NativeStage::Home" for m in changed.values())
        assert changed["dn:M:MiniPlant.Core.Recipe.Validate"]["tests"] >= 1
        assert changed["dn:M:MiniPlant.Core.Recipe.Validate"]["entry_points"].get("remote") == 1
        assert "MiniPlant.Core.StageMonitor.Sample" in review["untested"]
        assert review["warning"] is None  # the index is at HEAD
        co_changed = {f["file"] for f in tools.call("co_changed_files", file="src/MiniPlant.Core/Recipe.Validation.cs")["co_changed"]}
        assert "src/MiniPlant.Core/LinearStage.cs" in co_changed
        churn = tools.call("hotspots", metric="churn", limit=5)["results"]
        assert churn and churn[0]["commits"] >= 2
    finally:
        graph.close()


def _assert_same(a: dict, b: dict) -> None:
    nodes_a = {n["id"]: n for n in a["nodes"]}
    nodes_b = {n["id"]: n for n in b["nodes"]}
    assert sorted(nodes_a) == sorted(nodes_b), (
        f"nodes only after incremental: {sorted(set(nodes_a) - set(nodes_b))[:10]}; "
        f"only after full: {sorted(set(nodes_b) - set(nodes_a))[:10]}")
    differing = [i for i in nodes_a if nodes_a[i] != nodes_b[i]]
    assert not differing, f"{len(differing)} node(s) differ, e.g. {nodes_a[differing[0]]} vs {nodes_b[differing[0]]}"
    edges_a = {json.dumps(e, sort_keys=True) for e in a["edges"]}
    edges_b = {json.dumps(e, sort_keys=True) for e in b["edges"]}
    assert edges_a == edges_b, (f"only after incremental: {sorted(edges_a - edges_b)[:5]}\n"
                                f"only after full: {sorted(edges_b - edges_a)[:5]}")
