"""DLL exports (SPEC 6.4) -> :NativeExport nodes and RESOLVES_TO edges to the implementing functions.

Two sources:
  dumpbin  the export table of each built DLL in native_dlls (ground truth; Windows, VS developer prompt)
  source   functions marked __declspec(dllexport) in the code (works anywhere, e.g. on the fixture)
Exports resolve by exact MSVC-mangled name first, then by name for extern "C" functions.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# "    1    0 00011000 ?Grab@Camera@@QEAAXXZ" or "    2    1 00012340 MiniPlant_Stage_Move = MiniPlant_Stage_Move"
_EXPORT_LINE = re.compile(r"^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+(\S+)")


def dumpbin_exports(dll: Path) -> list[str]:
    tool = shutil.which("dumpbin") or shutil.which("dumpbin.exe")
    if tool is None:
        raise FileNotFoundError("dumpbin not found: run from a Visual Studio developer prompt, or set native.exports_from: source")
    result = subprocess.run([tool, "/nologo", "/exports", str(dll)], capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"dumpbin failed for {dll}: {result.stdout[-500:]}")
    return [m.group(1) for line in result.stdout.splitlines() if (m := _EXPORT_LINE.match(line))]


def resolve(names_by_dll: dict[str, list[str]], functions: dict[str, dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """(:NativeExport nodes, RESOLVES_TO edges) for every exported name, resolved where possible."""
    by_mangled: dict[str, str] = {}
    by_c_name: dict[str, str] = {}
    for function_id, function in sorted(functions.items()):
        for mangled in [function.get("mangledName"), *(function.get("manglings") or [])]:
            if mangled:
                by_mangled.setdefault(mangled, function_id)
        if function.get("isExternC"):
            by_c_name.setdefault(function["name"], function_id)

    nodes, edges = [], []
    for dll, names in sorted(names_by_dll.items()):
        for name in sorted(set(names)):
            export_id = f"exp:{dll}!{name}"
            nodes.append({"label": "NativeExport", "id": export_id, "name": name, "fqn": f"{dll}!{name}", "lang": "cpp",
                          "dll": dll, "entryPoint": name})
            target = by_mangled.get(name) or by_c_name.get(name)
            if target is not None:
                edges.append({"type": "RESOLVES_TO", "start": export_id, "end": target, "confidence": "exact"})
    return nodes, edges


def source_exports(functions: dict[str, dict[str, Any]], dll_of: Any) -> dict[str, list[str]]:
    """Exported names per DLL from __declspec(dllexport): the C name for extern "C", else the mangled name."""
    exports: dict[str, list[str]] = {}
    for function in functions.values():
        if not function.get("dllexport") or not function.get("isDefinition"):
            continue
        name = function["name"] if function.get("isExternC") else function.get("mangledName")
        if name:
            exports.setdefault(dll_of(function), []).append(name)
    return exports
