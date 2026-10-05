# Decisions and VERIFY results

Every `VERIFY` item of SPEC.md and every deliberate deviation from it. Status values:

* **verified** – checked by running code, on this repository's fixture (macOS, .NET 10.0.401, Neo4j 2026.09.0,
  libclang 18.1.1, Roslyn 5.9.0, ICSharpCode.Decompiler 11.1). The test that keeps it true is named.
* **pending (Windows)** – needs the real codebase or Visual Studio. The exact check to run is given;
  record the result here (date, machine, outcome) when it is done. `docs/RUNBOOK_WINDOWS.md` lists them in order.
* **decision** – a design choice, with the reason.

Open questions for the codebase owners (SPEC 0.7, "ask instead of guessing") are at the end.

---

## Extraction — C# (component A)

### D-01 · Extractor targets .NET 10, not net48 — SPEC 5.1 VERIFY · verified (SDK projects) / pending (Windows, legacy projects)
`Microsoft.CodeAnalysis.Workspaces.MSBuild` 5.x never runs MSBuild inside the extractor. It starts a
*BuildHost* process per project kind: `BuildHost-netcore` for SDK-style projects and `BuildHost-net472` for
legacy (non-SDK) projects on Windows; the net472 host locates Visual Studio's MSBuild itself (both hosts ship
`Microsoft.Build.Locator.dll`, see the package's `contentFiles/any/any/BuildHost-*`). So a .NET extractor can
load legacy .NET Framework projects, which is the spec's preferred outcome ("prefer net8.0 for speed").
.NET 10 instead of 8: .NET 8 support ends 2026-11-10; .NET 10 is the current LTS (to 2028-11).
* Verified: SDK-style net48 projects load and compile with 0 errors (fixture; `tests/test_fixture_edges.py`).
* **Windows check:** `CodeGraph.Roslyn --config <cfg> --list-projects`, then a full run; `out/csharp/load_report.json`
  must have no `Failure` entries for `.csproj` files and `manifest.json` must show 0 compile errors.
  If legacy projects fail to load, the fallback is a `net48` build of `CodeGraph.Roslyn` (+ `CodeGraph.Shared`),
  which needs small polyfills for newer APIs used here: `Path.GetRelativePath`, `Convert.ToHexStringLower`,
  LINQ `Order()`, `ReferenceEqualityComparer`, `[GeneratedRegex]`, `Parallel.ForEachAsync`.

### D-02 · No in-process MSBuildLocator · decision
Because evaluation happens in the BuildHost processes (D-01), the extractor never loads `Microsoft.Build.dll`,
so `MSBuildLocator.RegisterInstance` and the `ExcludeAssets="runtime"` rule (SPEC 5.1) do not apply. The package
was removed to keep the dependency list honest.

### D-03 · Extractors write JSONL only; the loader writes the neo4j-admin CSV · decision (deviation from SPEC 5.5/6.5)
Each extractor writes `<owner>.nodes.jsonl`, `<owner>.edges.jsonl` and `<owner>.meta.json` per assembly/module.
The loader merges all of them and writes the typed CSV once. Reasons: one CSV writer instead of three; typed JSON
values (no CSV type headers in C# and Python); per-owner files are exactly what incremental runs replace;
the full and incremental paths read the same input, so they cannot diverge. CsvHelper is not needed.

### D-04 · Edge aggregation · decision
Repeated occurrences of one relationship inside one owner (A calls B on lines 10, 14, 30) are one edge with
`line` = first occurrence, `lines` = all, `count`. Keeps path counts meaningful and traversal fast.
Key: (type, start, end, kind, via) — a call from a lambda and a direct call stay separate edges.

### D-05 · External targets are decided by the loader · decision
The C# extractor emits the semantic edge (CALLS, READS, ...) plus an `:External` stub for every target outside
its own assembly. The loader drops a stub when any extractor defines the real node (another project, another
solution, the bridge), and turns edges into stubs that remain into `REFS_EXTERNAL {rel: <original type>}`.
One decision point; cross-solution binary references join automatically. Stubs no edge uses are not imported.
C++/CLI targets carry `expectedFrom: "bridge"`; if the bridge did not produce them the load fails (SPEC 5.3)
unless `--allow-missing-bridge` is given.

### D-06 · Body pass details · decision (all verified on the fixture, `tests/test_fixture_edges.py`)
* Members are bound as a whole (`GetOperation(declaration)`), so constructor initializers and implicit
  `: base()` calls are in the operation tree. Implicit `: base()` calls into `System.Object` are dropped (noise).
* Implicit default constructors have no syntax; their base-constructor call is emitted by the symbol pass.
* Field/property initializers are attributed to every instance constructor that does not chain to `this(...)`
  (those run them), static ones to the static constructor; when none is declared, a synthesized
  `#cctor` node is emitted (the compiler creates one in IL). Const initializers are skipped (never run).
* `nameof(x)` is not a use of `x`. Compiler-generated backing fields get no node.
* `ref`/`out` arguments, `++`, `+=`, `??=`, deconstruction and address-of are classified as read/write.
* Delegate invocations are kept as calls to the delegate type's `Invoke`.

### D-07 · Exception message argument · decision (refines SPEC 5.3 "first string argument")
The message is the string parameter whose name contains `message`/`msg`/`format`, else the first string
parameter not named like a parameter name. `new ArgumentNullException(nameof(x))` therefore yields no message
instead of the fake message "x".

### D-08 · Message templates · decision (refines SPEC 5.4)
* Stored templates are always .NET composite format strings: literal braces are doubled, so `log.Error("a {b}")`
  (printed verbatim by a non-format method) and `ErrorFormat("a {0}", x)` are told apart. Each log rule has
  `template_style: literal | composite` (default: composite when the method name ends in `Format`).
* Constant pieces are inlined wherever the compiler knows them: `const` fields, `nameof`, constant interpolation
  holes; `string.Format` and `string.Concat` are followed.
* Native templates keep printf syntax (`templateStyle: printf`).
* The loader computes `regex` and `literalChars` (one implementation, `logmatch/templates.py`). Newlines in
  templates are stored as the two characters `\n` (one CSV row per record); the regex is built from the
  original text and never contains a raw newline.

### D-09 · `diagnostics: fast | full` · decision
`full` = `compilation.GetDiagnostics()` (a second full binding of every body, ~2x time). `fast` = parse +
declaration errors + count of `IInvalidOperation` met by the body pass (each one is a possibly missing edge).
Both feed the `max_compile_errors` gate; exit code 3 = output written, gate failed. Use `full` for gate runs.

### D-10 · REFERENCES kinds · decision
`project` (in the project file), `transitive` (SDK-style projects flow references), `binary` (to an indexed
assembly through a DLL reference), `pinvoke` and `ijw` (derived: managed assembly → native module it calls).
`layer_violations` and `assembly_graph` ignore `transitive` by default.

### D-11 · Peak memory on the full solution — SPEC 5.6 VERIFY · pending (Windows)
Mitigations already in place: one MSBuildWorkspace per solution, released before the next; at most
`min(4, cores/2)` projects compile concurrently. **Windows check:** run the full extraction under Task Manager /
`Get-Process` and record the peak working set here. If it exceeds ~75% of RAM, set `roslyn.max_parallelism`
lower or split the solutions list.

### D-12 · Accuracy check is automated · decision · verified on the fixture
`CodeGraph.Roslyn --accuracy 50` compares graph callers with `SymbolFinder.FindReferencesAsync` (the engine of
Visual Studio's Find All References) for 50 random methods, counting references to the method and to the
interface/base declarations it implements or overrides (computed from Roslyn symbols, not from the graph).
Fixture: 49 methods, 57 references, precision 100%, recall 100%. Real codebase: see docs/ACCURACY.md.

## Extraction — C++/CLI bridge (component C)

### D-13 · How native targets look in C++/CLI IL — SPEC 7.3 step 1 VERIFY · pending (Windows)
What the bridge expects (from ildasm output of mixed-mode assemblies; matches what the fixture simulates):
* a call into native code compiled in the same image is `call` of a **`<Module>` method** with
  `pinvokeimpl` and **no ImplMap row** (`pinvokeimpl(/* No map */) ... native unmanaged preservesig`),
  named with the readable scope **`Namespace.Class.Method`**, constructors `{ctor}`, destructors `{dtor}`
  (template arguments keep `::` inside `<...>`);
* member functions take `this` as an explicit first parameter `valuetype Ns.Class*`;
* a call of an imported DLL function may instead be `pinvokeimpl("x.dll")` **with** an ImplMap row → emitted
  as PINVOKES to `exp:x.dll!name`;
* **virtual native calls are `calli` through the vtable: no target in IL.** They are counted per method
  (`Method.indirectNativeCalls`) and listed as a known gap. The `WRAPS` edge still connects the wrapper to the
  native class.
* MSVC-mangled names (`?Grab@...`) are also accepted: they are matched against `mangledName`.

**Windows check:** `CodeGraph.Bridge --list-native --assembly <real wrapper DLL>` prints every pinvokeimpl method
with ImplMap/module/import name/flags. Compare with the bullets above and record the outcome here. If names are
mangled or differently shaped, `loader/merge.py:native_qualified_name` is the single place to adapt.

### D-14 · Bridge IDs equal Roslyn IDs — SPEC 7.3 step 1 VERIFY · verified (C#-compiled assemblies) / pending (real C++/CLI)
`tests/test_ids.py` runs `CodeGraph.Bridge --ids-only` on every compiled fixture assembly and compares with the
Roslyn extractor's IDs: identical sets (119 symbols, including generics, nested types, explicit interface
implementations, operators, `in` parameters). Risk on real C++/CLI: custom modifiers (`modopt(IsConst)`,
`modopt(IsLong)`) in signatures. **Windows check:** run a full load: the loader fails and lists every C# reference
into a C++/CLI assembly that the bridge did not produce (D-05). Zero missing = check passed.

### D-15 · Testing the bridge without MSVC · decision · verified
`fixtures/MiniPlant/interop/MiniPlant.Interop.Sim` is a C# stand-in with the same assembly name and public
surface as the C++/CLI project; `tests/tools/SimIjwRewriter` (Mono.Cecil) rewrites its stubs into exactly the
D-13 shape (no-map pinvokeimpl in `<Module>`, `{ctor}` names, `[NativeCppClass]` value types, `calli`). The
C# projects reference the real `.vcxproj` on Windows and the stand-in elsewhere (MSBuild `Condition` on `$(OS)`),
so C# IDs are identical on both. Bridge-only edges are checked through `must_have` in `tests/expected_edges.yaml`.

### D-16 · Bridge details · decision
* `WRAPS`: a field of type `T*` where `T` is a `[NativeCppClass]` value type; resolved by the loader against
  NativeType.fqn (unique → exact).
* Managed global functions live in `<Module>`, which every C++/CLI assembly has: their IDs are qualified as
  `M:<Module:AssemblyName>.Name(...)` so two assemblies never collide. CRT helpers (`_...`, `<...>`) are skipped.
* OVERRIDES / IMPLEMENTS_METHOD come from ILSpy's `InheritanceHelper` (C++/CLI often implements interfaces
  through explicit MethodImpls).
* Optional heuristic locations: `Class::Member(` and `ref class X` in the project's sources
  (`locationConfidence: heuristic`); C++/CLI destructors map to `Dispose`, finalizers to `Finalize`.
* Native name resolution happens in the loader (needs the native extractor's symbols): mangled name → exact;
  `Ns.Cls.Fn` → `Ns::Cls::Fn` qualifiedName, unique → exact, overloads narrowed by parameter count, else
  heuristic; nothing → `cpp:unresolved:<name> {resolved:false}`. Match rate is in `out/import/merge_report.json`
  and in the IndexRun warnings when below the 90% gate. Fixture: 2/2 exact.

## Extraction — native C++ (component B)

### D-17 · Compilation database — SPEC 6.1 VERIFY · pending (Windows)
Default: `extractors/native/make_compdb.ps1` rebuilds the solution into a scratch folder with
`CompileCommandsLogger` (an MSBuild logger in this repo, ~150 lines: it records every `CL` task command line and
splits multi-file invocations). Alternatives: `-Method extractor` for microsoft/msbuild-extractor-sample, or any
existing `compile_commands.json`. `python -m codegraph_native.vcxproj_compdb` is a fallback that reads simple
`.vcxproj` files directly (no property sheets); it is what the fixture uses off Windows.
**Windows check:** run make_compdb on the real solution; count entries vs `.cpp` files; then check the
diagnostics gate (D-19).

### D-18 · clang-cl driver mode and MSVC mangling — SPEC 6.3 VERIFY · verified
`--driver-mode=cl --target=x86_64-pc-windows-msvc` gives MSVC manglings (`?Move@NativeStage@Native@MiniPlant@@UEAAHN@Z`)
even on macOS. Extra `-f`/`-W` options are passed as `/clang:<option>`. On Unix an absolute path such as
`/Users/x.cpp` parses as the `/U` option in cl mode, so the argument list ends with `--`.
Constructors and destructors have several symbols (complete/deleting...): all of them are taken from
`clang_Cursor_getCXXManglings` for export matching (`dumpbin` lists e.g. `??1Class@@...`).

### D-19 · Builtin headers and the diagnostics gate · pending (Windows)
The pip `libclang` wheel ships no clang builtin headers (`stddef.h`, `intrin.h`...). Set `native.resource_dir`
to `<LLVM 18>/lib/clang/18` (or put a matching `clang` on PATH: it is detected, only if its major version equals
libclang's). Run from a VS developer prompt so `INCLUDE` is set. **Gate:** < 5% of translation units with errors
(`out/native/diagnostics.json`; the manifest warns above that).

### D-20 · /clr split — SPEC 6.2 VERIFY · verified (fixture) / pending (count on real code)
Any `/clr` or `-clr[:...]` flag skips the unit; list in `out/native/clr_files.json`. Fixture: 1 of 4 units.

### D-21 · Overrides — SPEC 6.3 · decision · verified
The Python bindings lack `get_overridden_cursors`, but libclang exports `clang_getOverriddenCursors`; it is called
through ctypes (`clangtools.py`), so overrides are exact - no signature-matching fallback is needed.

### D-22 · Exports · decision
`native.exports_from: dumpbin` (built DLLs, ground truth, Windows) or `source` (`__declspec(dllexport)` on the
function or its class, any platform). Module (DLL) of a file = `native.modules` globs, else the nearest `.vcxproj`
above it (its `<TargetName>`). DLL names are normalized like `DllImport` does (`X`, `X.dll`, `..\X.DLL` → `x.dll`).

### D-23 · Per-unit cache · decision
Key = sha1(cache version, libclang version, source path, arguments, log rules); a hit also requires the source
and every included in-repo header to have the same sha1 as when cached (SPEC's key alone would miss header edits).

## Loader (component D)

### D-24 · neo4j-admin flags — SPEC 8.2 VERIFY · verified (Neo4j 2026.09.0)
`neo4j-admin database import full <db> --overwrite-destination=true --id-type=string --array-delimiter=;`
`--nodes=... --relationships=...`. **The database name must come right after `full`**: `--relationships` takes
several files and swallows a trailing name. Labels come from a `:LABEL` column (`Method;Node`), types from `:TYPE`.

### D-25 · Universal `:Node` label · decision
Every node also has `:Node` with a unique `id` constraint, so every lookup by ID (all tool queries, incremental
MERGE) is an index seek. SPEC's per-label constraints are kept as well.

### D-26 · Derived data computed in the merge, not with Cypher · decision (deviation from SPEC 8.2 step 5)
DISPATCHES_TO (same rule as the spec's two queries: implementations and transitive overrides), `ui_handler`
and `native_callback` entry points, REFERENCES to native modules, message regexes and layers are computed in
Python while streaming the edges. The bulk importer then writes them at full speed, and the incremental path
reuses the exact same code. Verified by `tests/test_merge.py` and `tests/test_incremental.py`.

### D-27 · Minimum Neo4j version 5.26 · decision
Uses `CALL (x) { ... } IN TRANSACTIONS` (5.23+), `COLLECT { }` subqueries (5.6+) and `COUNT { }`. Tested on 2026.09.

### D-28 · Incremental equals full rebuild — SPEC 8.3 VERIFY · verified
`tests/test_incremental.py`: temp git copy of the fixture → full index → edit a method, delete a method, add a
file, change native code → `loader incremental` → dump; full rebuild → dump; the dumps are identical.
Details: changed files from `git diff <last indexed commit>` + untracked files; owners from the per-project
meta files; projects whose edges point at removed IDs are re-extracted too; owned nodes/edges replaced,
vanished nodes deleted (`seenRun`); DISPATCHES_TO, CO_CHANGES_WITH, derived entry points and git file
statistics recomputed globally; orphan stubs removed. Hashes of generated files (`obj/`) are not stored (they
change with every build). Real-codebase timing: pending (SPEC Phase 6 gate).

### D-29 · Read-only access on Community Edition — SPEC 10 · decision · verified
Community has no roles: every account can write. The MCP server runs every query in a READ transaction
(Neo4j refuses writes there; `tests/test_mcp_tools.py::test_queries_run_read_only`), with a timeout, and only
fixed queries exist. `python -m loader users` creates `codegraph_ro`; on Enterprise it also grants `reader`.

## Query side (components E and F)

### D-30 · No variable-length patterns — SPEC 10 VERIFY · decision · verified
Instead of `[:CALLS*1..$depth]` (whose bound cannot be a parameter, and which enumerates every path), traversals
are breadth-first with one indexed query per level and per-call neighbour caching. Every reached node appears
once with a shortest path; results are deterministic; depth/limit are clamped server-side.
Connector hops (DISPATCHES_TO, PINVOKES, RESOLVES_TO) cost no depth: depth counts real calls, so an interface
call or a P/Invoke does not eat the budget (fixture: `entry_points_reaching` from a native function finds `Main`).

### D-31 · Symbol arguments accept names · decision
Tools accept an ID, a fully-qualified name, `Type.Member`, `ns::Cls::fn` or a short name; an ambiguous name
returns the candidates instead of a guess. Results are compact JSON (no indentation): agents pay per token.

### D-32 · MCP SDK version · decision
`mcp` 2.x renamed `FastMCP` to `MCPServer`; the server imports either. Tools are annotated read-only/idempotent.

### D-33 · Measured performance · verified (synthetic, macOS, 8 cores, 16 GB)
`tests/tools/generate_scale_solution.py` (20 projects, 2,000 files, 40,000 methods, **418,000 lines**; every method
calls 3 methods in lower projects, logs and throws):

| Step | Time | Notes |
|---|---|---|
| C# extraction, `diagnostics: fast` | 9.7 s | peak memory 1.4 GB; 0 compile errors; 320k CALLS, 542k relationships total |
| C# extraction, `diagnostics: full` | 11.9 s | |
| Merge + CSV | ~5 s | streaming; 94k nodes |
| neo4j-admin import | ~6 s | plus ~20 s Neo4j stop/start (Community needs the database offline) |
| `callers_of` depth 2 / 4 / 8 | 0.2 / 0.15 / 0.9 s | depth 8 reaches 4,084 callers (200 returned) |
| other tools | 40–300 ms | |

Real legacy projects add MSBuild design-time evaluation (~1–3 s per project, sequential per solution) on Windows.
Extrapolated to 1M lines: C# extraction well under a minute plus project evaluation; the load stays under ~1 minute.
MSBuildWorkspace never restores NuGet packages: restore (build) the solution first, or references go missing and
the compile-error gate fails (seen with an unrestored test solution: 172 errors per project).

### D-34 · Compact tool output · decision
Tool results are read by agents that pay per token. Traversal results are a tree: each entry has the node id,
location, depth, `next` (the neighbour one step towards the start; follow it to rebuild a path) and the edge
(`rel` when not CALLS, `at` = call site, `via`, `kind`); `confidence` appears only when heuristic. Full paths are
included only where results are few (entry points, tests, native crossings), as readable strings
(`"-CALLS@src/a.cs:12-> dn:M:..."`). Readable IDs are not repeated as `fqn`. On the scale test this cut
`callers_of` depth 4 from 87 KB to 14.5 KB (~3.7k tokens) with the same information.

---

## Open questions for the codebase owners (put the answers in config/codegraph.yaml)

| # | Question | Config key | Default used now |
|---|---|---|---|
| Q1 | Which logging APIs are used (C# and native), and which argument is the message/format? | `log_methods`, `native_log_functions` | log4net `ILog`; native `LogMessage(level, fmt, ...)` |
| Q2 | How is configuration read (AppSettings, a settings class, a custom service)? | `config_accessors` | `ConfigurationManager.AppSettings` |
| Q3 | DI container and plugin loading (MEF, custom attributes, reflection by name)? | `entry_point_attributes` (+ v2 DI rules) | WCF `OperationContract` only |
| Q4 | Intended layering of assemblies (lowest first)? | `layers` | 4 example layers |
| Q5 | Test framework(s) and test project naming? | `test_attributes`, `test_assembly_patterns` | NUnit/MSTest/xUnit, `*.Tests` |
| Q6 | UI technology (WinForms, WPF) and base types whose events are UI events? | `ui_event_base_types` | `Control`, `UIElement` |
| Q7 | Which DLLs are C++/CLI and which are native, and where are their sources? | `cppcli_assemblies`, `native_dlls`, `native.modules` | example paths |
