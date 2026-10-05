using System.Text.Json.Serialization;

namespace CodeGraph.Shared;

/// <summary>
/// One graph node, written as one line of &lt;assembly&gt;.nodes.jsonl.
/// Every property is optional; null properties are left out of the JSON, so each label only
/// carries the properties listed for it in docs/SCHEMA.md. The loader stamps commit and seenRun.
/// </summary>
public sealed class NodeRecord
{
    // Common to all labels (SPEC 4.2).
    public string Label { get; set; } = "";
    public string Id { get; set; } = "";
    public string? Name { get; set; }
    public string? Fqn { get; set; }
    public string? Lang { get; set; }
    public string? Assembly { get; set; }
    public string? File { get; set; }
    public int? LineStart { get; set; }
    public int? LineEnd { get; set; }

    // Containment and kind.
    public string? TypeId { get; set; }          // containing type of a member or nested type
    public string? Kind { get; set; }            // class/struct/interface/... or csharp/cppcli/native or log/exception
    public string? Accessibility { get; set; }
    public string? Signature { get; set; }
    public string? TypeFqn { get; set; }
    public string? AssociatedId { get; set; }    // property/event that owns an accessor method
    public string[]? Attributes { get; set; }

    // Flags.
    public bool? IsStatic { get; set; }
    public bool? IsAbstract { get; set; }
    public bool? IsVirtual { get; set; }
    public bool? IsOverride { get; set; }
    public bool? IsExtern { get; set; }
    public bool? IsCtor { get; set; }
    public bool? IsAccessor { get; set; }
    public bool? IsImplicit { get; set; }
    public bool? IsConst { get; set; }
    public bool? HasGetter { get; set; }
    public bool? HasSetter { get; set; }
    public string? ConstValue { get; set; }

    // Classification (SPEC 5.7).
    public bool? IsTest { get; set; }
    public bool? IsEntryPoint { get; set; }
    public string? EntryKind { get; set; }
    public bool? IsPublicApi { get; set; }
    public bool? IsTestAssembly { get; set; }
    public string? TargetFramework { get; set; }
    public string[]? DelegateParamTypes { get; set; } // extern methods: delegate types they accept

    // :File
    public string? Path { get; set; }
    public string? Sha1 { get; set; }
    public bool? Generated { get; set; }

    // :Message
    public string? Level { get; set; }
    public string? Template { get; set; }
    public string? TemplateStyle { get; set; }   // dotnet (composite format) or printf

    // :ConfigKey
    public string? Key { get; set; }
    public string? Accessor { get; set; }

    // :NativeExport
    public string? Dll { get; set; }
    public string? EntryPoint { get; set; }

    // :NativeFunction / :NativeType
    public string? MangledName { get; set; }
    public string? QualifiedName { get; set; }
    public bool? IsMethod { get; set; }
    public bool? IsDefinition { get; set; }
    public bool? Resolved { get; set; }
    public int? ParamCount { get; set; }
    public int? IndirectNativeCalls { get; set; } // bridge: calli instructions (virtual native calls)

    // :External
    public string? SymbolKind { get; set; }
    public string? ExpectedFrom { get; set; }    // "bridge": a C++/CLI symbol the bridge must supply
    public string? LocationConfidence { get; set; }
}

/// <summary>
/// One graph relationship, written as one line of &lt;assembly&gt;.edges.jsonl.
/// Repeated occurrences of the same (type, start, end, kind, via) inside one owner are merged:
/// Line is the first occurrence, Lines lists all of them and Count says how many there were.
/// </summary>
public sealed class EdgeRecord
{
    public string Type { get; set; } = "";
    public string Start { get; set; } = "";
    public string End { get; set; } = "";
    public string Owner { get; set; } = "";
    public string? File { get; set; }
    public int? Line { get; set; }
    public int[]? Lines { get; set; }
    public int? Count { get; set; }
    public int? IlOffset { get; set; }
    public string? Kind { get; set; }
    public string? Via { get; set; }
    public string? Confidence { get; set; }

    // Relationship-specific properties.
    public string? ExceptionType { get; set; }   // EMITS from a throw
    public string? Level { get; set; }           // EMITS from a log call
    public string? Handler { get; set; }         // SUBSCRIBES: the handler method id
    public bool? UiEvent { get; set; }           // SUBSCRIBES: event of a configured UI base type
    public string? CallbackKind { get; set; }    // REFERENCES_METHOD: method group passed to native code
    public bool? Implicit { get; set; }          // compiler-inserted (implicit base ctor call, initializer)
    public int? LineEnd { get; set; }            // DEFINED_IN: end of the partial declaration
    public string? NativeName { get; set; }      // bridge: native name still to be resolved by the loader
    public int? NativeParamCount { get; set; }   // bridge: parameter count used to break overload ties
}

/// <summary>Source-generated serializer: fast, reflection-free JSON for millions of records.</summary>
[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    WriteIndented = false)]
[JsonSerializable(typeof(NodeRecord))]
[JsonSerializable(typeof(EdgeRecord))]
public partial class RecordJsonContext : JsonSerializerContext
{
}
