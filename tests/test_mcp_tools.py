"""MCP tools (SPEC 10) against the fixture graph in Neo4j: answers, locations, confidence, read-only access."""
import pytest

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def tools(neo4j_config):
    from mcp_server.graph import ReadOnlyGraph
    from mcp_server.tools import Tools

    graph = ReadOnlyGraph(neo4j_config)
    yield Tools(graph, neo4j_config)
    graph.close()


def ids(items):
    return {item["id"] for item in items}


def test_every_result_carries_the_index_stamp(tools):
    result = tools.call("index_status")
    assert result["indexed"] is True and "index_commit" in result and "stale" in result


def test_find_symbol_and_ambiguity(tools):
    assert [r["id"] for r in tools.call("find_symbol", name="StageBase.Move")["results"]] == \
        ["dn:M:MiniPlant.Core.StageBase.Move(System.Double)"]
    ambiguous = tools.call("callers_of", symbol="Move")
    assert "ambiguous" in ambiguous["error"] and len(ambiguous["candidates"]) >= 3


def test_callers_follow_interface_dispatch(tools):
    result = tools.call("callers_of", symbol="dn:M:MiniPlant.Core.StageBase.Move(System.Double)", max_depth=2)
    callers = {c["id"]: c for c in result["callers"]}
    # The interface method is reached through dispatch at no depth cost; calls through it are depth 1.
    interface = callers["dn:M:MiniPlant.Core.IStage.Move(System.Double)"]
    assert interface["depth"] == 0 and interface["rel"] == "DISPATCHES_TO"
    move_to = callers["dn:M:MiniPlant.Core.MotionController.MoveTo(System.Double)"]
    assert move_to["depth"] == 1 and move_to["next"] == "dn:M:MiniPlant.Core.IStage.Move(System.Double)"
    assert move_to["at"] == "src/MiniPlant.Core/MotionController.cs:20"
    assert callers["dn:M:MiniPlant.Core.MotionController.MoveToAsync(System.Double)"]["via"] == "lambda"
    assert "confidence" not in move_to  # only heuristic results say so
    assert all(c.get("loc") for c in result["callers"])


def test_implementations_and_entry_points(tools):
    impls = tools.call("implementations_of", symbol="IStage.Move")["implementations"]
    assert ids(impls) == {"dn:M:MiniPlant.Core.StageBase.Move(System.Double)", "dn:M:MiniPlant.Core.PiezoStage.Move(System.Double)"}
    counts = tools.call("entry_points")["counts"]
    assert counts == {"main": 1, "native_callback": 1, "remote": 1, "ui_handler": 1}


def test_entry_points_reach_native_code_through_pinvoke(tools):
    result = tools.call("entry_points_reaching", symbol="NativeStage::CheckLimits")
    assert "main" in result["by_kind"]


def test_path_to_native_crosses_cppcli_and_pinvoke(tools):
    home = tools.call("path_to_native", symbol="PiezoStage.Home")["crossings"]
    assert home[0]["fqn"] == "MiniPlant::Native::NativeStage::Home"
    assert home[0]["path"] == ["dn:M:MiniPlant.Core.PiezoStage.Home",
                               "-CALLS@src/MiniPlant.Core/PiezoStage.cs:21-> dn:M:MiniPlant.Interop.StageWrapper.Home",
                               "-CALLS_NATIVE@interop/MiniPlant.Interop-> MiniPlant::Native::NativeStage::Home"]
    inspect = tools.call("path_to_native", symbol="InspectionService.Inspect")["crossings"]
    assert any(c["id"] == "exp:miniplant.native.dll!MiniPlant_Stage_Move" for c in inspect)


def test_tests_reaching_and_impact(tools):
    tests = tools.call("tests_reaching", symbol="Recipe.Validate")["tests"]
    assert "dn:M:MiniPlant.Tests.RecipeTests.Validate_RejectsEmptyRecipe" in ids(tests)
    impact = tools.call("impact_of_change", symbol="IStage.Move", change_kind="signature")
    assert {"dn:M:MiniPlant.Core.StageBase.Move(System.Double)", "dn:M:MiniPlant.Core.PiezoStage.Move(System.Double)"} <= ids(impact["family"])
    assert "dn:M:MiniPlant.Core.MotionController.MoveTo(System.Double)" in ids(impact["must_change"])
    assert "MiniPlant.Tests" in impact["assemblies"]
    behaviour = tools.call("impact_of_change", symbol="Recipe.Validate", change_kind="behaviour")
    assert any(e["entryKind"] == "remote" for e in behaviour["entry_points"])


def test_log_and_exception_sources(tools):
    log = tools.call("find_log_source", log_line="2026-10-04 10:11:12,345 [INFO] Moving X to 12.5 mm")["results"][0]
    assert log["matched"] and log["template"] == "Moving {0} to {1} mm"
    assert log["emitters"][0]["file"] == "src/MiniPlant.Core/StageBase.cs" and log["emitters"][0]["line"] == 31
    native = tools.call("find_log_source", log_line="Target 150.000 mm is outside the travel range")["results"][0]
    assert native["matched"] and native["emitters"][0]["lang"] == "cpp"
    exception = tools.call("find_exception_source", message="Recipe has no points", exception_type="InvalidOperationException")
    assert exception["results"][0]["emitters"][0]["id"] == "dn:M:MiniPlant.Core.Recipe.Validate"


def test_data_and_config_flow(tools):
    writers = tools.call("writers_of", symbol="StageBase.position")["writes"]
    assert ids(writers) == {"dn:M:MiniPlant.Core.StageBase.Move(System.Double)"}
    usage = tools.call("config_usage", key="Stage.MaxSpeed")["keys"][0]
    assert ids(usage["readers"]) == {"dn:M:MiniPlant.Services.InspectionService.Start"}


def test_architecture_tools(tools):
    violations = tools.call("layer_violations")["violations"]
    assert [(v["from"], v["to"]) for v in violations] == [("MiniPlant.Services", "MiniPlant.UI.Controls")]
    assert violations[0]["evidence"]
    dead = ids(tools.call("unreferenced_symbols")["candidates"])
    # The never-called method and the reflection-only one (a documented false positive).
    assert dead == {"dn:M:MiniPlant.Core.LinearStage.LegacyRecalibrate", "dn:M:MiniPlant.Core.LinearStage.RunDiagnostics"}
    assert tools.call("hotspots", metric="fan_in", limit=3)["results"]


def test_outline_and_dependencies(tools):
    outline = tools.call("outline", target="src/MiniPlant.Core/Recipe.Validation.cs")
    assert outline["types"][0]["type"]["id"] == "dn:T:MiniPlant.Core.Recipe"
    assert ids(outline["types"][0]["members"]) >= {"dn:M:MiniPlant.Core.Recipe.Validate"}
    deps = tools.call("dependencies_of", symbol="InspectionService", level="assembly")["dependencies"]
    assert {d["target"] for d in deps} >= {"MiniPlant.Core", "MiniPlant.UI.Controls"}


def test_queries_run_read_only(tools):
    from neo4j.exceptions import ClientError

    with pytest.raises(ClientError, match="(?i)read"):
        tools.graph.query("CREATE (:Node {id: 'should-not-exist'})")
