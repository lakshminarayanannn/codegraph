using System.Collections.Concurrent;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace CodeGraph.Roslyn;

/// <summary>
/// Per-project state shared by the symbol and body passes: symbol IDs, :External stubs, source
/// locations and the node/edge collectors. Thread-safe: the body pass calls it from many threads.
/// </summary>
internal sealed class GraphBuilder
{
    /// <summary>"MiniPlant.Core.Registry", "System.Collections.Generic.List&lt;T&gt;": no global::, no special-type keywords.</summary>
    public static readonly SymbolDisplayFormat FqnFormat = new(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.ExpandNullable);

    /// <summary>"bool DoMove(double targetMm)": what outline shows next to a member.</summary>
    public static readonly SymbolDisplayFormat SignatureFormat = new(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameOnly,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        memberOptions: SymbolDisplayMemberOptions.IncludeParameters | SymbolDisplayMemberOptions.IncludeType
                       | SymbolDisplayMemberOptions.IncludeRef,
        parameterOptions: SymbolDisplayParameterOptions.IncludeType | SymbolDisplayParameterOptions.IncludeName
                          | SymbolDisplayParameterOptions.IncludeParamsRefOut | SymbolDisplayParameterOptions.IncludeExtensionThis
                          | SymbolDisplayParameterOptions.IncludeDefaultValue,
        propertyStyle: SymbolDisplayPropertyStyle.ShowReadWriteDescriptor,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.UseSpecialTypes
                              | SymbolDisplayMiscellaneousOptions.EscapeKeywordIdentifiers);

    private readonly ConcurrentDictionary<ISymbol, string?> _ids = new(SymbolEqualityComparer.Default);
    private readonly ConcurrentDictionary<string, string> _relativePaths = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, byte> _stubs = new(StringComparer.Ordinal);

    public GraphBuilder(Settings settings, Compilation compilation)
    {
        Settings = settings;
        Compilation = compilation;
        AssemblyName = compilation.AssemblyName ?? "unknown";
        AssemblyId = Ids.Assembly(AssemblyName);
        Rules = new Rules(settings, compilation);
    }

    public Settings Settings { get; }
    public Compilation Compilation { get; }
    public string AssemblyName { get; }
    public string AssemblyId { get; }
    public Rules Rules { get; }
    public NodeSet Nodes { get; } = new();
    public EdgeAggregator Edges { get; } = new();

    /// <summary>
    /// The declared symbol behind a use: generic instantiations and reduced extension calls point at
    /// the declaration (SPEC 5.3), so List&lt;int&gt;.Add and List&lt;string&gt;.Add are one node.
    /// </summary>
    public static ISymbol Normalize(ISymbol symbol) => symbol switch
    {
        IMethodSymbol method => (method.ReducedFrom ?? method).OriginalDefinition,
        _ => symbol.OriginalDefinition,
    };

    /// <summary>"dn:" + documentation-comment ID, or null for symbols that have none (lambdas, local functions, errors).</summary>
    public string? Id(ISymbol symbol) => _ids.GetOrAdd(Normalize(symbol), static s =>
        s.GetDocumentationCommentId() is { } doc && !doc.StartsWith("!:", StringComparison.Ordinal) ? Ids.Dn(doc) : null);

    public bool IsOwn(ISymbol symbol) =>
        SymbolEqualityComparer.Default.Equals(Normalize(symbol).ContainingAssembly, Compilation.Assembly);

    /// <summary>
    /// ID of an edge target. Targets outside this assembly also get an :External stub node; the loader
    /// drops the stub when another extractor defines the real node (another project, the bridge).
    /// </summary>
    public string? Target(ISymbol symbol)
    {
        var normalized = Normalize(symbol);
        var id = Id(normalized);
        if (id is not null && !IsOwn(normalized) && _stubs.TryAdd(id, 0))
        {
            var assembly = normalized.ContainingAssembly?.Name;
            var fromBridge = assembly is not null && Settings.CppCliNames.Contains(assembly);
            Nodes.Add(new NodeRecord
            {
                Label = "External",
                Id = id,
                Name = normalized.Name,
                Fqn = Fqn(normalized),
                Lang = fromBridge ? "cppcli" : "ext",
                Assembly = assembly,
                SymbolKind = KindName(normalized),
                ExpectedFrom = fromBridge ? "bridge" : null,
            });
        }

        return id;
    }

    public static string Fqn(ISymbol symbol) => symbol switch
    {
        INamedTypeSymbol type => type.ToDisplayString(FqnFormat),
        { ContainingType: { } container } => container.ToDisplayString(FqnFormat) + "." + symbol.Name,
        _ => symbol.ToDisplayString(FqnFormat),
    };

    public static string KindName(ISymbol symbol) => symbol switch
    {
        INamedTypeSymbol => "Type",
        IMethodSymbol => "Method",
        IFieldSymbol => "Field",
        IPropertySymbol => "Property",
        IEventSymbol => "Event",
        _ => symbol.Kind.ToString(),
    };

    /// <summary>Repo-relative path with forward slashes, cached because every edge needs one.</summary>
    public string RelativePath(string? fullPath) =>
        string.IsNullOrEmpty(fullPath) ? "" : _relativePaths.GetOrAdd(fullPath, p => Ids.RelativeTo(Settings.RepoRoot, p));

    /// <summary>File and line span of a symbol's first declaration (by path, then position) - deterministic for partials.</summary>
    public (string? File, int? Start, int? End) Span(ISymbol symbol)
    {
        var reference = symbol.DeclaringSyntaxReferences
            .OrderBy(r => r.SyntaxTree.FilePath, StringComparer.Ordinal)
            .ThenBy(r => r.Span.Start)
            .FirstOrDefault();
        if (reference is not null)
        {
            var lines = reference.SyntaxTree.GetLineSpan(reference.Span);
            return (RelativePath(reference.SyntaxTree.FilePath), lines.StartLinePosition.Line + 1, lines.EndLinePosition.Line + 1);
        }

        // Implicitly declared members (default constructors, record members) have no syntax: use the type's location.
        var location = symbol.Locations.Where(l => l.IsInSource)
            .OrderBy(l => l.SourceTree!.FilePath, StringComparer.Ordinal).ThenBy(l => l.SourceSpan.Start)
            .FirstOrDefault();
        if (location is not null)
        {
            var line = location.GetLineSpan().StartLinePosition.Line + 1;
            return (RelativePath(location.SourceTree!.FilePath), line, line);
        }

        return (null, null, null);
    }

    public static int Line(SyntaxNode node) => node.SyntaxTree.GetLineSpan(node.Span).StartLinePosition.Line + 1;

    public string[]? Attributes(ISymbol symbol)
    {
        var names = symbol.GetAttributes()
            .Select(a => a.AttributeClass?.ToDisplayString(FqnFormat))
            .OfType<string>()
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        return names.Length > 0 ? names : null;
    }

    public void AddEdge(string type, string start, string end, string? file, int? line, string? kind = null,
        string? via = null, string? discriminator = null, int? lineEnd = null, bool? isImplicit = null)
    {
        Edges.Add(new EdgeRecord
        {
            Type = type,
            Start = start,
            End = end,
            Owner = AssemblyName,
            File = file,
            Line = line,
            Kind = kind,
            Via = via,
            LineEnd = lineEnd,
            Implicit = isImplicit,
        }, discriminator);
    }

    /// <summary>
    /// Methods that run a field/property initializer: the static constructor for static members, otherwise
    /// every instance constructor that does not chain to this(...) (those run the initializers via the chain).
    /// </summary>
    public string[] InitializerOwners(INamedTypeSymbol type, bool isStatic)
    {
        if (isStatic)
        {
            return new[] { StaticConstructorId(type) };
        }

        return type.InstanceConstructors
            .Where(c => !ChainsToThis(c))
            .Select(c => Id(c))
            .OfType<string>()
            .ToArray();
    }

    /// <summary>The explicit static constructor, or the one the compiler synthesizes for static initializers.</summary>
    public string StaticConstructorId(INamedTypeSymbol type) =>
        type.StaticConstructors.FirstOrDefault() is { } cctor && Id(cctor) is { } id ? id : SynthesizedStaticConstructorId(type);

    public string SynthesizedStaticConstructorId(INamedTypeSymbol type) =>
        Ids.Dn("M:" + type.GetDocumentationCommentId()![2..] + ".#cctor");

    private static bool ChainsToThis(IMethodSymbol constructor) =>
        constructor.DeclaringSyntaxReferences.Any(r =>
            r.GetSyntax() is ConstructorDeclarationSyntax { Initializer: { } init }
            && init.ThisOrBaseKeyword.IsKind(SyntaxKind.ThisKeyword));
}
