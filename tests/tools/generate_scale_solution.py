"""Generates a large synthetic C# solution for performance testing (docs/DECISIONS.md D-33).

  python tests/tools/generate_scale_solution.py <folder> [--projects 20] [--files 100] [--methods 20]
  The script writes the projects, the solution and a codegraph.yaml, and restores the solution (MSBuildWorkspace
  never restores: unrestored projects lose their framework references). Then:
  dotnet <checkout>/extractors/CodeGraph.Roslyn/bin/Release/net10.0/CodeGraph.Roslyn.dll --config <folder>/codegraph.yaml
  python -m loader full --config <folder>/codegraph.yaml --skip-git

Each project references the three below it; every method calls three random methods of those projects, logs
through an interface, throws, and reads/writes a field - roughly the edge mix of a real codebase.
"""
from __future__ import annotations

import argparse
import random
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--projects", type=int, default=20)
    parser.add_argument("--files", type=int, default=100)
    parser.add_argument("--methods", type=int, default=20)
    args = parser.parse_args()
    random.seed(7)
    root = Path(args.folder).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "Directory.Build.props").write_text(
        "<Project><PropertyGroup><TargetFramework>net48</TargetFramework><LangVersion>latest</LangVersion>"
        "<OutputPath>$(MSBuildThisFileDirectory)bin\\$(MSBuildProjectName)\\</OutputPath></PropertyGroup></Project>\n")
    lines = 0
    for p in range(args.projects):
        name = f"Big.P{p:02d}"
        folder = root / name
        folder.mkdir(exist_ok=True)
        references = "".join(f'<ProjectReference Include="..\\Big.P{q:02d}\\Big.P{q:02d}.csproj" />' for q in range(max(0, p - 3), p))
        (folder / f"{name}.csproj").write_text(f'<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{references}</ItemGroup></Project>\n')
        (folder / "Log.cs").write_text(f"namespace {name} {{ public interface ILogSink {{ void Info(object m); void Error(object m); }}"
                                       f" public static class L {{ public static ILogSink Log; }} }}\n")
        for f in range(args.files):
            lines += write_class(folder / f"C{f:03d}.cs", name, p, f, args)
    write_solution(root, args.projects)
    (root / "codegraph.yaml").write_text("""repo_root: "."
solutions: ["Big.sln"]
msbuild_properties: {Configuration: "Debug"}
output_dir: "out"
roslyn: {diagnostics: "fast"}
log_methods:
  - { type_implements: "Big.P00.ILogSink", methods: ["Info", "Error"], template_arg: 0, level_from: "method", template_style: "literal" }
neo4j: {uri: "bolt://localhost:7687", database: "neo4j", user_writer: "neo4j", password_writer_env: "CODEGRAPH_NEO4J_PASSWORD", user_reader: "codegraph_ro", password_reader_env: "CODEGRAPH_NEO4J_RO_PASSWORD", import_method: "admin"}
""")
    print(f"{args.projects} projects, {args.projects * args.files} files, "
          f"{args.projects * args.files * args.methods} methods, {lines} lines in {root}")


def write_class(path: Path, namespace: str, project: int, index: int, args: argparse.Namespace) -> int:
    name = f"C{index:03d}"
    body = ["using System;", f"namespace {namespace}", "{", f"    public interface I{name} {{ int Run0(int x); }}",
            f"    public class {name} : I{name}", "    {", "        private int _state;"]
    for method in range(args.methods):
        calls = []
        for _ in range(3):
            target_project = random.randint(max(0, project - 3), project)
            calls.append(f"            acc += new Big.P{target_project:02d}.C{random.randrange(args.files):03d}()"
                         f".Run{random.randrange(args.methods)}(acc);")
        body += [f"        public int Run{method}(int x)", "        {", "            int acc = x + _state;", *calls,
                 (f'            if (acc < 0) {{ L.Log?.Error($"Run{method} of {name} failed with {{acc}}"); '
                  f'throw new InvalidOperationException("negative in {name}.Run{method}"); }}'),
                 "            _state = acc;", "            return acc;", "        }"]
    body += ["    }", "}"]
    path.write_text("\n".join(body) + "\n")
    return len(body)


def write_solution(root: Path, projects: int) -> None:
    subprocess.run(["dotnet", "new", "sln", "-n", "Big", "--format", "sln", "--force"], cwd=root, check=True, capture_output=True)
    subprocess.run(["dotnet", "sln", "Big.sln", "add", *[f"Big.P{p:02d}/Big.P{p:02d}.csproj" for p in range(projects)]],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["dotnet", "restore", "Big.sln", "-v:quiet"], cwd=root, check=True, capture_output=True)


if __name__ == "__main__":
    main()
