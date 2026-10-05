"""Session fixtures: build + extract the MiniPlant fixture once, load it into Neo4j once.

  pytest                         everything (needs the .NET SDK and a running Neo4j for the integration tests)
  pytest -m "not neo4j"          no database needed
  CODEGRAPH_REUSE_OUTPUT=1       reuse out/fixture from a previous run instead of re-extracting
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "fixtures" / "MiniPlant"
FIXTURE_CONFIG = FIXTURE / "codegraph.yaml"
FIXTURE_OUT = REPO / "out" / "fixture"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def dotnet() -> str | None:
    found = shutil.which("dotnet")
    home = Path.home() / ".dotnet" / "dotnet"
    return found or (str(home) if home.exists() else None)


def run(command: list[str], cwd: Path = REPO) -> None:
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    if dotnet():
        env["PATH"] = str(Path(dotnet()).parent) + os.pathsep + env.get("PATH", "")
        env.setdefault("DOTNET_ROOT", str(Path(dotnet()).parent))
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed ({result.returncode}):\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")


def extract_fixture(config: Path, fixture: Path = FIXTURE) -> None:
    """Build the fixture and run the three extractors (the steps of scripts/full_index, minus the load)."""
    exe = dotnet()
    if os.name == "nt":
        run(["powershell", "-File", str(REPO / "scripts" / "build_fixture.ps1"), "-Fixture", str(fixture)])
    else:
        run(["bash", str(REPO / "scripts" / "build_fixture.sh"), str(fixture)])
    run([exe, "build", str(REPO / "extractors" / "CodeGraph.sln"), "-c", "Release", "-v:quiet", "-nologo"])
    run([exe, str(REPO / "extractors/CodeGraph.Roslyn/bin/Release/net10.0/CodeGraph.Roslyn.dll"), "--config", str(config)])
    run([sys.executable, "-m", "codegraph_native", "--config", str(config)])
    run([exe, str(REPO / "extractors/CodeGraph.Bridge/bin/Release/net10.0/CodeGraph.Bridge.dll"), "--config", str(config)])


@pytest.fixture(scope="session")
def extracted() -> Path:
    if os.environ.get("CODEGRAPH_REUSE_OUTPUT") and (FIXTURE_OUT / "csharp" / "manifest.json").exists():
        return FIXTURE_OUT
    if dotnet() is None:
        pytest.skip("the .NET SDK is needed to build and extract the fixture")
    extract_fixture(FIXTURE_CONFIG)
    return FIXTURE_OUT


@pytest.fixture(scope="session")
def neo4j_config(extracted: Path):
    """The fixture graph loaded into Neo4j (full admin import), plus the read-only user."""
    from codegraph_common.config import Config
    from loader import pipeline
    from loader.neo import Graph
    from loader.users import ensure_reader

    config = Config(FIXTURE_CONFIG)
    graph = Graph(config.neo4j)
    try:
        graph.wait_until_available(timeout_s=5)
    except Exception as error:  # no database on this machine: integration tests are skipped
        pytest.skip(f"Neo4j not reachable: {error}")
    finally:
        graph.close()
    pipeline.full(config)
    ensure_reader(config)
    return config
