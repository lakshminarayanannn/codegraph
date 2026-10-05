# Graph schema

Generated from `codegraph_common/schema.py` (the loader builds the neo4j-admin CSV headers and the defaults from
the same tables). Every node also has the label `:Node`; `:Node(id)` is unique and indexed.

## IDs (SPEC 4.1)

| Kind | Format | Example |
|---|---|---|
| Managed symbol (C#, C++/CLI) | `dn:` + documentation-comment ID | `dn:M:MiniPlant.Core.StageBase.Move(System.Double)` |
| C++/CLI global function | `dn:M:<Module:Assembly>` + rest of the ID | `dn:M:<Module:Wrap>.Helper(System.String)` |
| Native symbol | `cpp:` + clang USR | `cpp:c:@N@MiniPlant@N@Native@S@NativeStage@F@Move#d#` |
| Native symbol the bridge could not match | `cpp:unresolved:` + metadata name | `cpp:unresolved:Ns.Cls.Fn` |
| DLL export | `exp:` + lower-case DLL + `!` + entry point | `exp:miniplant.native.dll!MiniPlant_Stage_Move` |
| Message | `msg:` + sha1(template + `\|` + emitter id) | `msg:6e79af6d...` |
| Config key | `cfg:` + key | `cfg:Stage.MaxSpeed` |
| File | `file:` + repo-relative path, lower case | `file:src/miniplant.core/stagebase.cs` |
| Assembly / native module | `asm:` + name | `asm:MiniPlant.Core` |
| Index run | `run:` + run id | `run:20261004-035001` |

## Node properties

Common to every label: `id` (string), `name` (string), `fqn` (string), `lang` (string), `assembly` (string), `file` (string), `fileKey` (string), `lineStart` (int), `lineEnd` (int), `commit` (string), `seenRun` (string).
`fileKey` is the lower-case `file` (indexed for Method and NativeFunction; used by `diff_impact`).

| Label | Properties (type) | Defaults when absent |
|---|---|---|
| `:Assembly` | `kind` (string), `targetFramework` (string), `layer` (string), `layerIndex` (int), `isTestAssembly` (boolean) | `isTestAssembly`=false |
| `:File` | `path` (string), `sha1` (string), `commitCount` (int), `lastCommitDate` (string), `generated` (boolean) | `generated`=false |
| `:Type` | `typeId` (string), `accessibility` (string), `attributes` (string[]), `isStatic` (boolean), `kind` (string), `isAbstract` (boolean), `isPublicApi` (boolean), `locationConfidence` (string) | `isStatic`=false, `isAbstract`=false, `isPublicApi`=false |
| `:Method` | `typeId` (string), `accessibility` (string), `attributes` (string[]), `isStatic` (boolean), `signature` (string), `isVirtual` (boolean), `isAbstract` (boolean), `isOverride` (boolean), `isExtern` (boolean), `isCtor` (boolean), `isAccessor` (boolean), `isImplicit` (boolean), `associatedId` (string), `isTest` (boolean), `isEntryPoint` (boolean), `entryKind` (string), `entrySource` (string), `entryConfidence` (string), `isPublicApi` (boolean), `delegateParamTypes` (string[]), `indirectNativeCalls` (int), `locationConfidence` (string) | `isStatic`=false, `isVirtual`=false, `isAbstract`=false, `isOverride`=false, `isExtern`=false, `isCtor`=false, `isAccessor`=false, `isImplicit`=false, `isTest`=false, `isEntryPoint`=false, `isPublicApi`=false |
| `:Field` | `typeId` (string), `accessibility` (string), `attributes` (string[]), `isStatic` (boolean), `typeFqn` (string), `isConst` (boolean), `constValue` (string) | `isStatic`=false, `isConst`=false |
| `:Property` | `typeId` (string), `accessibility` (string), `attributes` (string[]), `isStatic` (boolean), `signature` (string), `typeFqn` (string), `hasGetter` (boolean), `hasSetter` (boolean), `isPublicApi` (boolean) | `isStatic`=false, `hasGetter`=false, `hasSetter`=false, `isPublicApi`=false |
| `:Event` | `typeId` (string), `accessibility` (string), `attributes` (string[]), `isStatic` (boolean), `typeFqn` (string) | `isStatic`=false |
| `:NativeType` | `typeId` (string), `kind` (string), `isDefinition` (boolean), `resolved` (boolean) | `isDefinition`=false, `resolved`=true |
| `:NativeFunction` | `typeId` (string), `signature` (string), `mangledName` (string), `qualifiedName` (string), `isMethod` (boolean), `isVirtual` (boolean), `isStatic` (boolean), `isDefinition` (boolean), `isExternC` (boolean), `resolved` (boolean), `paramCount` (int) | `isMethod`=false, `isVirtual`=false, `isStatic`=false, `isDefinition`=false, `isExternC`=false, `resolved`=true |
| `:NativeExport` | `dll` (string), `entryPoint` (string) |  |
| `:Message` | `kind` (string), `level` (string), `template` (string), `templateStyle` (string), `literalChars` (int), `regex` (string) |  |
| `:ConfigKey` | `key` (string), `accessor` (string) |  |
| `:External` | `symbolKind` (string), `expectedFrom` (string) |  |
| `:IndexRun` | `runId` (string), `startedAt` (string), `finishedAt` (string), `mode` (string), `dirty` (boolean), `toolVersions` (string), `counts` (string), `warnings` (string) | `dirty`=false |

Entry points: `entryKind` is `main`, `remote`, `scheduled`, `plugin` (set by the C# extractor from the entry
point and configured attributes) or `ui_handler`, `native_callback` (derived by the loader, `entrySource: derived`).

## Relationship properties

Common to every type: `owner` (string), `file` (string), `line` (int), `lines` (int[]), `count` (int), `ilOffset` (int), `kind` (string), `via` (string), `confidence` (string), `seenRun` (string), `derived` (boolean), `implicit` (boolean).
`via` defaults to `direct` for CALLS, EMITS, READS, READS_CONFIG, REFERENCES_METHOD, SUBSCRIBES, WRITES; `confidence` defaults to `exact`.
`line` is the first occurrence, `lines`/`count` list all occurrences inside one owner (edges are merged).

| Type | Extra properties | From → to |
|---|---|---|
| `CONTAINS` | — | Assembly → Type / NativeType |
| `REFERENCES` | — | Assembly → Assembly (`kind`: project, transitive, binary, pinvoke, ijw) |
| `DEFINED_IN` | `lineEnd` (int) | Type / NativeType → File (one per declaration) |
| `HAS_MEMBER` | — | Type → member or nested Type; NativeType → NativeFunction |
| `INHERITS` | — | Type → Type; NativeType → NativeType |
| `IMPLEMENTS` | — | Type → interface Type |
| `IMPLEMENTS_METHOD` | — | Method → interface Method |
| `OVERRIDES` | — | Method → Method; NativeFunction → NativeFunction |
| `DISPATCHES_TO` | — | declaration → every implementation/override (derived) |
| `CALLS` | — | Method → Method (`kind`: call, ctor, getter, setter); NativeFunction → NativeFunction |
| `REFERENCES_METHOD` | `callbackKind` (string) | Method → Method (method group, delegate) |
| `SUBSCRIBES` | `handler` (string), `uiEvent` (boolean) | Method → Event (`handler` = handler method id) |
| `READS` | — | Method → Field / Property |
| `WRITES` | — | Method → Field / Property |
| `EMITS` | `exceptionType` (string), `level` (string) | Method / NativeFunction → Message |
| `READS_CONFIG` | — | Method → ConfigKey |
| `PINVOKES` | — | Method → NativeExport |
| `RESOLVES_TO` | `candidates` (int) | NativeExport → NativeFunction |
| `CALLS_NATIVE` | `nativeName` (string), `nativeParamCount` (int), `candidates` (int) | C++/CLI Method → NativeFunction (IJW) |
| `WRAPS` | `nativeName` (string), `candidates` (int) | C++/CLI Type → NativeType |
| `REFS_EXTERNAL` | `rel` (string), `exceptionType` (string), `level` (string), `handler` (string), `uiEvent` (boolean), `callbackKind` (string), `lineEnd` (int), `nativeName` (string), `nativeParamCount` (int), `candidates` (int) | anything → External (`rel` = original type; not traversed by tools) |
| `CO_CHANGES_WITH` | — | File → File (git history, heuristic) |

## Constraints and indexes

```cypher
CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_type IF NOT EXISTS FOR (n:Type) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_method IF NOT EXISTS FOR (n:Method) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_field IF NOT EXISTS FOR (n:Field) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_prop IF NOT EXISTS FOR (n:Property) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_event IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_ntype IF NOT EXISTS FOR (n:NativeType) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_nfunc IF NOT EXISTS FOR (n:NativeFunction) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_export IF NOT EXISTS FOR (n:NativeExport) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_msg IF NOT EXISTS FOR (n:Message) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_cfg IF NOT EXISTS FOR (n:ConfigKey) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_asm IF NOT EXISTS FOR (n:Assembly) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_file IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT node_id_ext IF NOT EXISTS FOR (n:External) REQUIRE n.id IS UNIQUE;
CREATE INDEX method_name IF NOT EXISTS FOR (n:Method) ON (n.name);
CREATE INDEX type_name IF NOT EXISTS FOR (n:Type) ON (n.name);
CREATE INDEX nfunc_name IF NOT EXISTS FOR (n:NativeFunction) ON (n.qualifiedName);
CREATE INDEX nfunc_mangle IF NOT EXISTS FOR (n:NativeFunction) ON (n.mangledName);
CREATE INDEX method_asm IF NOT EXISTS FOR (n:Method) ON (n.assembly);
CREATE INDEX node_assembly IF NOT EXISTS FOR (n:Node) ON (n.assembly);
CREATE INDEX node_name IF NOT EXISTS FOR (n:Node) ON (n.name);
CREATE INDEX node_fqn IF NOT EXISTS FOR (n:Node) ON (n.fqn);
CREATE INDEX method_filekey IF NOT EXISTS FOR (n:Method) ON (n.fileKey);
CREATE INDEX nfunc_filekey IF NOT EXISTS FOR (n:NativeFunction) ON (n.fileKey);
CREATE INDEX method_entry IF NOT EXISTS FOR (n:Method) ON (n.isEntryPoint);
CREATE INDEX method_test IF NOT EXISTS FOR (n:Method) ON (n.isTest);
CREATE INDEX configkey_key IF NOT EXISTS FOR (n:ConfigKey) ON (n.key);
CREATE INDEX run_started IF NOT EXISTS FOR (n:IndexRun) ON (n.startedAt);
CREATE INDEX implements_owner IF NOT EXISTS FOR ()-[r:IMPLEMENTS_METHOD]-() ON (r.owner);
CREATE FULLTEXT INDEX message_text IF NOT EXISTS FOR (n:Message) ON EACH [n.template];
CREATE FULLTEXT INDEX symbol_text IF NOT EXISTS FOR (n:Method|Type|NativeFunction|NativeType|Field|Property|Event) ON EACH [n.name, n.fqn];
```

## Useful queries (Neo4j Browser)

```cypher
// callers of a method through interfaces, 3 levels
MATCH p = (c:Method)-[:CALLS|DISPATCHES_TO*1..3]->(m:Node {id: 'dn:M:MiniPlant.Core.StageBase.Move(System.Double)'}) RETURN p LIMIT 50;
// where C# crosses into native code
MATCH (m:Method)-[r:PINVOKES|CALLS_NATIVE]->(n) RETURN m.fqn, type(r), n.fqn LIMIT 50;
// entry points by kind
MATCH (m:Method {isEntryPoint: true}) RETURN m.entryKind, count(*) ORDER BY count(*) DESC;
// latest index run
MATCH (r:IndexRun) RETURN r ORDER BY r.startedAt DESC LIMIT 1;
```
