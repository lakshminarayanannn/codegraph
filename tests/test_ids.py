"""ID equality (SPEC 7.3 step 1): ILSpy's IdStringProvider (bridge) must produce exactly Roslyn's documentation
IDs, or C# callers would not join onto the bridge's nodes. Checked on every compiled fixture assembly."""
import json
import subprocess

import pytest
from conftest import FIXTURE, FIXTURE_OUT, REPO, dotnet

ASSEMBLIES = ["MiniPlant.Core.dll", "MiniPlant.Services.dll", "MiniPlant.UI.Controls.dll", "MiniPlant.Tests.dll", "MiniPlant.App.exe"]


@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_bridge_ids_equal_roslyn_ids(extracted, assembly):
    owner = assembly.rsplit(".", 1)[0]
    roslyn = {json.loads(line)["id"] for line in (FIXTURE_OUT / "csharp" / f"{owner}.nodes.jsonl").read_text().splitlines()
              if json.loads(line)["label"] in ("Type", "Method", "Field", "Property", "Event")}
    output = subprocess.run([dotnet(), str(REPO / "extractors/CodeGraph.Bridge/bin/Release/net10.0/CodeGraph.Bridge.dll"),
                             "--ids-only", "--assembly", str(FIXTURE / "bin" / assembly)],
                            capture_output=True, text=True, check=True).stdout
    bridge = {line.strip() for line in output.splitlines() if line.strip()}
    assert roslyn == bridge, f"only Roslyn: {sorted(roslyn - bridge)}\nonly bridge: {sorted(bridge - roslyn)}"
