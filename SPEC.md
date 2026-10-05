# Codebase Knowledge Graph — Implementation Spec v1.1

Target codebase: ~1M lines, .NET Framework, ~95% C#, ~4% native C++, ~1% C++/CLI (`/clr`).
Storage: Neo4j Community Edition (proof of concept; Enterprise later).
Consumers: any LLM agent (Claude Code for development, bug-triage bots, review bots) and engineers, through an MCP tool server and Neo4j Browser.

The graph is a general, compiler-accurate model of the codebase. It is not tied to one use case: development, code review, impact analysis, onboarding, test selection, architecture checks and debugging all query the same graph through different tools.

---

## 0. Instructions for the implementing agent (read first)

1. **Work phase by phase (Section 14).** Each phase ends with a verification gate. Stop at every gate, report results to the human, and wait for approval before starting the next phase.
2. **Every line must be explainable.** This company requires engineers to explain each line of code. So:
   - comment every non-obvious line (why, not what);
   - keep functions short;
   - after each phase, append a section to `docs/WALKTHROUGH.md` explaining each new file top to bottom.
3. **Items marked `VERIFY` are assumptions.** Before writing code that depends on one, write a tiny spike that checks it on the real codebase, and record the result in `docs/DECISIONS.md`.
4. **Never send source code off the machine.** No telemetry, no external APIs. All tools run locally.
5. **Prefer determinism.** No embeddings and no LLM calls anywhere in the indexing pipeline. Every edge must come from a compiler, a metadata reader, or an explicit, documented rule.
6. **Make every edge traceable.** If an edge cannot be traced to a file and line (or to an assembly and IL offset), mark it `confidence: "heuristic"` and say so in tool output.
7. **Ask instead of guessing** when the codebase's conventions are unclear: logging APIs, DI container, plugin loading. Put the answers in `config/codegraph.yaml`, not in code.

---

## 1. Goal and non-goals

**Goal:** one compiler-accurate graph of the codebase that answers structural questions deterministically, for every kind of engineering work:

| Use case | Example questions | Main tools (Section 10) |
|---|---|---|
| **Development (orientation)** | "Where is stage motion implemented?" "What does this class depend on?" "Show me the outline of this module." | `find_symbol`, `outline`, `dependencies_of`, `callees_of` |
| **Development (change planning / impact)** | "If I change `Stage.Move`'s signature, what breaks?" "Which assemblies are affected?" | `impact_of_change`, `callers_of`, `implementations_of` |
| **Code review** | "Does this diff touch code reached from the UI thread or from native callbacks?" "Which public APIs did it change?" | `diff_impact`, `entry_points_reaching` |
| **Testing** | "Which tests exercise this method?" "Which changed methods have no test reaching them?" | `tests_reaching`, `diff_impact` |
| **Onboarding / documentation** | "What are the entry points?" "How do the assemblies layer?" "What calls into native code?" | `entry_points`, `assembly_graph`, `path_to_native` |
| **Architecture health** | "Which assemblies violate the intended layering?" "What's dead code?" "Which classes are most depended on?" | `layer_violations`, `unreferenced_symbols`, `hotspots` |
| **Debugging / triage** | "Which method wrote this log line or threw this message?" "How can execution reach it?" "Where does it cross into native code?" | `find_log_source`, `find_exception_source`, `callers_of`, `path_to_native` |
| **Data and config flow** | "Who writes this field?" "Who reads this config key?" | `writers_of`, `readers_of`, `config_usage` |

Debugging is one consumer among several. No extractor or schema element is specific to it, apart from message templates, which also serve documentation and review.

**Non-goals (v1):**
- understanding reflection-based or string-based dispatch (documented gap; see Section 12);
- runtime tracing;
- semantic (embedding) search;
- UI beyond Neo4j Browser.

---

## 2. Architecture

```
                 ┌──────────────────────┐
  .sln / .csproj │ A. Roslyn extractor  │  (C#, net48, MSBuildWorkspace)
  ─────────────▶ │   types, methods,    │──┐
                 │   calls, logs, P/Inv │  │
                 └──────────────────────┘  │
                 ┌──────────────────────┐  │   CSV (neo4j-admin format)
  compile_cmds   │ B. Native extractor  │  │   + JSONL (incremental)
  ─────────────▶ │   (Python, libclang) │──┼──────────────┐
                 └──────────────────────┘  │              ▼
                 ┌──────────────────────┐  │     ┌──────────────────┐     ┌────────────┐
  built /clr DLLs│ C. Bridge extractor  │  │     │ D. Loader        │────▶│  Neo4j     │
  ─────────────▶ │   (C#, ILSpy libs)   │──┘     │ dedupe, import,  │     │ Community  │
                 └──────────────────────┘        │ derive edges     │     └─────┬──────┘
                                                 └──────────────────┘           │
                 ┌──────────────────────┐                                       │
  runtime log ──▶│ E. Log matcher (Py)  │◀───────────── templates ──────────────┤
                 └──────────────────────┘                                       │
                 ┌──────────────────────┐                                       │
  agent ◀──MCP──▶│ F. MCP server (Py)   │──────── fixed, read-only Cypher ──────┘
                 └──────────────────────┘
```

---

## 3. Repository layout

```
codegraph/
├── SPEC.md                          # this file
├── config/
│   └── codegraph.yaml               # all codebase-specific knobs (Section 13)
├── extractors/
│   ├── CodeGraph.Roslyn/            # A: C# console app, targets net48 (VERIFY, Section 5.1)
│   ├── CodeGraph.Bridge/            # C: C# console app, C++/CLI IL reader
│   ├── CodeGraph.Shared/            # shared: CSV writers, ID helpers, record types
│   └── native/                      # B: Python package, libclang walker
├── loader/                          # D: Python, dedupe + neo4j-admin + Cypher post-processing
├── logmatch/                        # E: Python, template → regex matcher
├── mcp_server/                      # F: Python, FastMCP server
├── fixtures/
│   └── MiniPlant/                   # synthetic test solution covering every edge type (Section 11)
├── tests/
├── out/                             # generated CSV/JSONL (git-ignored)
└── docs/
    ├── WALKTHROUGH.md
    └── DECISIONS.md
```

---

## 4. Graph schema

### 4.1 ID scheme (critical: IDs must be stable across runs)

| Kind | ID format | Source |
|---|---|---|
| Any managed symbol (C# or C++/CLI managed side) | `dn:` + documentation-comment ID, e.g. `dn:M:Acme.Stage.Move(System.Double)` | Roslyn `ISymbol.GetDocumentationCommentId()`; bridge uses ICSharpCode.Decompiler `IdStringProvider.GetIdString()` (must produce identical strings — VERIFY in Phase 3) |
| Native C++ symbol | `cpp:` + clang USR | `cursor.get_usr()` |
| Native DLL export | `exp:` + lower(dll) + `!` + entry point | DllImport data / dumpbin |
| Message (log or exception text) | `msg:` + sha1(template + "\|" + emitting method id) | extractor |
| Config key | `cfg:` + key | extractor |
| File | `file:` + repo-relative path (forward slashes, lowercase) | extractor |

The `dn:` prefix is shared deliberately. When C# calls into a C++/CLI managed class, Roslyn sees that class as a metadata symbol with the same documentation ID the bridge extractor emits, so the two sides join on the same node with no matching logic.

Symbols with no documentation ID (local functions, lambdas): do **not** create nodes. Attribute their calls to the containing member, with `via: "lambda"` or `via: "localfunc"` on the edge.

### 4.2 Node labels and properties

All nodes carry these properties:

| Property | Meaning |
|---|---|
| `id` | from Section 4.1 |
| `name` | short name |
| `fqn` | fully qualified name |
| `lang` | `cs`, `cpp`, `cppcli` or `ext` |
| `assembly` | assembly name, or native module name |
| `file` | repo-relative path |
| `lineStart`, `lineEnd` | source span |
| `commit` | git SHA indexed from |
| `seenRun` | ID of the index run that last emitted this node |

Label-specific properties:

| Label | Extra properties |
|---|---|
| `:Assembly` | `kind` (`csharp`, `cppcli`, `native`), `targetFramework`, `layer` (from config, Section 13), `isTestAssembly` |
| `:File` | `sha1`, `commitCount`, `lastCommitDate` (from `git log`; Section 5.7) |
| `:Type` | `kind` (class, struct, enum, interface, delegate, record), `accessibility`, `isAbstract`, `isStatic`, `attributes: string[]` |
| `:Method` | `signature`, `accessibility`, `isStatic`, `isVirtual`, `isAbstract`, `isOverride`, `isExtern`, `isCtor`, `isAccessor`, `attributes: string[]`, `isTest`, `isEntryPoint`, `entryKind` (Section 5.7), `isPublicApi` |
| `:Field` | `typeFqn`, `isStatic`, `isConst`, `constValue` (if const string/number) |
| `:Property` | `typeFqn`, `hasGetter`, `hasSetter` |
| `:Event` | `typeFqn` |
| `:NativeType` | `kind` (class, struct, union) |
| `:NativeFunction` | `mangledName`, `qualifiedName`, `isMethod`, `isVirtual`, `resolved` (false if only known from IL/export) |
| `:NativeExport` | `dll`, `entryPoint` |
| `:Message` | `kind` (`log` or `exception`), `level`, `template`, `literalChars` (int, count of non-placeholder characters), `regex` |
| `:ConfigKey` | `key`, `accessor` |
| `:External` | a referenced symbol outside the indexed code (BCL, NuGet); never traversed by tools |
| `:IndexRun` | `runId`, `commit`, `startedAt`, `finishedAt`, `toolVersions`, `counts` (JSON string), `warnings` (JSON string) |

### 4.3 Relationship types

Every relationship carries:
- `owner`: the assembly that emitted the edge, used for incremental replacement;
- `seenRun`;
- `file`, `line` where the relationship appears in source (or `ilOffset` for bridge edges);
- `confidence`: `exact` (default) or `heuristic`.

| Relationship | Meaning | Extracted by |
|---|---|---|
| `(:Assembly)-[:CONTAINS]->(:Type)` | type is defined in assembly | A, C |
| `(:Assembly)-[:REFERENCES]->(:Assembly)` | project/assembly reference | A |
| `(:Type)-[:DEFINED_IN]->(:File)` | one per partial declaration | A, B |
| `(:Type)-[:HAS_MEMBER]->(:Method\|:Field\|:Property\|:Event)` | membership | A, C |
| `(:Type)-[:INHERITS]->(:Type)` | base class | A, C |
| `(:Type)-[:IMPLEMENTS]->(:Type)` | interface implementation (target `kind=interface`) | A, C |
| `(:Method)-[:IMPLEMENTS_METHOD]->(:Method)` | concrete method → interface method it implements | A |
| `(:Method)-[:OVERRIDES]->(:Method)` | override → overridden | A |
| `(:Method)-[:DISPATCHES_TO]->(:Method)` | **derived**: declared (interface/virtual) → each concrete implementation. Computed by the loader, never extracted | D |
| `(:Method)-[:CALLS]->(:Method)` | direct call / object creation / accessor call; `kind` ∈ `call`, `ctor`, `getter`, `setter`; `via` ∈ `direct`, `lambda`, `localfunc` | A, B, C |
| `(:Method)-[:REFERENCES_METHOD]->(:Method)` | method group / delegate creation (indirect call) | A |
| `(:Method)-[:SUBSCRIBES]->(:Event)` | `+=` on an event; `handler` property holds the handler method ID | A |
| `(:Method)-[:READS]->(:Field\|:Property)` | read | A |
| `(:Method)-[:WRITES]->(:Field\|:Property)` | write | A |
| `(:Method)-[:EMITS]->(:Message)` | log call or `throw new X("...")`; edge has `exceptionType` for throws | A, B |
| `(:Method)-[:READS_CONFIG]->(:ConfigKey)` | configured config accessor | A |
| `(:Method)-[:PINVOKES]->(:NativeExport)` | `[DllImport]` extern method → export | A |
| `(:NativeExport)-[:RESOLVES_TO]->(:NativeFunction)` | export → native implementation | B / D |
| `(:Method)-[:CALLS_NATIVE]->(:NativeFunction)` | C++/CLI managed code → native function (IJW) | C |
| `(:Type)-[:WRAPS]->(:NativeType)` | C++/CLI ref class holds a pointer to a native class (field type analysis) | C |
| `(:NativeType)-[:HAS_MEMBER]->(:NativeFunction)` | membership | B |
| `(:NativeType)-[:INHERITS]->(:NativeType)` | base class | B |
| `(:NativeFunction)-[:OVERRIDES]->(:NativeFunction)` | virtual override | B |
| `(:NativeFunction)-[:CALLS]->(:NativeFunction)` | native call | B |
| `(any)-[:REFS_EXTERNAL]->(:External)` | call into BCL / NuGet; kept for context, not traversed | A |
| `(:File)-[:CO_CHANGES_WITH {count}]->(:File)` | the two files changed in the same commit at least `co_change_min` times (Section 5.7); `confidence: heuristic`, since it's historical evidence, not code structure | D |

### 4.4 Constraints and indexes (run after every full import)

```cypher
CREATE CONSTRAINT node_id_type     IF NOT EXISTS FOR (n:Type)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_method   IF NOT EXISTS FOR (n:Method)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_field    IF NOT EXISTS FOR (n:Field)          REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_prop     IF NOT EXISTS FOR (n:Property)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_event    IF NOT EXISTS FOR (n:Event)          REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_ntype    IF NOT EXISTS FOR (n:NativeType)     REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_nfunc    IF NOT EXISTS FOR (n:NativeFunction) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_export   IF NOT EXISTS FOR (n:NativeExport)   REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_msg      IF NOT EXISTS FOR (n:Message)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_cfg      IF NOT EXISTS FOR (n:ConfigKey)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_asm      IF NOT EXISTS FOR (n:Assembly)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_file     IF NOT EXISTS FOR (n:File)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_ext      IF NOT EXISTS FOR (n:External)       REQUIRE n.id IS UNIQUE;

CREATE INDEX method_name  IF NOT EXISTS FOR (n:Method)         ON (n.name);
CREATE INDEX type_name    IF NOT EXISTS FOR (n:Type)           ON (n.name);
CREATE INDEX nfunc_name   IF NOT EXISTS FOR (n:NativeFunction) ON (n.qualifiedName);
CREATE INDEX nfunc_mangle IF NOT EXISTS FOR (n:NativeFunction) ON (n.mangledName);
CREATE INDEX method_asm   IF NOT EXISTS FOR (n:Method)         ON (n.assembly);

CREATE FULLTEXT INDEX message_text IF NOT EXISTS FOR (n:Message) ON EACH [n.template];
CREATE FULLTEXT INDEX symbol_text  IF NOT EXISTS FOR (n:Method|Type|NativeFunction) ON EACH [n.name, n.fqn];
```

The full-text indexes are Lucene keyword indexes, not embeddings, so results are deterministic.

---

## 5. Component A — Roslyn extractor (C#)

### 5.1 Project setup

- Console app. **Start with target `net48`.** The .NET Framework build of `Microsoft.Build.Locator` is the one that finds Visual Studio and Build Tools installations; the .NET Core build only finds .NET SDK installations. Legacy (non-SDK) `.csproj` files generally need Visual Studio's MSBuild.
- `VERIFY` (Phase 0 spike): recent `Microsoft.CodeAnalysis.Workspaces.MSBuild` versions run project evaluation in an out-of-process build host. Check whether a `net8.0` extractor can load the legacy projects. If yes, prefer `net8.0` for speed; if no, stay on `net48`. Record the outcome in `DECISIONS.md`.
- NuGet packages:
  - `Microsoft.Build.Locator`
  - `Microsoft.CodeAnalysis.CSharp.Workspaces`
  - `Microsoft.CodeAnalysis.Workspaces.MSBuild`
  - `CsvHelper`
  - `YamlDotNet`
- Do **not** ship MSBuild assemblies in the output folder. Use `ExcludeAssets="runtime"` on `Microsoft.Build*` package references, per MSBuildLocator guidance.

Startup:

```csharp
// Pick the newest Visual Studio / Build Tools MSBuild so evaluation matches what Visual Studio sees.
var instance = MSBuildLocator.QueryVisualStudioInstances()
                             .OrderByDescending(i => i.Version)
                             .First();
MSBuildLocator.RegisterInstance(instance);
// IMPORTANT: no Microsoft.Build.* type may be touched in this method before RegisterInstance;
// put the workspace code in a separate method so the JIT doesn't load MSBuild early.
```

Loading:

- `MSBuildWorkspace.Create(properties)`, with properties from config: `Configuration`, `Platform`, `SolutionDir`.
- Subscribe to `workspace.WorkspaceFailed` and log every diagnostic to `out/load_report.json`.
- Open each solution listed in config. Projects shared between solutions are processed once, deduplicated by project file path.
- For each project, call `GetCompilationAsync()`.
- Record the count of `Error`-severity diagnostics per project. **Compile errors mean unresolved symbols, which means missing edges.** Report them prominently and fail the gate if any project has more than `max_compile_errors` (configurable, default 0).

### 5.2 What to extract — symbol pass

For every `INamedTypeSymbol` declared in source (walk `compilation.Assembly.GlobalNamespace` recursively; skip `IsImplicitlyDeclared`):

- emit a `:Type` node, plus one `DEFINED_IN` edge per `Locations` entry (partial classes have several);
- emit `INHERITS` for `BaseType` when it is not `System.Object`;
- emit `IMPLEMENTS` for each of `Interfaces` (direct interfaces only; inherited ones are reachable through the graph);
- emit `HAS_MEMBER` + a member node for each member:
  - `IMethodSymbol`: ordinary methods, constructors, operators, conversions, and property/event accessors (`isAccessor=true`);
  - `IFieldSymbol`, `IPropertySymbol`, `IEventSymbol`.
- **Interface mapping.** For each interface `I` in `type.AllInterfaces` and each member `m` of `I`:
  - call `type.FindImplementationForInterfaceMember(m)`;
  - if the implementation is declared in this type, emit `(impl)-[:IMPLEMENTS_METHOD]->(m)`;
  - this catches explicit implementations and implementations inherited from base classes.
- `OVERRIDES` for `method.OverriddenMethod`, using `OriginalDefinition`.
- `PINVOKES`: if `method.GetDllImportData()` is non-null, emit:
  - a `:NativeExport` node with `id = exp:<dll lower>!<EntryPointName ?? method.Name>`;
  - the edge `(method)-[:PINVOKES]->(export)`.
- `attributes`: `AttributeClass.ToDisplayString()` for each attribute, as a string array.

### 5.3 What to extract — body pass (operations, not syntax)

For each method, accessor, constructor and field/property initializer:

1. Get the body syntax and call `semanticModel.GetOperation(bodyNode)`.
2. Walk `operation.Descendants()`.
3. Map each operation as below. **Always normalize the target symbol** with `.OriginalDefinition`, and for reduced extension methods use `.ReducedFrom ?? symbol`. This makes generic instantiations and extension calls point at the declared method.

| Operation | Edge emitted |
|---|---|
| `IInvocationOperation` | `CALLS {kind: call}` → `TargetMethod`. If `TargetMethod` is virtual/abstract/interface, still point at the declared method; dispatch is handled by derived `DISPATCHES_TO` edges. |
| `IObjectCreationOperation` | `CALLS {kind: ctor}` → `Constructor` |
| `IPropertyReferenceOperation` | `CALLS {kind: getter\|setter}` → accessor (`setter` if the parent is `ISimpleAssignmentOperation` / `ICompoundAssignmentOperation` with this as `Target`; both for compound assignment), plus `READS` / `WRITES` → property |
| `IFieldReferenceOperation` | `READS` or `WRITES` → field (same assignment-target rule) |
| `IEventAssignmentOperation` | `SUBSCRIBES` → event, with `handler` = method referenced on the right side, if resolvable |
| `IMethodReferenceOperation` | `REFERENCES_METHOD` → method (covers `Task.Run(Foo)`, delegate fields, event handlers) |
| `IAnonymousFunctionOperation` / `ILocalFunctionOperation` | recurse into the body; tag emitted edges `via: lambda\|localfunc`; source stays the containing member |
| `IThrowOperation` whose `Exception` is an `IObjectCreationOperation` | `EMITS {exceptionType}` → `:Message {kind: exception}` if the first string argument yields a template (Section 5.4) |
| Invocation matching a configured **log method** | `EMITS` → `:Message {kind: log, level}` (Section 5.4) |
| Invocation / indexer matching a configured **config accessor** with a constant string key | `READS_CONFIG` → `:ConfigKey` |

Targets outside the indexed solutions (BCL, NuGet) become `:External` nodes linked by `REFS_EXTERNAL`. **Exception:** targets in a C++/CLI assembly listed in config are emitted as plain `dn:` IDs with no node of their own. The bridge extractor (C) supplies those nodes, and the loader fails loudly if any are missing.

Record `file` and `line` from `operation.Syntax.GetLocation().GetLineSpan()` on every edge.

### 5.4 Message template extraction (log and exception text)

Log method matching is configuration-driven (Section 13). A rule says: "invocations of method `Error` on any type implementing `log4net.ILog` are level=error; the template is argument 0."

To turn the template argument into a template string:

| Argument operation | Template |
|---|---|
| `ILiteralOperation` (string) | the literal |
| `IFieldReferenceOperation` to a `const string` | `ConstantValue` |
| `IInterpolatedStringOperation` | text parts verbatim; each interpolation hole becomes `{N}` in order |
| `IBinaryOperation` (`+`) chains | concatenate: constant parts verbatim; non-constant parts become `{N}` |
| `string.Format(fmt, …)` invocation | recurse into `fmt` |
| anything else | template `<dynamic>`; still emit the edge with `literalChars = 0` |

Store:
- `template`;
- `literalChars` = number of non-placeholder characters;
- `regex` = the template turned into a regex (Section 9).

Message ID = `msg:` + sha1(template + "|" + emitting method ID). The same text logged in two places gives two nodes, which is intentional: the method is what we want to identify.

### 5.5 Output

- One CSV per label and one per relationship type, in `out/csharp/`, using neo4j-admin header syntax:
  - node files: `id:ID,:LABEL,name,fqn,…,lineStart:int,lineEnd:int,isStatic:boolean,attributes:string[],commit,seenRun`;
  - relationship files: `:START_ID,:END_ID,:TYPE,owner,file,line:int,kind,via,confidence,seenRun`.
- The same records as JSONL in `out/csharp/jsonl/`, for incremental loads.
- `out/csharp/manifest.json` with counts per label and per type, projects loaded, diagnostics, duration, Roslyn version, MSBuild instance path.
- CLI:

  ```
  CodeGraph.Roslyn.exe --config config/codegraph.yaml --out out/csharp [--project <name>]... [--run-id <id>]
  ```

  `--project` limits extraction to the named projects (used for incremental runs).

### 5.6 Performance

- Process documents within a project in parallel (`Parallel.ForEachAsync`, max degree configurable).
- Write CSV through a single-writer channel so files aren't corrupted.
- `VERIFY`: peak memory on the full solution. If it exceeds about 75% of RAM, process solutions one at a time and release each workspace before the next.

### 5.7 Classification pass (general-purpose properties)

These properties are what make the graph useful beyond debugging. All rules are configuration-driven (Section 13) and deterministic.

| Property | Rule |
|---|---|
| `Method.isTest` | Method has a configured test attribute: `NUnit.Framework.TestAttribute`, `TestCaseAttribute`, `Microsoft.VisualStudio.TestTools.UnitTesting.TestMethodAttribute`, `Xunit.FactAttribute`, `Xunit.TheoryAttribute`, plus any in `test_attributes` |
| `Assembly.isTestAssembly` | Assembly references a test framework, or matches `test_assembly_patterns` |
| `Method.isEntryPoint`, `entryKind` | `main` (`compilation.GetEntryPoint()`); `ui_handler` (method referenced by `SUBSCRIBES.handler` on events of types deriving from configured UI base types, or XAML handler in v2); `remote` (types/methods with configured service attributes, e.g. WCF `OperationContractAttribute`); `native_callback` (managed methods referenced by delegates passed to `PINVOKES`/`CALLS_NATIVE` targets); `scheduled` / `plugin` (from `entry_point_attributes` in config) |
| `Method.isPublicApi` | `DeclaredAccessibility == Public` on the method **and** all containing types, in a non-test assembly |
| `Assembly.layer` | Matched from `layers` in config by assembly name pattern |
| `File.commitCount`, `File.lastCommitDate` | `git log --format=%H%x09%cI --name-only` over the configured history window (default 2 years); counted per file. **Do not store author names** (privacy); add them only if the team explicitly asks |
| `CO_CHANGES_WITH` | From the same `git log` pass: for commits touching ≤ `co_change_max_files` files (default 30, which skips mass refactors), count file pairs; emit edges with `count ≥ co_change_min` (default 5) |

The git-history pass lives in the loader (component D) as `loader/git_history.py`, because it is language-independent.

---

## 6. Component B — Native C++ extractor (Python + libclang)

### 6.1 Compilation database

MSBuild does not produce `compile_commands.json` natively. Options, in order of preference (`VERIFY` which works on this codebase):

1. `microsoft/msbuild-extractor-sample` (pip wrapper `py-msbuild-extractor`). Reads `.vcxproj`/`.sln` directly; needs the "Desktop development with C++" workload.
2. An MSBuild logger in the `MsBuildCompileCommandsJson` family (e.g. `UnitTestBot/msbuild-database`):

   ```
   msbuild Solution.sln /t:Rebuild -logger:path\to\logger.dll
   ```

3. Parse an MSBuild detailed log with `ms2cc`.

Commit the chosen approach as a script: `extractors/native/make_compdb.ps1`.

### 6.2 Split native vs. C++/CLI translation units

- For each entry in the compilation database: if the command contains `/clr` (any variant), **skip it** and record it in `out/native/clr_files.json`. Clang cannot parse C++/CLI; those files are covered by component C.
- `VERIFY` count: the list should match the known ~1% C++/CLI code.

### 6.3 Parsing

- Package `libclang` (pip), which bundles the shared library.
- Parse with clang-cl driver semantics: prepend `--driver-mode=cl` to the arguments from the compilation database, and keep `/I`, `/D`, `/std:` flags. Add `-fms-compatibility -fms-extensions -fdelayed-template-parsing` if `VERIFY` shows MSVC-specific headers failing.
- Record diagnostics with severity ≥ error per translation unit into `out/native/diagnostics.json`. Many errors in one file means missing include paths; fix the compilation database before trusting edges from it.
- Walk only cursors whose `location.file` is inside the repo (skip system and SDK headers). Headers inside the repo are visited from many translation units; deduplicate by USR, keeping the first definition (`cursor.is_definition()` wins over a declaration).

| Cursor kind | Output |
|---|---|
| `CLASS_DECL`, `STRUCT_DECL`, `CLASS_TEMPLATE` (definitions) | `:NativeType`; `INHERITS` from `CXX_BASE_SPECIFIER` children (`.referenced`) |
| `FUNCTION_DECL`, `CXX_METHOD`, `CONSTRUCTOR`, `DESTRUCTOR`, `FUNCTION_TEMPLATE` | `:NativeFunction {mangledName: cursor.mangled_name, qualifiedName, isVirtual: cursor.is_virtual_method()}`; `HAS_MEMBER` from `semantic_parent` |
| `CALL_EXPR` inside a function body | `CALLS` → `cursor.referenced` (skip if `None`, count skips in the manifest) |
| Overrides | `cursor.get_overridden_cursors()` if available in the installed binding; otherwise `VERIFY` and fall back to matching virtual methods by signature through the base chain, marked `confidence: heuristic` |
| Calls to configured native log functions/macros (after macro expansion the `CALL_EXPR` targets the underlying function) | `EMITS` → `:Message`, with the template from the first `STRING_LITERAL` argument (printf-style placeholders kept verbatim) |

- `VERIFY`: in clang-cl mode, `mangled_name` should be MSVC-style (`?Grab@Camera@@QEAAXXZ`). This is what lets component C and the DLL exports join to these nodes.

### 6.4 Exports

For each native DLL built by the solution (paths from config):

- run `dumpbin /exports <dll>` (from the Visual Studio developer environment) and parse the name table;
- emit `:NativeExport {id: exp:<dll>!<name>}`;
- emit `(:NativeExport)-[:RESOLVES_TO]->(:NativeFunction)`:
  - first by exact `mangledName`;
  - then, for `extern "C"` exports, by `name` where the function has C linkage.

### 6.5 Output and CLI

- Same CSV/JSONL format as Section 5.5, written to `out/native/`.
- Use `multiprocessing` across translation units.
- Cache results per translation unit, keyed by sha1(source + args), in `out/native/cache/`, so unchanged files are skipped.

```
python -m codegraph_native --config config/codegraph.yaml --compdb compile_commands.json --out out/native
```

---

## 7. Component C — C++/CLI bridge extractor (C#)

This is the most valuable component for understanding behaviour across the managed/native boundary (debugging, impact analysis, onboarding). It is also the least standard, so **do the Phase 3 spike before writing the real code.**

### 7.1 Why this approach

- Clang cannot parse `/clr` code, and Roslyn cannot parse C++.
- A built C++/CLI assembly is a mixed-mode assembly: it contains normal .NET metadata and IL for managed code.
- When managed code calls a native function, the compiler emits a P/Invoke-style (`pinvokeimpl`) method definition for that native function in the IL. Reading that IL therefore gives exact managed → native call edges without parsing C++/CLI source.

### 7.2 Libraries

- `ICSharpCode.Decompiler` (the engine behind ILSpy, MIT licence):
  - `PEFile` / `MetadataFile` to open the DLL;
  - `DecompilerTypeSystem` to resolve handles to `IMethod` / `ITypeDefinition`;
  - `IdStringProvider.GetIdString(entity)` to produce documentation-comment IDs.
- `System.Reflection.Metadata`, to read method bodies and decode IL opcodes.

### 7.3 Steps

1. **Spike (`VERIFY`), before any real code:**
   - Open one wrapper DLL in ILSpy.
   - Find a managed method that calls native code, and write down exactly how the native target appears: the `<Module>` method name, `PinvokeImpl` flags, `ImplMap` import name, and whether the name is MSVC-mangled or a readable `Class.Method`.
   - Pick 20 managed symbols that C# code uses from this DLL. Check that `IdStringProvider` gives exactly the same ID strings as Roslyn's `GetDocumentationCommentId()` for those symbols from the C# side.
   - Record everything in `DECISIONS.md`. **Do not proceed until both checks pass, or an alternative is agreed.**
2. **Managed symbols:** for every type and member, emit nodes with `dn:` IDs and `lang: cppcli`, plus `HAS_MEMBER`, `INHERITS` and `IMPLEMENTS`. Source file and line are unknown from metadata; set `file` to the C++/CLI project folder and `lineStart = -1`. Optionally fill them in step 6.
3. **IL scan:** for every method body, decode opcodes (`call`, `callvirt`, `newobj`, `ldftn`, `ldvirtftn`) and resolve the token:
   - Target is a normal managed method → `CALLS` with a `dn:` ID (C++/CLI → C# calls are covered too).
   - Target is a method with `MethodAttributes.PinvokeImpl` → **native call**. Get the import name from `MethodDefinition.GetImport()`, or the method name if there is none. Emit `(:Method)-[:CALLS_NATIVE {ilOffset}]->(:NativeFunction)`, where the target ID is resolved in step 4.
4. **Native name matching:**
   - If the name is MSVC-mangled: match `NativeFunction.mangledName` exactly → `confidence: exact`.
   - Otherwise demangle (`llvm-undname`, or `UnDecorateSymbolName` from DbgHelp) or use the readable name, and match `NativeFunction.qualifiedName` → `confidence: exact` if exactly one candidate, `heuristic` if overloads tie.
   - No match: create `:NativeFunction {id: cpp:unresolved:<name>, resolved: false}` so the edge still exists.
   - **Report the match rate.** Gate: at least 90%, or an explained list of what's unmatched.
5. **WRAPS:** for each managed type with a field whose type is a pointer to a native value type (IJW represents native classes as opaque value types in metadata), emit `(:Type)-[:WRAPS]->(:NativeType)`, matched by name, `confidence: heuristic` unless the name match is unique.
6. **Optional source locations:** search the C++/CLI project's `.cpp`/`.h` files for `ClassName::MethodName(` to fill `file`/`line`, marked `locationConfidence: heuristic`.

Output goes to `out/bridge/` in the same CSV/JSONL format.

```
CodeGraph.Bridge.exe --config config/codegraph.yaml --out out/bridge
```

---

## 8. Component D — Loader (Python)

Packages: `neo4j` (official driver), `pandas` or `pyarrow` for CSV merging, `pyyaml`.

### 8.1 Merge and dedupe (before import)

- Concatenate node files by label from `out/csharp`, `out/native` and `out/bridge`.
- For duplicate IDs, keep the record with the highest priority: source-defined > bridge > external. Log every merge in which properties conflict.
- Validate references: every `:START_ID` / `:END_ID` must exist in the node set. Unknown `dn:` IDs that should be supplied by the bridge are an **error**; others become `:External` stubs.
- Write the final files to `out/import/`.

### 8.2 Full import (Community Edition)

Community Edition has a single user database (`neo4j`), and full import requires that the database does not already exist. So:

1. `neo4j stop`
2. Run:

   ```
   neo4j-admin database import full neo4j --overwrite-destination --id-type=string \
     --array-delimiter=";" --nodes=... --relationships=...
   ```

   `VERIFY`: the exact flag names against the installed Neo4j version (`neo4j-admin database import full --help`).
3. `neo4j start`
4. Run the Section 4.4 constraints and indexes.
5. Derive dispatch edges:

   ```cypher
   MATCH (impl:Method)-[:IMPLEMENTS_METHOD|OVERRIDES]->(decl:Method)
   MERGE (decl)-[d:DISPATCHES_TO]->(impl)
   SET d.owner = impl.assembly, d.confidence = 'exact';

   // transitive overrides: an interface method also dispatches to overrides of its implementations
   MATCH (decl:Method)-[:DISPATCHES_TO]->(:Method)<-[:OVERRIDES*1..5]-(deeper:Method)
   MERGE (decl)-[d:DISPATCHES_TO]->(deeper)
   SET d.owner = deeper.assembly, d.confidence = 'exact';
   ```

   Do the same for `NativeFunction` `OVERRIDES`.
6. Write the `:IndexRun` node.

### 8.3 Incremental update (per changed assembly)

The goal is to keep edges coming from unchanged assemblies, which point at stable IDs.

1. Determine changed projects with `git diff --name-only <lastIndexedCommit>..HEAD`, mapped to projects by path.
2. Re-run the extractors with `--project` for only those projects.
3. For each changed assembly `a`, in batches (`CALL { … } IN TRANSACTIONS OF 10000 ROWS`):
   - delete relationships with `r.owner = a`;
   - `UNWIND $rows MERGE` the nodes, setting properties and `seenRun = $run`;
   - `UNWIND $rows MATCH … MERGE` the relationships;
   - delete nodes with `assembly = a AND seenRun <> $run`. These are nodes that disappeared; `DETACH DELETE` them.
4. Recompute `DISPATCHES_TO` for the affected methods (delete those owned by `a`, then re-derive).
5. Update `:IndexRun`.

`VERIFY` with a test: change one method in the fixture, run incremental, and diff the graph against a full rebuild. They must be identical.

---

## 9. Component E — Log matcher (Python)

Input: a raw runtime log line, or a fragment of one. Output: candidate `:Message` nodes, ranked.

1. Template → regex, built once at index time and stored in `Message.regex`:
   - escape literal text;
   - `{0}`, `{name}`, `{0:F2}` → `(.+?)`;
   - printf `%s`, `%d`, `%.2f`, `%x` etc. → `(.+?)`;
   - `<dynamic>` → no regex.
2. Pre-filter: extract the 3 longest alphabetic tokens from the log line and query the `message_text` full-text index (top 200).
3. Test each candidate's regex against the log line (search, not full match, because log prefixes like timestamps and thread IDs vary).
4. Rank deterministically: matched first, then by `literalChars` descending, then by ID. Return the top N with `file`, `line`, emitting method and level.
5. Make the timestamp/prefix stripper configurable: `log_prefix_regex` in config.

---

## 10. Component F — MCP server (Python)

- Package: the official MCP Python SDK (`mcp`, using `FastMCP`), or the standalone `fastmcp` package. Use stdio transport for Claude Code.
- **Never print to stdout** in a stdio server; it corrupts the protocol. Log to stderr or a file.
- Connect as a **read-only** Neo4j user. Community has limited role management, so additionally run every query inside `session.execute_read` with a timeout.
- **No free-form Cypher tool.** Every tool is a fixed, parameterized query. Clamp `max_depth` and `limit` server-side.
- Every result includes `file:line` and `confidence`.
- Every result ends with `index_commit`. If the repo's current `HEAD` differs, add `stale: true`.

Tools are grouped by purpose. All of them are fixed, read-only queries over the same graph.

**Navigation and understanding**

| Tool | Parameters | Returns |
|---|---|---|
| `find_symbol` | `name: str`, `kind?: str` | ranked symbols (exact name match first, then full-text index) |
| `outline` | `target` (file path, type ID, or assembly name) | members with signatures, base types, interfaces, `file:line`; no method bodies |
| `dependencies_of` | `symbol_id`, `level` (`type` or `assembly`) | what it uses: types/assemblies reached through `CALLS`, `READS`, `WRITES`, `INHERITS`, `IMPLEMENTS` (depth 1), grouped by target |
| `callers_of` | `symbol_id`, `max_depth=4` (≤8), `limit=100` | caller paths over `CALLS\|DISPATCHES_TO\|REFERENCES_METHOD\|SUBSCRIBES` (reverse direction) |
| `callees_of` | `symbol_id`, `max_depth=3` (≤6) | callee tree, excluding `:External` |
| `implementations_of` | `symbol_id` | `DISPATCHES_TO` targets |
| `path_to_native` | `symbol_id`, `max_depth=6` | paths ending at `NativeFunction` through `CALLS\|DISPATCHES_TO\|PINVOKES\|RESOLVES_TO\|CALLS_NATIVE` |
| `entry_points` | `kind?` (`main`, `ui_handler`, `remote`, `native_callback`, `scheduled`, `plugin`) | entry-point methods with `file:line` |
| `entry_points_reaching` | `symbol_id`, `max_depth=8` | which entry points can reach this symbol, grouped by `entryKind` |
| `assembly_graph` | `layer?` | assembly → assembly `REFERENCES` edges with layer labels |

**Change planning, review and testing**

| Tool | Parameters | Returns |
|---|---|---|
| `impact_of_change` | `symbol_id`, `change_kind` (`signature`, `behaviour`, `removal`), `max_depth=4` | `signature`/`removal`: direct callers, overriders, implementers and the assemblies they belong to (must change). `behaviour`: transitive callers up to depth (may be affected), plus affected entry points and tests |
| `diff_impact` | `base_ref`, `head_ref` (git refs) | maps the git diff's changed line ranges to methods (by `file` + `lineStart..lineEnd`). For each changed method: public-API flag, entry points reaching it, tests reaching it; plus a list of changed methods reached by **no** test |
| `tests_reaching` | `symbol_id`, `max_depth=8` | test methods (`isTest`) with a call path to the symbol, sorted by path length |
| `co_changed_files` | `file` | files with `CO_CHANGES_WITH` edges, by count (historical, `confidence: heuristic`) |

**Architecture health**

| Tool | Parameters | Returns |
|---|---|---|
| `layer_violations` | — | `REFERENCES` / `CALLS` edges going from a lower layer to a higher one, per the `layers` config |
| `unreferenced_symbols` | `assembly?`, `kind?` | non-public, non-entry-point, non-test methods/types with no incoming `CALLS`, `REFERENCES_METHOD`, `DISPATCHES_TO`, `SUBSCRIBES`, or attribute-based use. Labelled "candidates", because reflection may still use them (Section 12) |
| `hotspots` | `metric` (`fan_in`, `fan_out`, `churn`), `limit=50` | most-called, most-calling, or most-changed symbols/files |

**Debugging, data and config flow**

| Tool | Parameters | Returns |
|---|---|---|
| `find_log_source` | `log_line: str`, `limit=10` | Message candidates with emitting method, file, line, level (uses component E) |
| `find_exception_source` | `message: str`, `exception_type?: str` | as above, for `kind=exception` |
| `writers_of` / `readers_of` | `symbol_id` (field or property) | methods with `WRITES` / `READS` |
| `config_usage` | `key` | methods with `READS_CONFIG` |

**Housekeeping**

| Tool | Parameters | Returns |
|---|---|---|
| `index_status` | — | last `:IndexRun`, staleness, counts, warnings |

Example query (`callers_of`):

```cypher
MATCH (target {id: $id})
MATCH path = (caller)-[:CALLS|DISPATCHES_TO|REFERENCES_METHOD*1..$depth]->(target)
WHERE NOT caller:External
RETURN path
LIMIT $limit
```

`VERIFY`: Cypher does not accept a parameter as the variable-length bound. Build the bound into the query string from the server-side clamped integer, never from raw user input.

Claude Code registration (document in `README.md`):

```
claude mcp add codegraph -- python -m mcp_server --config config/codegraph.yaml
```

---

## 11. Testing

### 11.1 Fixture solution `fixtures/MiniPlant/` (build this first, in Phase 1)

A small solution with known answers. It must contain at least:

- interface `IStage` with two implementations, and a call through the interface;
- abstract base class with a virtual method, an override, and an override of the override;
- a generic method called with two type arguments; an extension method;
- a lambda passed to `Task.Run` and a local function, each containing a call;
- an event with a `+=` subscription;
- a partial class split across two files;
- log calls covering: literal; interpolated string; `string.Format`; concatenation; `const` field;
- `throw new InvalidOperationException("...")`;
- a config read through the configured accessor;
- a `[DllImport]` into a small native DLL project;
- a native C++ project with a class hierarchy, a virtual override, and a printf-style log;
- a C++/CLI project with a `ref class` wrapping the native class, called from C#;
- a test project (NUnit or MSTest, whichever the real codebase uses) with tests that reach some methods and deliberately not others;
- a `Main` entry point, a UI-style event handler, and a native callback delegate;
- three assemblies assigned to layers in config, with one deliberate layer violation;
- one unreferenced private method (expected in `unreferenced_symbols`) and one used only through reflection (expected there too, as a known false positive).

`tests/expected_edges.yaml` lists every edge the extractors must produce for the fixture. The test fails on any **missing** edge and on any **unexpected** edge to a non-`:External` node.

### 11.2 Accuracy check on the real codebase

- Pick 50 random methods. For each, compare graph callers (depth 1, including `DISPATCHES_TO`) against Visual Studio's "Find All References".
- Report precision and recall in `docs/ACCURACY.md`. **Gate: recall ≥ 98% for C#.** Investigate every miss.

### 11.3 Task benchmark (the proof for management)

The benchmark covers several kinds of work, so the result shows general value, not just debugging value. Build `bench/tasks.yaml` with 30–40 tasks, each with a known correct answer agreed with a senior engineer who knows the code:

| Category | # tasks | Task example | Correct answer recorded |
|---|---|---|---|
| Orientation | 6–8 | "Where is the stage-calibration logic, and what are its entry points?" | files/methods |
| Impact analysis | 6–8 | "List every method that must change if `X.Y(int)` gains a parameter." | exact method set |
| Code review | 4–6 | "For past PR #N, which public APIs and entry points does it affect, and which changed methods are untested?" | sets |
| Testing | 4–6 | "Which tests should run for this change?" | test set |
| Debugging | 8–10 | Already-resolved bugs (the existing frozen incident set): "Find the root cause of this failure from these logs." | root-cause file(s) |
| Architecture | 2–4 | "Which assemblies depend on the UI layer from below?" | edge set |

Protocol:

- Run each task twice: (a) grep/read tools only; (b) the same plus the codegraph MCP tools. Same model, same prompt, same budget.
- Record per run:
  - answer quality: precision and recall against the recorded set, or found-yes/no plus rank for single-answer tasks;
  - number of tool calls;
  - tokens;
  - wall-clock time.
- Report results **per category**, not just overall. A category where the graph doesn't help is useful information too.
- Write the results to `docs/BENCHMARK.md`, with every transcript archived.

---

## 12. Known gaps (document them; don't hide them)

| Gap | v1 handling | Possible v2 |
|---|---|---|
| Reflection (`Type.GetType("...")`, `Activator.CreateInstance`) | Not resolved. Count string literals that look like type names and report them | Config-driven rules mapping literals to types |
| DI container registrations | Not resolved unless configured | Extract registration calls (`Register<IFoo, Foo>()`) as `DISPATCHES_TO {confidence: heuristic}` |
| MEF `[Export]`/`[Import]` | Attributes are stored | Pair exports and imports by contract type |
| `dynamic`, COM late binding | Not resolved | — |
| XAML bindings / event handlers in `.xaml` | Not resolved | Parse XAML for `Click="Handler"` |
| Native function pointers / virtual calls through base pointers | Static target only; virtual dispatch through derived `DISPATCHES_TO` | — |
| Dead-code results | `unreferenced_symbols` returns candidates only; anything used via reflection, DI, XAML or serialization appears as a false positive | Feed DI/MEF/XAML extraction (above) into the incoming-reference check |
| Git co-change | Historical correlation, not a code dependency; always `confidence: heuristic` | — |
| C++/CLI source line numbers | Heuristic search (Section 7.3, step 6) | Read Windows PDBs via DIA SDK |

---

## 13. Configuration (`config/codegraph.yaml`)

```yaml
repo_root: "D:/src/myproduct"
solutions:
  - "D:/src/myproduct/Main.sln"
msbuild_properties:
  Configuration: "Release"
  Platform: "x64"
max_compile_errors: 0

cppcli_assemblies:            # managed DLLs built from /clr projects (component C input)
  - "D:/src/myproduct/bin/x64/Release/StageInterop.dll"
native_dlls:                  # for dumpbin exports
  - "D:/src/myproduct/bin/x64/Release/StageNative.dll"

log_methods:                  # ASK the human for the real list
  - { type_implements: "log4net.ILog", methods: ["Debug","Info","Warn","Error","Fatal"], template_arg: 0, level_from: "method" }
  - { type_implements: "log4net.ILog", methods: ["DebugFormat","InfoFormat","WarnFormat","ErrorFormat","FatalFormat"], template_arg: 0, level_from: "method" }
native_log_functions:
  - { name: "LogMessage", template_arg: 1, level_arg: 0 }

config_accessors:
  - { member: "System.Configuration.ConfigurationManager.AppSettings", kind: "indexer" }

test_attributes: []              # extra test attributes beyond NUnit / MSTest / xUnit defaults
test_assembly_patterns: ["*.Tests", "*.UnitTests", "*.IntegrationTests"]
entry_point_attributes:          # ASK the human: plugin, command, scheduler attributes used in the codebase
  - { attribute: "System.ServiceModel.OperationContractAttribute", kind: "remote" }
ui_event_base_types: ["System.Windows.Forms.Control", "System.Windows.UIElement"]

layers:                          # lower index = lower layer; a lower layer must not depend on a higher one. ASK the human for the intended layering
  - { name: "native_interop", assemblies: ["*.Interop", "*.Native*"] }
  - { name: "core",           assemblies: ["*.Core", "*.Common"] }
  - { name: "services",       assemblies: ["*.Services*"] }
  - { name: "ui",             assemblies: ["*.UI*", "*.App"] }

git_history:
  window_days: 730
  co_change_min: 5
  co_change_max_files: 30

log_prefix_regex: '^\d{4}-\d{2}-\d{2}[ T][\d:.,]+\s+\[\w+\]\s+'

neo4j:
  uri: "bolt://localhost:7687"
  user_writer: "neo4j"
  user_reader: "codegraph_ro"
  database: "neo4j"

limits:
  max_depth: 8
  max_results: 200
  query_timeout_s: 15
```

---

## 14. Phases and verification gates

| Phase | Build | Gate (stop, report, wait for approval) |
|---|---|---|
| **0. Spikes** | MSBuild load test (net48 vs net8, Section 5.1); compilation database generation (6.1); C++/CLI ILSpy inspection + ID equality check (7.3, step 1) | `DECISIONS.md` filled in; every project loads, or a list of failures with causes |
| **1. Fixture + C# symbols** | `fixtures/MiniPlant`; extractor A symbol pass (5.2); CSV writer; loader full import (8.1–8.2) | Fixture symbol and hierarchy edges match the expected list; real codebase loads into Neo4j; counts reported |
| **2. C# bodies + messages + classification** | Body pass (5.3), message templates (5.4), classification pass (5.7) including git history, derived `DISPATCHES_TO` | All fixture C# edges and classifications pass; real-codebase accuracy check (11.2) recall ≥ 98% |
| **3. Bridge** | Component C | Native name match rate ≥ 90%; fixture C++/CLI edges pass |
| **4. Native** | Component B, exports, `RESOLVES_TO` | Fixture native edges pass; diagnostics report shows < 5% of translation units with errors |
| **5a. MCP: navigation + debugging tools** | Component E; Section 10 navigation, debugging and data-flow tools; registered in Claude Code | Each tool demoed on 3 real symbols / log lines; outputs include `file:line` and staleness |
| **5b. MCP: change, review, test and architecture tools** | `impact_of_change`, `diff_impact`, `tests_reaching`, `co_changed_files`, `layer_violations`, `unreferenced_symbols`, `hotspots` | `diff_impact` run on 3 real past PRs and checked by a reviewer; fixture expectations pass |
| **6. Incremental** | Section 8.3 | Incremental result equals full rebuild on the fixture; timing on a real one-project change |
| **7. Benchmark** | Section 11.3 (all categories) | `BENCHMARK.md` with per-category A/B results, ready to show management |

---

## 15. Definition of done (v1)

- One command does a full rebuild: `scripts/full_index.ps1` (extract all → merge → import → derive → verify counts).
- One command does an incremental update: `scripts/incremental_index.ps1`.
- The MCP server is registered and documented; all tools return `file:line`, confidence and staleness.
- `WALKTHROUGH.md` explains every file; `DECISIONS.md` records every `VERIFY`; `ACCURACY.md` and `BENCHMARK.md` are filled in.
- No source code or graph data leaves the machine.
