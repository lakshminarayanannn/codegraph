"""Graph schema (SPEC 4.2 and 4.3): labels, relationship types, property types and defaults.

The loader builds neo4j-admin CSV headers from these tables and fills defaults, so a query such as
`WHERE NOT m.isTest` never meets a null. docs/SCHEMA.md describes the same tables for people.
Every node also carries the label :Node, which has the unique `id` constraint used for all lookups.
"""
from __future__ import annotations

STRING, INT, BOOL, STRINGS, INTS = "string", "int", "boolean", "string[]", "int[]"

COMMON_NODE_PROPS: dict[str, str] = {
    "id": STRING,
    "name": STRING,
    "fqn": STRING,
    "lang": STRING,
    "assembly": STRING,
    "file": STRING,
    "fileKey": STRING,       # lower-case file, indexed: diff_impact maps git paths to symbols with it
    "lineStart": INT,
    "lineEnd": INT,
    "commit": STRING,
    "seenRun": STRING,
}

_MEMBER = {"typeId": STRING, "accessibility": STRING, "attributes": STRINGS, "isStatic": BOOL}

NODE_PROPS: dict[str, dict[str, str]] = {
    "Assembly": {"kind": STRING, "targetFramework": STRING, "layer": STRING, "layerIndex": INT,
                 "isTestAssembly": BOOL},
    "File": {"path": STRING, "sha1": STRING, "commitCount": INT, "lastCommitDate": STRING, "generated": BOOL},
    "Type": {**_MEMBER, "kind": STRING, "isAbstract": BOOL, "isPublicApi": BOOL, "locationConfidence": STRING},
    "Method": {**_MEMBER, "signature": STRING, "isVirtual": BOOL, "isAbstract": BOOL, "isOverride": BOOL,
               "isExtern": BOOL, "isCtor": BOOL, "isAccessor": BOOL, "isImplicit": BOOL, "associatedId": STRING,
               "isTest": BOOL, "isEntryPoint": BOOL, "entryKind": STRING, "entrySource": STRING,
               "entryConfidence": STRING, "isPublicApi": BOOL, "delegateParamTypes": STRINGS,
               "indirectNativeCalls": INT, "locationConfidence": STRING},
    "Field": {**_MEMBER, "typeFqn": STRING, "isConst": BOOL, "constValue": STRING},
    "Property": {**_MEMBER, "signature": STRING, "typeFqn": STRING, "hasGetter": BOOL, "hasSetter": BOOL,
                 "isPublicApi": BOOL},
    "Event": {**_MEMBER, "typeFqn": STRING},
    "NativeType": {"typeId": STRING, "kind": STRING, "isDefinition": BOOL, "resolved": BOOL},
    "NativeFunction": {"typeId": STRING, "signature": STRING, "mangledName": STRING, "qualifiedName": STRING,
                       "isMethod": BOOL, "isVirtual": BOOL, "isStatic": BOOL, "isDefinition": BOOL,
                       "isExternC": BOOL, "resolved": BOOL, "paramCount": INT},
    "NativeExport": {"dll": STRING, "entryPoint": STRING},
    "Message": {"kind": STRING, "level": STRING, "template": STRING, "templateStyle": STRING,
                "literalChars": INT, "regex": STRING},
    "ConfigKey": {"key": STRING, "accessor": STRING},
    "External": {"symbolKind": STRING, "expectedFrom": STRING},
    "IndexRun": {"runId": STRING, "startedAt": STRING, "finishedAt": STRING, "mode": STRING, "dirty": BOOL,
                 "toolVersions": STRING, "counts": STRING, "warnings": STRING},
}

# Defaults applied to every node of a label when the extractor left the property out.
NODE_DEFAULTS: dict[str, dict[str, object]] = {
    label: {name: False for name, kind in props.items() if kind == BOOL} for label, props in NODE_PROPS.items()
}
NODE_DEFAULTS["NativeFunction"]["resolved"] = True
NODE_DEFAULTS["NativeType"]["resolved"] = True

COMMON_REL_PROPS: dict[str, str] = {
    "owner": STRING,
    "file": STRING,
    "line": INT,
    "lines": INTS,
    "count": INT,
    "ilOffset": INT,
    "kind": STRING,
    "via": STRING,
    "confidence": STRING,
    "seenRun": STRING,
    "derived": BOOL,
    "implicit": BOOL,
}

_SPECIFIC = {
    "exceptionType": STRING,
    "level": STRING,
    "handler": STRING,
    "uiEvent": BOOL,
    "callbackKind": STRING,
    "lineEnd": INT,
    "nativeName": STRING,
    "nativeParamCount": INT,
    "candidates": INT,
}

REL_PROPS: dict[str, dict[str, str]] = {
    "CONTAINS": {},
    "REFERENCES": {},
    "DEFINED_IN": {"lineEnd": INT},
    "HAS_MEMBER": {},
    "INHERITS": {},
    "IMPLEMENTS": {},
    "IMPLEMENTS_METHOD": {},
    "OVERRIDES": {},
    "DISPATCHES_TO": {},
    "CALLS": {},
    "REFERENCES_METHOD": {"callbackKind": STRING},
    "SUBSCRIBES": {"handler": STRING, "uiEvent": BOOL},
    "READS": {},
    "WRITES": {},
    "EMITS": {"exceptionType": STRING, "level": STRING},
    "READS_CONFIG": {},
    "PINVOKES": {},
    "RESOLVES_TO": {"candidates": INT},
    "CALLS_NATIVE": {"nativeName": STRING, "nativeParamCount": INT, "candidates": INT},
    "WRAPS": {"nativeName": STRING, "candidates": INT},
    # An edge whose target is outside the indexed code keeps its original type in `rel`.
    "REFS_EXTERNAL": {"rel": STRING, **_SPECIFIC},
    "CO_CHANGES_WITH": {},
}

# Relationship types whose `via` defaults to "direct" (the others are declarations, not code paths).
VIA_TYPES = {"CALLS", "REFERENCES_METHOD", "READS", "WRITES", "EMITS", "SUBSCRIBES", "READS_CONFIG"}


def node_props(label: str) -> dict[str, str]:
    """All properties a node of this label may carry, common ones first."""
    return {**COMMON_NODE_PROPS, **NODE_PROPS.get(label, {})}


def rel_props(rel_type: str) -> dict[str, str]:
    return {**COMMON_REL_PROPS, **REL_PROPS.get(rel_type, {})}


# Constraints and indexes (SPEC 4.4), plus :Node(id) - the universal lookup used by every tool query -
# and the property indexes the incremental loader and diff_impact rely on.
SCHEMA_STATEMENTS: list[str] = [
    "CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
    *[
        f"CREATE CONSTRAINT node_id_{alias} IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for alias, label in [
            ("type", "Type"), ("method", "Method"), ("field", "Field"), ("prop", "Property"), ("event", "Event"),
            ("ntype", "NativeType"), ("nfunc", "NativeFunction"), ("export", "NativeExport"), ("msg", "Message"),
            ("cfg", "ConfigKey"), ("asm", "Assembly"), ("file", "File"), ("ext", "External"),
        ]
    ],
    "CREATE INDEX method_name IF NOT EXISTS FOR (n:Method) ON (n.name)",
    "CREATE INDEX type_name IF NOT EXISTS FOR (n:Type) ON (n.name)",
    "CREATE INDEX nfunc_name IF NOT EXISTS FOR (n:NativeFunction) ON (n.qualifiedName)",
    "CREATE INDEX nfunc_mangle IF NOT EXISTS FOR (n:NativeFunction) ON (n.mangledName)",
    "CREATE INDEX method_asm IF NOT EXISTS FOR (n:Method) ON (n.assembly)",
    "CREATE INDEX node_assembly IF NOT EXISTS FOR (n:Node) ON (n.assembly)",
    "CREATE INDEX node_name IF NOT EXISTS FOR (n:Node) ON (n.name)",
    "CREATE INDEX node_fqn IF NOT EXISTS FOR (n:Node) ON (n.fqn)",
    "CREATE INDEX method_filekey IF NOT EXISTS FOR (n:Method) ON (n.fileKey)",
    "CREATE INDEX nfunc_filekey IF NOT EXISTS FOR (n:NativeFunction) ON (n.fileKey)",
    "CREATE INDEX method_entry IF NOT EXISTS FOR (n:Method) ON (n.isEntryPoint)",
    "CREATE INDEX method_test IF NOT EXISTS FOR (n:Method) ON (n.isTest)",
    "CREATE INDEX configkey_key IF NOT EXISTS FOR (n:ConfigKey) ON (n.key)",
    "CREATE INDEX run_started IF NOT EXISTS FOR (n:IndexRun) ON (n.startedAt)",
    "CREATE INDEX implements_owner IF NOT EXISTS FOR ()-[r:IMPLEMENTS_METHOD]-() ON (r.owner)",
    "CREATE FULLTEXT INDEX message_text IF NOT EXISTS FOR (n:Message) ON EACH [n.template]",
    "CREATE FULLTEXT INDEX symbol_text IF NOT EXISTS FOR (n:Method|Type|NativeFunction|NativeType|Field|Property|Event) "
    "ON EACH [n.name, n.fqn]",
]
