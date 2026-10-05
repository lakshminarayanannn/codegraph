"""Compilation database handling (SPEC 6.1-6.2): Windows command splitting, cleaning, /clr detection."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extractors" / "native"))

from codegraph_native import compdb  # noqa: E402


def test_windows_command_splitting_follows_commandlinetoargvw():
    command = r'cl.exe /c /I"C:\Program Files\x" /DFOO=\"bar\" src\a.cpp /Fo"obj\\"'
    assert compdb.split_command(command) == ["cl.exe", "/c", r"/IC:\Program Files\x", '/DFOO="bar"', r"src\a.cpp", "/Foobj\\"]
    assert compdb.split_command('clang-cl -I dir "a b" c') == ["clang-cl", "-I", "dir", "a b", "c"]


def test_clean_keeps_meaning_and_drops_outputs(tmp_path):
    source = str(tmp_path / "a.cpp")
    args = ["/c", "/nologo", "/I", "inc", "/DX=1", "/FIpch.h", "/Fo" + str(tmp_path / "a.obj"), "/Yustdafx.h", "/MP", source]
    assert compdb.clean(args, source, str(tmp_path)) == ["/I", "inc", "/DX=1", "/FIpch.h"]


def test_clr_units_are_detected(tmp_path):
    database = [
        {"directory": str(tmp_path), "file": "native.cpp", "arguments": ["cl.exe", "/c", "native.cpp"]},
        {"directory": str(tmp_path), "file": "wrapper.cpp", "command": "cl.exe /c /clr:netcore wrapper.cpp"},
    ]
    path = tmp_path / "compile_commands.json"
    path.write_text(json.dumps(database))
    units = compdb.load(path)
    assert [u.is_clr for u in units] == [False, True]
    assert units[0].args == []
