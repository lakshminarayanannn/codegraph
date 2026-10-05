# Walkthrough

Every file, top to bottom, grouped by component in the order data flows: configuration → extractors → loader →
log matcher → MCP server → fixture → tests → scripts. Decision numbers (D-nn) refer to docs/DECISIONS.md.

---

## Configuration

### `config/codegraph.yaml`
The template for a real codebase. Sections in order: paths (`repo_root`, `solutions`, `msbuild_properties`,
`max_compile_errors`, `output_dir`); `roslyn` (parallelism, diagnostics mode, excluded projects, preferred target
framework); `cppcli_assemblies` (path + optional name/source_dir); `native_dlls`; `native` (compilation database,
clang target, extra args, export source, module mapping, workers); the conventions marked **ASK** (`log_methods`
with `template_style`, `native_log_functions`, `config_accessors`, test attributes/patterns,
`entry_point_attributes`, `ui_event_base_types`, `layers`); `git_history`; `log_prefix_regex`; `neo4j` (URI,
accounts, *names of* the password environment variables, install folder, import method); `limits`.
Relative paths resolve against the folder holding the file (both languages implement the same rule).

### `pyproject.toml`
Python packaging: dependencies (neo4j driver, PyYAML, MCP SDK, libclang), optional `fast` (orjson) and `test`
extras, the five packages (the native extractor lives under `extractors/native/`), pytest markers.

### `.gitignore`
Ignores generated data (`out/`), build output, the virtual environment, and local secrets (`.env`,
`config/*.local.yaml`).

---

## Shared C# library — `extractors/CodeGraph.Shared`

### `Records.cs`
* `NodeRecord`: one class with every node property as a nullable field, grouped by label (common, containment,
  flags, classification, File, Message, ConfigKey, NativeExport, native, External). Null fields are not written,
  so each label carries only its own properties.
* `EdgeRecord`: type/start/end/owner, location (`File`, `Line`, `Lines`, `Count`, `IlOffset`), `Kind`, `Via`,
  `Confidence`, then relationship-specific fields (`ExceptionType`, `Level`, `Handler`, `UiEvent`,
  `CallbackKind`, `Implicit`, `LineEnd`, `NativeName`, `NativeParamCount`).
* `RecordJsonContext`: System.Text.Json source generator (camelCase, skip nulls) - reflection-free and fast for
  millions of records.

### `Ids.cs`
The ID rules of SPEC 4.1, used by both C# extractors: `Dn`, `Assembly`, `File` (lower-cased, forward slashes),
`Export` (via `NormalizeDll`: file name, lower case, `.dll` appended unless `.dll`/`.exe`), `Message`
(sha1 of template + "|" + emitter), `Config`, `Sha1Hex`, `RelativeTo`. `codegraph_common/ids.py` mirrors it.

### `EdgeAggregator.cs`
* `EdgeAggregator`: a concurrent dictionary keyed by (type, start, end, kind, via, discriminator). `Add` counts the
  occurrence, collects its line, and keeps the record with the earliest (file, line, IL offset) so parallel threads
  produce identical output (`Merge` also ORs optional facts like `CallbackKind`). `Drain` sorts by key and sets
  `Line`/`Lines`/`Count` (D-04).
* `NodeSet`: concurrent node collection; a real node replaces an `External` stub with the same ID, otherwise the
  earliest location wins; `Drain` sorts by label and ID.

### `JsonlWriter.cs`
`Write` serializes one record per line through a `Utf8JsonWriter` into a temp file, then renames it (readers never
see half a file). `WriteOwner` writes the three per-owner files; `WriteJson` writes indented meta/manifest JSON;
`SafeFileName` keeps owner names inside the output folder.

### `Config.cs`
`CodeGraphConfig` deserializes the YAML (underscored names, unknown sections ignored), records the config folder,
and normalizes `cppcli_assemblies` entries (string or map) into `CppCliAssembly(Path, Name, SourceDir)`.
`ResolvePath` applies the relative-path rule. Rule classes: `RoslynSettings`, `LogMethodRule`,
`ConfigAccessorRule`, `EntryPointAttributeRule`. `Glob` matches config patterns like `*.Tests` case-insensitively.

---

## Component A — `extractors/CodeGraph.Roslyn`

### `CodeGraph.Roslyn.csproj`
Exe on .NET 10 with the two Roslyn workspace packages; the comment explains why MSBuildLocator is absent (D-01, D-02).

### `Program.cs`
`Main` parses options, runs `Extraction.RunAsync`, maps exceptions to exit code 1. `ExitCodes` names the codes the
scripts check (3 = compile-error gate failed, output written). `Options` parses `--config`, `--out`,
`--project` (repeatable), `--run-id`, `--diagnostics`, `--max-parallelism`, `--list-projects`, `--accuracy`,
`--accuracy-seed`, and validates them.

### `Settings.cs`
Everything resolved once: default test attributes (NUnit, MSTest, xUnit) merged with config ones, test framework
assembly names, diagnostics mode, parallelism, `--project` filter, C++/CLI assembly names, entry-point attribute
map, UI base types. `ProjectParallelism` caps concurrent projects (each also binds documents in parallel).

### `Extraction.cs`
`RunAsync`: load config, pick the output folder, then per solution: create a workspace (`CreateWorkspace` sets
`SolutionDir` and records every workspace diagnostic), open the solution (with `LoadProgress` printing each
project's load time), select projects, and either list them, run the accuracy check, or extract them in parallel.
Afterwards it finds projects over `max_compile_errors`, writes `manifest.json` (tool/Roslyn/runtime versions,
projects without their file lists, skipped projects, totals by label and type, gate) and `load_report.json`, and
returns exit code 3 when the gate fails. The workspace is disposed before the next solution (SPEC 5.6).

### `ProjectSelection.cs`
Groups the solution's projects by file path (a multi-targeted project appears once per framework) and picks one
(`prefer_target_framework` or the first). Skips, with a reason: non-C# projects (`.vcxproj`), C++/CLI assemblies
(bridge-owned), excluded names. Projects shared between solutions are processed once; `--project` filters by
project or assembly name. `BaseName` strips the `(net48)` suffix.

### `GraphBuilder.cs`
Per-project state shared by both passes. Display formats for fully-qualified names and signatures. `Normalize`
maps uses to declarations (reduced extension method → static method, generic instance → definition).
`Id` returns `dn:` + documentation ID (cached; null for lambdas, local functions and error symbols).
`Target` returns a target's ID and, for symbols outside the assembly, adds an `External` stub (with
`expectedFrom: bridge` for C++/CLI assemblies) (D-05). `Fqn`, `KindName`, `RelativePath` (cached), `Span`
(first declaration by path then position; implicit members fall back to their type's location), `Line`,
`Attributes`, `AddEdge`. `InitializerOwners` / `StaticConstructorId` / `SynthesizedStaticConstructorId` decide
which constructors run an initializer (D-06).

### `Rules.cs`
Configuration-driven matching. Logging: rules indexed by method name (fast reject), then cached per target method
(`type` exact or `type_implements` through interfaces and base types); `StyleOf` picks literal/composite;
`LevelOf` reads the level from the method name, an argument (enum member name or constant) or a fixed value.
Config reads: `Get("key")`/indexer on a configured collection property, a configured method's key argument, or
any property of a configured settings type. `IsUiEvent` checks the event's declaring type and the receiver's type
against the UI base types. `IsTest`, `EntryKind` (main, then attributes on the method, then on the type for public
methods) and `IsPublicApi` implement SPEC 5.7.

### `Templates.cs`
Turns a message argument into a canonical composite-format template (D-08). `Build` returns `<dynamic>` when no
literal text was found. `Unwrap` strips conversions. The builder appends: constants (literals, const fields,
`nameof`) as text; interpolated strings part by part; `+` chains recursively; `string.Format` by following its
`format` argument in composite style; `string.Concat` (including `params` arrays); anything else as a `{N}` hole.
Literal text has its braces doubled; composite text is kept and checked for literal characters.

### `SymbolPass.cs`
Walks the assembly's namespaces. For each type: a `Type` node, CONTAINS, HAS_MEMBER from the containing type,
one DEFINED_IN per declaration (partial types), INHERITS (implicit bases skipped), IMPLEMENTS, the interface
mapping, then each member, then a synthesized static constructor when static initializers need one.
`MapInterfaceImplementations` uses `FindImplementationForInterfaceMember`, skipping default interface methods,
external implementations and mappings a base type already declares. `VisitMethod` emits the node with all
classification flags, HAS_MEMBER, OVERRIDES, P/Invoke declarations (`EmitPInvoke` → NativeExport + PINVOKES),
and the implicit base-constructor call of implicit constructors. `VisitField`, `VisitProperty`, `VisitEvent`
emit the other members. Helpers: kind and accessibility names, constant formatting, `True` (flags written only
when set; the loader fills false).

### `BodyPass.cs`
One instance per syntax tree. `Members` walks namespaces and types without entering bodies. `VisitMember` binds
methods/constructors/operators (whole declaration), properties (expression body → getter; accessors; initializer
→ constructors), and non-const field initializers → constructors. `Walk` dispatches on the operation type:
`nameof` ignored; lambdas and local functions walked with `via`; invocation, creation, property, field, event
assignment, method reference, throw, initializer writes; invalid operations counted; then children.
Handlers: `OnInvocation` (CALLS with ctor/call kind, skipping local functions and implicit `object()` calls;
marks method groups passed to native code; log and config rules), `OnCreation`, `OnProperty` (getter/setter calls
plus READS/WRITES, overridden accessors found up the chain), `OnField`, `OnEventAssignment` (SUBSCRIBES with
handler and UI flag), `OnMethodReference` (REFERENCES_METHOD with callback kind), `OnThrow` (EMITS of kind
exception). Emit helpers create edges and message nodes per owner. `AccessOf` classifies read/write from the
parent operation. `MessageArgument` picks the exception's message parameter (D-07).

### `ProjectExtractor.cs`
`ExtractAsync`: compile, count errors (`CompileErrors`: fast or full, D-09), emit the Assembly node and its
REFERENCES (`DirectProjectReferences` reads `<ProjectReference>` from the project file to tell direct from
transitive, D-10), emit File nodes (hash omitted for generated files), run the symbol pass, run the body pass over
all syntax trees in parallel, add unresolved operations to the error count in fast mode, then write the owner's
files and meta (counts, errors, file list for incremental mapping). `TargetFramework` reads the
`TargetFrameworkAttribute`, else the project-name suffix.

### `Accuracy.cs`
SPEC 11.2, automated (D-12): collect the selected projects' methods, sample N with a fixed seed, ask
`SymbolFinder.FindReferencesAsync` for each, keep references to the method and its "up chain" (`UpChain`:
overridden methods and the interface methods any of them implement), drop crefs and `nameof`, map each reference
to its containing member (`Owners`: lambdas/local functions → member; initializers → constructors), and compare
with the graph callers read from the JSONL (`GraphIndex.CallersOf` adds callers of implemented/overridden
declarations, i.e. what DISPATCHES_TO contributes). Writes `accuracy.json` with per-method misses and extras.

---

## Component C — `extractors/CodeGraph.Bridge`

### `CodeGraph.Bridge.csproj`
Exe on .NET 10 with ICSharpCode.Decompiler.

### `Program.cs`
Parses `--config`, `--out`, `--assembly` (repeatable override), `--ids-only` (prints every member ID: the ID
equality check, D-14) and `--list-native` (the Phase 0 spike listing, D-13). `Run` extracts each configured
assembly (missing DLLs become warnings) and writes the manifest with the decompiler version.

### `IlScanner.cs`
`References` decodes a method body: one- and two-byte opcodes, operand sizes from the runtime's own `OpCodes`
table, yielding only instructions with a metadata token (calls, `newobj`, `ldftn`, `calli`, field loads/stores)
with their IL offset. `IsUnmanagedCalli` reads a `calli` signature's calling convention (unmanaged = a native
function pointer, typically a virtual native call).

### `BridgeExtractor.cs`
Top to bottom: native-proxy attribute names; state; `ManagedIds` (ID check); `ListNativeStubs` and `Describe`
(spike listing); `Extract` (open, assembly node, types, global functions, IL scan of every method, write);
`Open` (PEFile, assembly resolver, ILSpy type system, the set of `[NativeCppClass]` proxy types);
`ManagedTypes`/`Members` (skips `<Module>`, proxies, compiler-generated types and members);
`EmitAssembly` (target framework, REFERENCES to every referenced assembly - the loader keeps indexed ones);
`EmitType` (node with heuristic location, CONTAINS, nested HAS_MEMBER, INHERITS/IMPLEMENTS, members);
`EmitMethod` (node, HAS_MEMBER, OVERRIDES only for overrides, IMPLEMENTS_METHOD via `InheritanceHelper`,
PINVOKES for P/Invoke declarations); `EmitGlobalFunctions` (`<Module>` managed functions, D-16); `EmitField`
(plus WRAPS for pointers to native proxies); `EmitMember` (properties, events); `EmitPInvoke`;
`ScanBody` (IL methods only; calli counted per method into `indirectNativeCalls`); `Call` (pinvokeimpl in
`<Module>` → `NativeCall`, else CALLS with ctor/getter/setter kind plus READS/WRITES for accessors, or
REFERENCES_METHOD for `ldftn`); `NativeCall` (ImplMap present → PINVOKES; "No map" → CALLS_NATIVE with the
metadata name and the parameter count without `this`); `FieldAccess`; helpers for IDs (`MemberId` qualifies
`<Module>`), type IDs of unresolved types, compiler-generated names, source names of constructors/destructors,
heuristic locations, signatures, attributes, kinds and accessibility.

### `SourceLocator.cs`
Optional heuristic locations: scans the C++/CLI project's `.cpp`/`.h` files once for `Type::Member(` and
`ref|value|interface class|struct Name`, first match wins.

---

## Component B — `extractors/native`

### `codegraph_native/compdb.py`
`load` reads `compile_commands.json` (`arguments` or `command`), marks `/clr` units, and `clean`s the arguments
(drops outputs, PCH options, `/MP` and the source file; keeps includes, defines, standard, forced includes).
`split_command` implements Windows command-line splitting.

### `codegraph_native/clangtools.py`
ctypes access to the C API the bindings lack: `overridden_usrs` (`clang_getOverriddenCursors`, D-21),
`all_manglings` (`clang_Cursor_getCXXManglings`, with a `CXString` struct without the bindings' destructor to
avoid a double free), `clang_version`, and `resource_dir` (configured, or a same-major clang on PATH, D-19).

### `codegraph_native/walker.py`
`parse` parses one unit (keep-going mode), walks it, and returns records, error count and the repo headers it
included (with hashes, for the cache). `Walker`: `relative` (cached repo-relative path or None outside the repo),
`walk`/`visit` (top-level pruning of system headers; namespaces and `extern "C"` blocks recursed; types and
functions handled), `on_type` (definitions only; INHERITS to in-repo bases), `on_function` (qualified name,
mangled name(s), signature, flags, parameter count, extern "C", dllexport; definition beats declaration;
OVERRIDES; body scan for definitions), `scan_body` (iterative DFS; lambdas tag `via`), `on_call` (static targets
inside the repo; others counted as external), `on_log` (template from the configured argument's string literal,
level from an enum constant or number). Helpers: `qualified_name`, `safe_mangled`, `has_dllexport`,
`string_literal` (adjacent literals joined, C escapes decoded), `level_name`.

### `codegraph_native/exports.py`
`dumpbin_exports` parses `dumpbin /exports`; `source_exports` lists dllexport functions per DLL (C name or
mangled name); `resolve` creates NativeExport nodes and RESOLVES_TO edges (mangled names incl. all
constructor/destructor manglings, then extern "C" names).

### `codegraph_native/extract.py`
`run`: load the database, record `/clr` units, build clang-cl arguments (D-18, the trailing `--`), parse units in a
process pool through `_parse_cached` (D-23), merge them (`merge`: definitions win, edges unioned with their lines,
edges to unknown targets dropped), assign modules (`ModuleMap`: config globs, else nearest `.vcxproj` and its
`TargetName`), write per-module files (`write_modules`: NativeType/NativeFunction/Message/File nodes, DEFINED_IN,
HAS_MEMBER, CONTAINS, CALLS/OVERRIDES/INHERITS/EMITS, exports, Assembly nodes), write `diagnostics.json` and the
manifest with the < 5% gate warning.

### `codegraph_native/vcxproj_compdb.py`
Fallback database generator: finds `.vcxproj` files in the configured solutions and turns their item definitions
(include directories, defines, language standard, CLR support) for the configured configuration/platform into
`cl.exe` command lines. Limitations are stated in the module docstring.

### `codegraph_native/__main__.py`
CLI: resolves the database path, explains how to create one if missing, runs `extract.run`.

### `make_compdb.ps1` and `CompileCommandsLogger/`
The Windows database generator (D-17): builds the logger, rebuilds the solution into a scratch folder with it,
or calls msbuild-extractor-sample with `-Method extractor`. `CompileCommandsLogger.cs` listens for `CL` task
command lines, splits sources from options, and writes one entry per source file.

---

## Shared Python — `codegraph_common`

* `config.py` — `Config` (typed accessors with defaults: paths, extractor folders, layers + `layer_of`, git
  history, limits, native settings, C++/CLI assemblies, Neo4j settings) and `load_dotenv`. `Neo4jSettings`
  resolves accounts (reader falls back to writer) and finds `neo4j`/`neo4j-admin`.
* `ids.py` — the Python twin of `Ids.cs`.
* `jsonl.py` — JSONL read/write (orjson when installed), atomic JSON write.
* `schema.py` — node and relationship property types, boolean defaults, `VIA_TYPES`, and every constraint/index
  statement (SPEC 4.4 plus `:Node(id)`, file keys, entry/test flags, the IMPLEMENTS_METHOD owner index, full-text).
* `log.py` — stderr logging (stdout is reserved for command output and the MCP protocol).

---

## Component D — `loader`

### `merge.py`
`RunInfo`, `MergeReport` (load counters, per-pass edge counters, conflicts, errors, warnings, bridge statistics).
`Merger`: `load_nodes` (all origins; `_add_node` applies the priority rules and logs conflicts);
`edges` (a fresh pass over all edge files) → `_normalize_edge` (resolve bridge placeholders, drop edges from
missing or external sources, drop references to unindexed assemblies, create stubs, convert to REFS_EXTERNAL,
dedupe, `_observe`); `_stub`; `_observe` (remembers dispatch pairs, UI handlers, native callbacks, native module
references and every touched ID); bridge resolution (`_index`, `_resolve_native`); derived edges
(`_dispatch_edges`, `_native_reference_edges`, `_co_change_edges` + `_ensure_file_node`); `finalize_nodes`
(bridge expectation check, unused stubs dropped, regex/literal count, layer, git stats, derived entry points,
defaults, file key, run stamp, `_canonical` newline escaping); `finalize_edge` (via/confidence defaults, run
stamp, counts). `native_qualified_name` converts C++/CLI metadata names (D-13). `GitHistory` holds file
statistics and co-change pairs.

### `csvout.py`
`_quote`/`_cell` encode values for neo4j-admin. `CsvWriter` streams relationship rows into one file per type
(header from the schema) and writes node files per label (schema columns plus any unknown properties as strings,
`:LABEL` = `Label;Node`).

### `admin.py`
`admin_import`: stop Neo4j, run `neo4j-admin database import full <db> ...` (database name first, D-24), save the
tool's output, restart Neo4j (also after a failure).

### `neo.py`
`Graph` (driver with notifications off, `run`, `autocommit` for `CALL ... IN TRANSACTIONS`, `write_batches`,
`wait_until_available`, `apply_schema`). `upsert_nodes` (MERGE on `:Node(id)`, set label, drop `External`, or
create-only for stubs), `create_edges`, `delete_all`.

### `git_history.py`
`git` helper, `head_commit` (SHA + dirty flag), `collect` (one `git log --relative --name-only` over the window;
tracked files only; per-file count and last date; co-change pairs for commits up to the size limit).

### `pipeline.py`
`new_run`, `git_history`, `full` (merge then `_admin_load` or `_bolt_load`), `_admin_load` (stream edges to CSV,
finalize nodes, fail on errors, import, schema), `_bolt_load` (observe pass, nodes, then edges in batches),
`finish_run` (IndexRun node, warnings incl. extractor gates and the bridge match-rate gate, merge report),
`_extractor_warnings`, `_tool_versions`, `status`, `dump` (canonical graph JSON without run stamps).

### `incremental.py`
`incremental`: last run → changed files → plan → re-extract (C# with dependents, native, bridge) → apply.
`changed_files` (git diff + untracked, or "everything" without a usable base), `make_plan` (files → owners via
meta files, new files by project folder, deleted projects, native/C++/CLI flags), `reextract_csharp` (before/after
member IDs; dependents with edges to removed IDs), `run_tool`/`tool_command` (configurable extractor commands),
`apply` (same merge as a full load; owned relationships deleted, owned nodes upserted, touched foreign nodes
created if missing, owned edges created, vanished nodes deleted) and `_refresh_global` (dispatch and co-change
edges, derived entry points, git statistics, orphans). Helpers read meta files and owner outputs.

### `users.py`
Creates or updates the read-only account; grants `reader` where roles exist (D-29).

### `__main__.py`
CLI for `full`, `incremental`, `status`, `dump`, `users`; returns 2 when a gate stops the load.

---

## Component E — `logmatch`

* `templates.py` — `compile_template` (split into literal pieces and holes for dotnet or printf style; holes →
  `(.+?)`, adjacent holes merged, control characters escaped; `literal_chars`), `strip_prefix`, `search_tokens`.
* `matcher.py` — `CANDIDATES_QUERY` (full-text pre-filter + emitting methods), `lucene_query` (escaping),
  `find_sources` (strip prefix, tokens, candidates, optional exception-type filter, regex test, deterministic
  ranking), `_type_matches`.

---

## Component F — `mcp_server`

* `graph.py` — `ReadOnlyGraph` (READ transactions with timeout; `index_info` cached 30 s: last run commit,
  staleness against `git rev-parse HEAD`), `NODE_FIELDS`/`node_fields` (the projection every query returns),
  `compact` (full node view with `loc`, for headers and outlines), `brief` (small list view, D-34), `label`
  (readable name for paths), `clamp`.
* `traverse.py` — relationship sets per direction, the one-level step query, `Hit`; `Traversal` (`path` as
  readable hop strings in call order, `confidence`, `entry` = one tree entry with `next`/`rel`/`at`); `Traverser`
  (neighbour cache; `bfs` with zero-cost connector hops, D-30); `_hop` formats one edge.
* `resolve.py` — `find` (id → fqn → name → `Type.Member`/`ns::fn` suffix → full text with prefix wildcards),
  `resolve` (unique exact match or candidates), `unresolved`.
* `diff.py` — `changed_ranges` (`git diff --unified=0` between two validated refs) and `parse` (hunk ranges in the
  new file; deleted files use old ranges; pure deletions map to their position).
* `tools.py` — `Tools.call` (runs a tool, turns ambiguity into a normal result, appends the index stamp), then the
  tools in SPEC order with their private helpers (`_outline_*`, `_family`, `_symbols_in_file`, `_accesses`), and
  module helpers (`gitdiff_head`, `_type_of`, `_is_native`).
* `server.py` — MCP SDK import (2.x `MCPServer` or 1.x `FastMCP`), read-only annotations, server instructions,
  and one registered function per tool whose docstring is the description the agent sees.
* `__main__.py` — CLI: build and run the server (stdio by default).

---

## Fixture — `fixtures/MiniPlant`

* `Directory.Build.props` — .NET Framework 4.8, latest C#, one shared `bin/` folder.
* `MiniPlant.sln` — five C# projects and two C++ projects (as Visual Studio writes it); `MiniPlant.Managed.slnf`
  builds only the C# ones (non-Windows).
* `src/MiniPlant.Core` — `IStage`; `StageBase` (interface implementation, virtual `Calibrate`, abstract
  `DoMove`, event, interpolated/const/`string.Format` logs, a throw); `LinearStage`/`PrecisionLinearStage`
  (override chain, concatenation and `*Format` logs, P/Invoke, native callback, reflection-only and dead methods);
  `PiezoStage` (second implementation, uses the C++/CLI wrapper, field initializer); `MotionController` (call
  through the interface, lambda, local function); `Registry`/`StageExtensions` (generic and extension methods);
  `Recipe` (partial class in two files, throw); `NativeMethods` (DllImports, callback delegate).
* `src/MiniPlant.Services` — event subscription, config read, field read/write, the layer violation, a WCF
  contract (remote entry point).
* `src/MiniPlant.UI.Controls`, `src/MiniPlant.App` — a WinForms control, `Main`, a UI event handler, a `.Get(key)`
  config read.
* `src/MiniPlant.Tests` — NUnit tests that reach some methods and not others.
* `native/MiniPlant.Native` — class hierarchy with a pure virtual and overrides, a call through a base pointer,
  a printf-style log through a macro, `extern "C"` exports.
* `interop/MiniPlant.Interop` — the real C++/CLI wrapper (Windows); `interop/MiniPlant.Interop.Sim` — its C#
  stand-in elsewhere (D-15).
* `codegraph.yaml` — the fixture's configuration (relative paths, `exports_from: source`).

---

## Tests — `tests`

* `conftest.py` — build + extract the fixture once (`extracted`), load it into Neo4j once (`neo4j_config`).
* `fixture_graph.py` — run the merge without Neo4j; `describe` (one-line edge identity); `comparable`
  (edges between indexed nodes minus the platform-specific owner).
* `expected_edges.yaml` — `must_have` per fixture feature, node and message expectations, the reviewed `exact` set.
* `test_fixture_edges.py`, `test_ids.py`, `test_mcp_tools.py`, `test_incremental.py` — integration tests.
* `test_templates.py`, `test_compdb.py`, `test_merge.py`, `test_logmatch.py`, `test_git_and_diff.py` — unit tests.
* `tools/snapshot_edges.py` — regenerates the `exact` section; `tools/SimIjwRewriter` — Mono.Cecil rewriter that
  gives the C# stand-in the C++/CLI IJW metadata shape; `tools/generate_scale_solution.py` — synthetic large
  solution for performance measurements (D-33).

## Scripts — `scripts`

`setup_dev.(sh|ps1)` (one-time setup), `build_fixture.(sh|ps1)`, `full_index.(sh|ps1)` (SPEC 15 full rebuild:
build, C#, native, bridge, load, reader account, status), `incremental_index.(sh|ps1)`.
