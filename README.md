# codegraph — compiler-accurate code knowledge graph

codegraph turns a large .NET Framework codebase (C#, native C++ and C++/CLI) into a Neo4j graph built by the
compilers themselves (Roslyn, libclang, the ILSpy metadata reader), and serves it to agents such as Claude Code
through an MCP server with 23 read-only tools: who calls this, what implements it, what breaks if it changes,
which tests reach it, which code wrote this log line, where C# crosses into native code...

Everything runs locally. No embeddings and no model calls anywhere in indexing; every edge comes from a compiler,
a metadata reader or a documented rule, and carries `file:line` (or an IL offset) and a confidence.

The full specification is [SPEC.md](SPEC.md). Decisions and verification status: [docs/DECISIONS.md](docs/DECISIONS.md).
Research on what beats grep for coding agents, and the next-step plan: [docs/research/](docs/research/README.md).
First run on the real codebase: [docs/RUNBOOK_WINDOWS.md](docs/RUNBOOK_WINDOWS.md).

```
  .sln/.csproj ──▶ A  CodeGraph.Roslyn   (C#, MSBuildWorkspace + Roslyn operations) ──┐
  compile_commands ─▶ B  codegraph_native (Python, libclang, clang-cl mode)        ──┼─▶ out/<origin>/<owner>.{nodes,edges}.jsonl
  built /clr DLLs ─▶ C  CodeGraph.Bridge   (C#, ILSpy metadata + IL scan)            ──┘           │
                                                                                                   ▼
                     D  loader (Python): merge + resolve bridge names + derive dispatch/entry points/git history
                        └─▶ neo4j-admin bulk import (full)  or  owner-scoped Bolt patch (incremental) ─▶ Neo4j
                     E  logmatch: log line ─▶ message template regex ─▶ emitting method
                     F  mcp_server: 23 fixed, parameterized, read-only Cypher tools over stdio ◀──▶ Claude Code
```

## Status

| Part | State |
|---|---|
| C# extractor, native extractor, bridge, loader (full + incremental), log matcher, MCP server | built; 69 tests pass on the MiniPlant fixture (macOS) |
| Fixture accuracy check (graph callers vs Roslyn Find References) | 57/57 references, precision and recall 100% |
| Incremental update equals full rebuild | verified on the fixture |
| Speed (synthetic 418k-line solution, 8-core laptop) | C# extraction 9.7 s, Neo4j load 34 s (mostly Neo4j restart), queries 40 ms–0.9 s (docs/DECISIONS.md D-33) |
| Windows-only steps: legacy `.csproj` loading, real C++/CLI IL shape, `dumpbin`, compilation database from MSBuild, accuracy/benchmark on the real code | written, **pending verification** - see [RUNBOOK_WINDOWS.md](docs/RUNBOOK_WINDOWS.md) |

## Requirements

| Tool | Version | Used by |
|---|---|---|
| .NET SDK | 10.0 | C# extractor, bridge |
| Python | 3.10+ (tested 3.13) | native extractor, loader, MCP server |
| Neo4j Community | 5.26 LTS or 2025.x+ (tested 2026.09), Java 21 | the graph |
| Visual Studio 2022+ with "Desktop development with C++" and "C++/CLI support" | Windows only | building the codebase, legacy project evaluation, `dumpbin`, compilation database |
| LLVM 18 (optional, recommended on Windows) | same major as the `libclang` wheel | builtin headers for libclang (`native.resource_dir`) |

## Setup

```bash
# macOS / Linux (also installs .NET into ~/.dotnet and Neo4j via Homebrew when missing)
scripts/setup_dev.sh
```

```powershell
# Windows (Developer PowerShell for VS); install .NET 10 SDK, Python 3.10+ and Neo4j first
scripts\setup_dev.ps1
```

Both create `.venv`, install the Python package (`pip install -e .[fast,test]`), build the extractors, and
write a git-ignored `.env` holding generated passwords (`CODEGRAPH_NEO4J_PASSWORD`,
`CODEGRAPH_NEO4J_RO_PASSWORD`). Neo4j's initial password must be set to the same value before its first start
(`neo4j-admin dbms set-initial-password <value>`); the setup scripts do that when they install Neo4j.

## Index a codebase

1. Copy `config/codegraph.yaml` to `config/codegraph.local.yaml` (git-ignored) and fill in paths, then the
   codebase conventions marked **ASK** (logging APIs, config access, entry-point attributes, layers). Relative
   paths resolve against the config file's folder.
2. Build the solution (the bridge reads the built C++/CLI DLLs; `dumpbin` reads the native DLLs).
3. Generate the compilation database (Windows): `extractors\native\make_compdb.ps1 -Solution <sln> -Out out\compile_commands.json`
4. Full index:

```powershell
scripts\full_index.ps1 -Config config\codegraph.local.yaml      # Windows
scripts/full_index.sh config/codegraph.local.yaml               # macOS / Linux
```

5. After code changes: `scripts\incremental_index.ps1 -Config config\codegraph.local.yaml` (`-DryRun` prints the plan).

What the full index runs, step by step (each can be run alone):

| Step | Command | Output |
|---|---|---|
| C# | `dotnet extractors/CodeGraph.Roslyn/bin/Release/net10.0/CodeGraph.Roslyn.dll --config <cfg> [--project X]... [--diagnostics full]` | `out/csharp/<assembly>.*`, `manifest.json`, `load_report.json` |
| native | `python -m codegraph_native --config <cfg> [--compdb file] [--workers N] [--no-cache]` | `out/native/<module>.*`, `diagnostics.json`, `clr_files.json` |
| bridge | `dotnet extractors/CodeGraph.Bridge/bin/Release/net10.0/CodeGraph.Bridge.dll --config <cfg>` | `out/bridge/<assembly>.*` |
| load | `python -m loader full --config <cfg> [--method admin\|bolt]` | Neo4j; `out/import/*.csv`, `merge_report.json` |
| reader account | `python -m loader users --config <cfg>` | Neo4j user `codegraph_ro` |
| status | `python -m loader status --config <cfg>` | last run, staleness |

Exit code 3 from the C# extractor means the compile-error gate failed (output was still written; pass
`--allow-compile-errors` / `-AllowCompileErrors` to load anyway). `--method bolt` loads online without stopping
Neo4j (no admin rights needed, slower).

## Use it from Claude Code

```bash
claude mcp add codegraph -- <checkout>/.venv/bin/python -m mcp_server --config <checkout>/config/codegraph.local.yaml
# Windows: claude mcp add codegraph -- <checkout>\.venv\Scripts\python.exe -m mcp_server --config <checkout>\config\codegraph.local.yaml
```

| Group | Tools |
|---|---|
| Navigation | `find_symbol`, `outline`, `dependencies_of`, `callers_of`, `callees_of`, `implementations_of`, `path_to_native`, `entry_points`, `entry_points_reaching`, `assembly_graph` |
| Change, review, testing | `impact_of_change`, `diff_impact`, `tests_reaching`, `co_changed_files` |
| Architecture | `layer_violations`, `unreferenced_symbols`, `hotspots` |
| Debugging, data, config | `find_log_source`, `find_exception_source`, `writers_of`, `readers_of`, `config_usage` |
| Housekeeping | `index_status` |

Symbol arguments take an ID from `find_symbol`, a fully-qualified name, `Type.Member`, `ns::Class::fn`, or a short
name (an ambiguous name returns candidates). Every result carries `file:line`, `confidence` and
`index_commit`/`stale`. Neo4j Browser (http://localhost:7474) works on the same graph; [docs/SCHEMA.md](docs/SCHEMA.md)
lists labels, properties and relationship types.

## Tests

```bash
.venv/bin/python -m pytest                 # everything: unit + fixture + Neo4j integration (~2 min)
.venv/bin/python -m pytest -m "not neo4j"  # no database needed
CODEGRAPH_REUSE_OUTPUT=1 .venv/bin/python -m pytest   # reuse out/fixture instead of re-extracting
```

The fixture `fixtures/MiniPlant` contains every construct SPEC 11.1 lists (interface dispatch, override chains,
generics, extension methods, lambdas, local functions, events, partial classes, five kinds of log calls, throws,
config reads, P/Invoke, native classes with virtual overrides, a C++/CLI wrapper, tests, entry points of every
kind, a layer violation, dead code). `tests/expected_edges.yaml` is the reviewed list of every edge it must produce.
Off Windows, the C++/CLI wrapper is replaced by a C# stand-in rewritten into the C++/CLI metadata shape
(docs/DECISIONS.md D-15).

## Performance notes

* C#: projects compile concurrently; method bodies of each project are bound in parallel; repeated edges are
  merged in memory; output is written once per assembly. Use `diagnostics: fast` for day-to-day runs.
* Native: one process per CPU; per-translation-unit cache that also checks included headers; system headers are
  pruned before traversal.
* Load: the merge streams edges (never holds them all); neo4j-admin bulk import; derived edges are computed
  before import, not with Cypher afterwards.
* Queries: every lookup uses the `:Node(id)` uniqueness index; traversals are level-by-level BFS with cached
  neighbour lists, bounded by depth and a 20k-node cap - no path enumeration.
* Incremental: only changed projects are re-extracted; only their nodes/edges are rewritten.

## Repository layout

```
SPEC.md                         specification (v1.1)
config/codegraph.yaml           configuration template (all codebase-specific knobs)
extractors/CodeGraph.Shared     records, IDs, JSONL writer, config model (C#)
extractors/CodeGraph.Roslyn     component A: C# extractor
extractors/CodeGraph.Bridge     component C: C++/CLI bridge extractor
extractors/native               component B: codegraph_native (Python), make_compdb.ps1, CompileCommandsLogger
codegraph_common/               shared Python: config, schema, IDs, JSONL
loader/                         component D: merge, CSV, neo4j-admin, Bolt, incremental, git history
logmatch/                       component E: template regexes, log-line matching
mcp_server/                     component F: MCP server and tools
fixtures/MiniPlant/             test solution with known answers
tests/                          pytest suite, expected_edges.yaml, tools/ (snapshot, SimIjwRewriter)
scripts/                        full_index, incremental_index, build_fixture, setup_dev (.sh and .ps1)
bench/                          task benchmark harness (SPEC 11.3)
docs/                           DECISIONS, WALKTHROUGH, SCHEMA, RUNBOOK_WINDOWS, ACCURACY, BENCHMARK
out/                            generated data (git-ignored)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `load_report.json` shows `Failure` for `.csproj` | MSBuild could not evaluate the project: run from a VS developer prompt, check `msbuild_properties`, restore NuGet packages first |
| Compile-error gate fails | missing references → missing edges; see `manifest.json` `firstErrors` per project; build the solution first |
| Loader: "C++/CLI symbol(s) referenced from C# were not produced by the bridge" | build the C++/CLI projects and list their DLLs in `cppcli_assemblies`; or an ID mismatch (DECISIONS D-14) |
| Native `diagnostics.json` rate ≥ 5% | include paths missing from the compilation database, or no builtin headers (`native.resource_dir`) |
| `neo4j-admin` fails | the database must be stoppable by this user; `--method bolt` avoids the restart; check `out/import/neo4j-admin.log` |
| MCP tools return `stale: true` | the repo moved past the indexed commit: run the incremental index |
