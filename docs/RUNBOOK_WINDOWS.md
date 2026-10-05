# Runbook: first run on the real codebase (Windows)

The prototype was built and tested on macOS against the MiniPlant fixture. Everything below needs Windows,
Visual Studio and the real code. Work through the phases in order (SPEC 14); each ends with a gate: record the
result in `docs/DECISIONS.md` under the D-number given, then continue.

Open every shell as **Developer PowerShell for VS** (msbuild, cl, dumpbin and `INCLUDE` on the environment).

## Phase 0 — setup and spikes

1. Prerequisites: .NET 10 SDK, Python 3.10+, Neo4j Community 5.26+ (+ Java 21), LLVM 18 (optional), the
   VS workloads "Desktop development with C++" and "C++/CLI support".
2. `scripts\setup_dev.ps1`; set Neo4j's initial password to `CODEGRAPH_NEO4J_PASSWORD` from `.env`; start Neo4j.
3. Fixture on Windows (proves the toolchain end to end, with the *real* C++/CLI compiler):
   ```powershell
   scripts\build_fixture.ps1
   scripts\full_index.ps1 -Config fixtures\MiniPlant\codegraph.yaml
   .venv\Scripts\python.exe -m pytest
   ```
   The fixture config uses `exports_from: source`; set it to `dumpbin` to also try dumpbin on `bin\MiniPlant.Native.dll`.
4. Copy `config\codegraph.yaml` to `config\codegraph.local.yaml`; fill in paths; answer Q1–Q7 at the end of
   DECISIONS.md with the codebase owners and put the answers in the config.
5. Build the real solution (Release|x64 or whatever the config says).

| Spike | Command | Pass | Record in |
|---|---|---|---|
| MSBuild loading (legacy projects) | `dotnet extractors\CodeGraph.Roslyn\bin\Release\net10.0\CodeGraph.Roslyn.dll --config config\codegraph.local.yaml --list-projects` | every C# project listed; `out\csharp\load_report.json` has no `Failure` for `.csproj` files | D-01 |
| Compilation database | `extractors\native\make_compdb.ps1 -Solution <sln> -Out out\compile_commands.json` | one entry per `.cpp` of every C++ project | D-17 |
| C++/CLI IL shape | `dotnet extractors\CodeGraph.Bridge\bin\Release\net10.0\CodeGraph.Bridge.dll --list-native --assembly <wrapper.dll>` | native targets look as described in D-13 (`<Module>`, no ImplMap, `Ns.Class.Method`, `{ctor}`) | D-13 |
| ID equality | covered by the Phase 1 load (it fails listing any C# reference the bridge did not produce) | 0 missing | D-14 |

**Gate 0:** DECISIONS.md D-01, D-13, D-17 filled in; every project loads, or a list of failures with causes.

## Phase 1–2 — C# graph and accuracy

```powershell
dotnet extractors\CodeGraph.Roslyn\bin\Release\net10.0\CodeGraph.Roslyn.dll --config config\codegraph.local.yaml --diagnostics full
.venv\Scripts\python.exe -m loader full --config config\codegraph.local.yaml --allow-missing-bridge
.venv\Scripts\python.exe -m loader users --config config\codegraph.local.yaml
dotnet extractors\CodeGraph.Roslyn\bin\Release\net10.0\CodeGraph.Roslyn.dll --config config\codegraph.local.yaml --accuracy 50
```

* `out\csharp\manifest.json`: compile errors per project (gate `max_compile_errors`), duration, counts.
  Peak memory of the extractor process → D-11.
* `--accuracy 50` writes `out\csharp\accuracy.json`. **Gate: recall ≥ 98%.** Investigate every miss
  (`missing` lists the callers Roslyn found that the graph lacks) and copy the summary into docs/ACCURACY.md.
* Spot checks in Neo4j Browser: `MATCH (m:Method {isEntryPoint:true}) RETURN m.entryKind, count(*)`;
  `MATCH (a:Assembly) RETURN a.name, a.layer`.

## Phase 3 — bridge

```powershell
dotnet extractors\CodeGraph.Bridge\bin\Release\net10.0\CodeGraph.Bridge.dll --config config\codegraph.local.yaml
```
Then (after Phase 4, which supplies the native symbols) the loader reports the native-name match rate in
`out\import\merge_report.json` → `bridgeResolution` (also an IndexRun warning below 90%).
**Gate: match rate ≥ 90%, or an explained list of `unresolvedNames`.** Common causes: native code outside the
compilation database; names shaped differently than D-13 (adapt `loader/merge.py: native_qualified_name`).
`indirectNativeCalls` in `out\bridge\manifest.json` counts virtual native calls (calli), which have no static
target (known gap).

## Phase 4 — native

```powershell
.venv\Scripts\python.exe -m codegraph_native --config config\codegraph.local.yaml
```
**Gate:** `out\native\diagnostics.json` rate < 5% (set `native.resource_dir` to LLVM's `lib\clang\18` if builtin
headers are missing); `out\native\clr_files.json` count ≈ the known C++/CLI share (D-20);
`native.exports_from: dumpbin` with `native_dlls` filled → `exportsResolved` in `out\native\manifest.json`.

## Phase 5 — MCP tools

```powershell
scripts\full_index.ps1 -Config config\codegraph.local.yaml
claude mcp add codegraph -- <checkout>\.venv\Scripts\python.exe -m mcp_server --config <checkout>\config\codegraph.local.yaml
```
**Gate 5a:** each navigation/debugging tool demoed on 3 real symbols or log lines (paste real log lines into
`find_log_source`). **Gate 5b:** `diff_impact` on 3 real past PRs (`base_ref`/`head_ref` = the PR's merge base
and head), checked by a reviewer.

## Phase 6 — incremental

Change one method in one project, commit, then:
```powershell
scripts\incremental_index.ps1 -Config config\codegraph.local.yaml -DryRun   # shows the plan
scripts\incremental_index.ps1 -Config config\codegraph.local.yaml
```
Record the wall-clock time in DECISIONS.md D-28. (Equality with a full rebuild is proven on the fixture by
`tests\test_incremental.py`.)

## Phase 7 — benchmark

See docs/BENCHMARK.md and `bench\`: write 30–40 tasks with a senior engineer, run each with and without the
codegraph tools, report per category.
