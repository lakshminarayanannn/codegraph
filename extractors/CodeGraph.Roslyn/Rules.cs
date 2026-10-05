using System.Collections.Concurrent;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.Operations;

namespace CodeGraph.Roslyn;

/// <summary>
/// Configuration-driven matching (SPEC 5.4, 5.7, 13): which calls are logging, which reads are
/// configuration, which events are UI events, which methods are tests and entry points.
/// All decisions come from codegraph.yaml; nothing here is specific to one codebase.
/// </summary>
internal sealed class Rules
{
    private readonly Settings _settings;
    private readonly Dictionary<string, List<LogMethodRule>> _logRulesByMethod;
    private readonly ConcurrentDictionary<IMethodSymbol, LogMethodRule?> _logCache = new(SymbolEqualityComparer.Default);
    private readonly ConcurrentDictionary<INamedTypeSymbol, bool> _uiTypeCache = new(SymbolEqualityComparer.Default);
    private readonly IMethodSymbol? _mainMethod;

    public Rules(Settings settings, Compilation compilation)
    {
        _settings = settings;
        _logRulesByMethod = settings.Config.LogMethods
            .SelectMany(rule => rule.Methods.Select(method => (method, rule)))
            .GroupBy(x => x.method, StringComparer.Ordinal)
            .ToDictionary(g => g.Key, g => g.Select(x => x.rule).ToList(), StringComparer.Ordinal);
        _mainMethod = compilation.GetEntryPoint(CancellationToken.None);

        var name = compilation.AssemblyName ?? "";
        IsTestAssembly = Glob.AnyMatch(settings.Config.TestAssemblyPatterns, name)
                         || compilation.ReferencedAssemblyNames.Any(r =>
                             Settings.TestFrameworkAssemblies.Contains(r.Name, StringComparer.OrdinalIgnoreCase));
    }

    public bool IsTestAssembly { get; }

    // ---- logging -----------------------------------------------------------------------------

    /// <summary>The configured log rule that applies to this call target, if any. Cached per method.</summary>
    public LogMethodRule? MatchLog(IMethodSymbol target)
    {
        if (!_logRulesByMethod.TryGetValue(target.Name, out var candidates))
        {
            return null; // fast path: most calls are not to a method named Error/Info/...
        }

        return _logCache.GetOrAdd(target.OriginalDefinition, t => candidates.FirstOrDefault(r => Applies(r, t.ContainingType)));
    }

    private static bool Applies(LogMethodRule rule, INamedTypeSymbol? type)
    {
        if (type is null)
        {
            return false;
        }

        if (rule.Type is { } exact)
        {
            return Name(type) == exact;
        }

        if (rule.TypeImplements is { } wanted)
        {
            return Name(type) == wanted
                   || type.AllInterfaces.Any(i => Name(i) == wanted)
                   || BaseTypes(type).Any(b => Name(b) == wanted);
        }

        return false;
    }

    /// <summary>literal: the argument is printed as is; composite: it is a "{0}" format string.</summary>
    public static TemplateStyle StyleOf(LogMethodRule rule, IMethodSymbol target) => rule.TemplateStyle switch
    {
        "literal" => TemplateStyle.Literal,
        "composite" => TemplateStyle.Composite,
        _ => target.Name.EndsWith("Format", StringComparison.Ordinal) ? TemplateStyle.Composite : TemplateStyle.Literal,
    };

    /// <summary>Log level: from the method name (ErrorFormat -> error), a level argument, or a fixed value.</summary>
    public static string? LevelOf(LogMethodRule rule, IInvocationOperation call)
    {
        switch (rule.LevelFrom)
        {
            case "fixed":
                return rule.Level;
            case "arg":
                var argument = call.Arguments.FirstOrDefault(a => a.Parameter?.Ordinal == rule.LevelArg);
                return argument is null ? null : ConstantText(argument.Value)?.ToLowerInvariant();
            default:
                var name = call.TargetMethod.Name;
                return (name.EndsWith("Format", StringComparison.Ordinal) ? name[..^"Format".Length] : name).ToLowerInvariant();
        }
    }

    /// <summary>An enum member's name (LogLevel.Error -> "Error") or a constant's value.</summary>
    private static string? ConstantText(IOperation value)
    {
        value = Templates.Unwrap(value);
        if (value is IFieldReferenceOperation { Field.ContainingType.TypeKind: TypeKind.Enum } field)
        {
            return field.Field.Name;
        }

        return value.ConstantValue.HasValue ? Convert.ToString(value.ConstantValue.Value, System.Globalization.CultureInfo.InvariantCulture) : null;
    }

    // ---- configuration reads -----------------------------------------------------------------

    /// <summary>Config key read by a call: Get("key") on an indexer-style member, or a configured accessor method.</summary>
    public (string Key, string Accessor)? MatchConfig(IInvocationOperation call)
    {
        foreach (var rule in _settings.Config.ConfigAccessors)
        {
            if (rule.Kind == "method" && MemberName(call.TargetMethod) == rule.Member)
            {
                var argument = call.Arguments.FirstOrDefault(a => a.Parameter?.Ordinal == rule.KeyArg);
                if (argument is not null && KeyOf(argument.Value) is { } key)
                {
                    return (key, rule.Member);
                }
            }

            // AppSettings.Get("key") / AppSettings.GetValues("key") on an indexer-style member.
            if (rule.Kind == "indexer" && call.Instance is { } instance
                && Templates.Unwrap(instance) is IPropertyReferenceOperation collection
                && MemberName(collection.Property) == rule.Member
                && call.Arguments.Length > 0 && KeyOf(call.Arguments[0].Value) is { } getKey)
            {
                return (getKey, rule.Member);
            }
        }

        return null;
    }

    /// <summary>Config key read through a property: AppSettings["key"], or Settings.Default.Key for kind=property.</summary>
    public (string Key, string Accessor)? MatchConfig(IPropertyReferenceOperation reference)
    {
        foreach (var rule in _settings.Config.ConfigAccessors)
        {
            if (rule.Kind == "indexer" && reference.Property.IsIndexer && reference.Instance is { } instance
                && Templates.Unwrap(instance) is IPropertyReferenceOperation collection
                && MemberName(collection.Property) == rule.Member
                && reference.Arguments.Length > 0 && KeyOf(reference.Arguments[0].Value) is { } key)
            {
                return (key, rule.Member);
            }

            if (rule.Kind == "property" && Name(reference.Property.ContainingType) == rule.Member)
            {
                return (reference.Property.Name, rule.Member);
            }
        }

        return null;
    }

    private static string? KeyOf(IOperation value) =>
        Templates.Unwrap(value).ConstantValue is { HasValue: true, Value: string key } ? key : null;

    // ---- UI events, tests, entry points ------------------------------------------------------

    /// <summary>An event of a type deriving from a configured UI base type (Control, UIElement, ...).</summary>
    public bool IsUiEvent(IEventReferenceOperation reference)
    {
        var declaring = reference.Event.ContainingType;
        var receiver = reference.Instance?.Type as INamedTypeSymbol;
        return IsUiType(declaring) || (receiver is not null && IsUiType(receiver));
    }

    private bool IsUiType(INamedTypeSymbol type) => _uiTypeCache.GetOrAdd(type, t =>
        _settings.UiEventBaseTypes.Contains(Name(t)) || BaseTypes(t).Any(b => _settings.UiEventBaseTypes.Contains(Name(b))));

    public bool IsTest(IMethodSymbol method) =>
        method.GetAttributes().Any(a => a.AttributeClass is { } c && SelfAndBases(c).Any(t => _settings.TestAttributes.Contains(Name(t))));

    /// <summary>main, or the kind of a configured attribute on the method (or, for public methods, on its type).</summary>
    public string? EntryKind(IMethodSymbol method)
    {
        if (_mainMethod is not null && SymbolEqualityComparer.Default.Equals(method, _mainMethod))
        {
            return "main";
        }

        if (AttributeKind(method) is { } kind)
        {
            return kind;
        }

        return method.DeclaredAccessibility == Accessibility.Public && method.MethodKind == MethodKind.Ordinary
               && method.ContainingType is { } type
            ? AttributeKind(type)
            : null;
    }

    private string? AttributeKind(ISymbol symbol)
    {
        foreach (var attribute in symbol.GetAttributes())
        {
            if (attribute.AttributeClass is null)
            {
                continue;
            }

            foreach (var type in SelfAndBases(attribute.AttributeClass))
            {
                if (_settings.EntryPointAttributes.TryGetValue(Name(type), out var kind))
                {
                    return kind;
                }
            }
        }

        return null;
    }

    /// <summary>Public on the member and on every containing type, in a non-test assembly (SPEC 5.7).</summary>
    public bool IsPublicApi(ISymbol symbol)
    {
        if (IsTestAssembly || symbol.DeclaredAccessibility != Accessibility.Public)
        {
            return false;
        }

        for (var type = symbol.ContainingType; type is not null; type = type.ContainingType)
        {
            if (type.DeclaredAccessibility != Accessibility.Public)
            {
                return false;
            }
        }

        return true;
    }

    // ---- helpers -----------------------------------------------------------------------------

    public static string Name(INamedTypeSymbol type) => type.OriginalDefinition.ToDisplayString(GraphBuilder.FqnFormat);

    public static string MemberName(ISymbol member) =>
        member.ContainingType is { } type ? Name(type) + "." + member.Name : member.Name;

    private static IEnumerable<INamedTypeSymbol> BaseTypes(INamedTypeSymbol type)
    {
        for (var current = type.BaseType; current is not null; current = current.BaseType)
        {
            yield return current;
        }
    }

    private static IEnumerable<INamedTypeSymbol> SelfAndBases(INamedTypeSymbol type) => new[] { type }.Concat(BaseTypes(type));
}
