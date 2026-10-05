"""Fallback compilation database from .vcxproj files, for machines without MSBuild (SPEC 6.1 alternatives).

It reads what simple projects state directly - ClCompile items, AdditionalIncludeDirectories,
PreprocessorDefinitions, LanguageStandard, CLRSupport - for the configured Configuration|Platform.
It does NOT evaluate property sheets (.props), imports or conditions beyond Configuration|Platform, so
on real Windows projects prefer extractors/native/make_compdb.ps1, which asks MSBuild itself.

  python -m codegraph_native.vcxproj_compdb --config <codegraph.yaml> [--out compile_commands.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from codegraph_common.config import Config

_STANDARDS = {"stdcpp14": "/std:c++14", "stdcpp17": "/std:c++17", "stdcpp20": "/std:c++20", "stdcpplatest": "/std:c++latest"}


def projects(config: Config) -> list[Path]:
    """.vcxproj files listed in the configured solutions."""
    found = []
    for solution in config.raw.get("solutions", []):
        path = config.resolve(solution)
        for match in re.finditer(r'"([^"]+\.vcxproj)"', path.read_text(encoding="utf-8-sig", errors="replace")):
            found.append((path.parent / match.group(1).replace("\\", "/")).resolve())
    return sorted(set(found))


def entries(project: Path, configuration: str, platform: str, solution_dir: Path) -> list[dict]:
    tree = ElementTree.parse(project)
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    wanted = f"{configuration}|{platform}"

    def applies(element: ElementTree.Element) -> bool:
        condition = element.get("Condition")
        return condition is None or wanted in condition

    def expand(value: str) -> str:
        value = value.replace("$(ProjectDir)", str(project.parent) + "/").replace("$(MSBuildThisFileDirectory)", str(project.parent) + "/")
        value = value.replace("$(SolutionDir)", str(solution_dir) + "/")
        return re.sub(r"%\([^)]*\)|\$\([^)]*\)", "", value)

    includes, defines, standard, clr = [], [], None, False
    for group in tree.getroot().findall("m:ItemDefinitionGroup", namespace):
        if not applies(group):
            continue
        for compile_settings in group.findall("m:ClCompile", namespace):
            for element in compile_settings:
                tag = element.tag.split("}")[1]
                if tag == "AdditionalIncludeDirectories":
                    includes += [p for p in expand(element.text or "").split(";") if p.strip()]
                elif tag == "PreprocessorDefinitions":
                    defines += [d for d in expand(element.text or "").split(";") if d.strip()]
                elif tag == "LanguageStandard":
                    standard = _STANDARDS.get((element.text or "").strip())
    for element in tree.getroot().iter():
        if element.tag.endswith("CLRSupport") and applies(element) and (element.text or "").strip().lower() not in ("", "false"):
            clr = True

    result = []
    for item in tree.getroot().iter("{http://schemas.microsoft.com/developer/msbuild/2003}ClCompile"):
        source = item.get("Include")
        if not source:
            continue
        path = (project.parent / source.replace("\\", "/")).resolve()
        arguments = ["cl.exe", "/c", "/nologo", "/EHsc"] + [f"/I{(project.parent / i.replace(chr(92), '/')).resolve()}" for i in includes]
        arguments += [f"/D{d}" for d in defines] + ([standard] if standard else []) + (["/clr"] if clr else []) + [str(path)]
        result.append({"directory": str(project.parent), "file": str(path), "arguments": arguments})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m codegraph_native.vcxproj_compdb")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    config = Config(args.config)
    properties = config.raw.get("msbuild_properties") or {}
    configuration, platform = properties.get("Configuration", "Debug"), properties.get("Platform", "x64")
    solution_dir = config.resolve(config.raw["solutions"][0]).parent
    database = [entry for project in projects(config) for entry in entries(project, configuration, platform, solution_dir)]
    out = Path(args.out) if args.out else config.resolve(config.native["compdb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(database, indent=2), encoding="utf-8")
    print(f"[native] {len(database)} compile command(s) written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
