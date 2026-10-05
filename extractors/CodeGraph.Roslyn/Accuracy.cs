using System.Text.Json;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.FindSymbols;

namespace CodeGraph.Roslyn;

/// <summary>
/// Accuracy check (SPEC 11.2), automated: for N random methods, compare the graph's depth-1 callers
/// (CALLS / REFERENCES_METHOD, plus callers of the interface or base methods it implements, i.e.
/// what DISPATCHES_TO adds) with Roslyn's SymbolFinder.FindReferencesAsync - the engine behind
/// Visual Studio's "Find All References". Reads the extracted JSONL, so Neo4j is not needed.
/// </summary>
internal static class Accuracy
{
    public static async Task RunAsync(Solution solution, List<Project> projects, Settings settings, string outDir, int sampleSize, int seed)
    {
        var graph = LoadGraph(outDir);
        var candidates = new List<(IMethodSymbol Method, Project Project)>();
        foreach (var project in projects)
        {
            var compilation = await project.GetCompilationAsync();
            if (compilation is null)
            {
                continue;
            }

            foreach (var method in AllMethods(compilation.Assembly.GlobalNamespace))
            {
                candidates.Add((method, project));
            }
        }

        // Stable order before sampling so the same seed always picks the same methods.
        candidates.Sort((a, b) => string.CompareOrdinal(a.Method.GetDocumentationCommentId(), b.Method.GetDocumentationCommentId()));
        var random = new Random(seed);
        var sample = candidates.OrderBy(_ => random.Next()).Take(sampleSize).ToList();

        var rows = new List<Dictionary<string, object?>>();
        int truePositives = 0, graphTotal = 0, roslynTotal = 0;
        foreach (var (method, project) in sample)
        {
            var id = Ids.Dn(method.GetDocumentationCommentId()!);
            var expected = await RoslynCallersAsync(method, solution, settings);
            var actual = graph.CallersOf(id);
            var hits = actual.Intersect(expected).Count();
            truePositives += hits;
            graphTotal += actual.Count;
            roslynTotal += expected.Count;
            rows.Add(new Dictionary<string, object?>
            {
                ["method"] = id,
                ["project"] = project.Name,
                ["roslyn"] = expected.Count,
                ["graph"] = actual.Count,
                ["matched"] = hits,
                ["missing"] = expected.Except(actual).Order(StringComparer.Ordinal).ToList(),
                ["extra"] = actual.Except(expected).Order(StringComparer.Ordinal).ToList(),
            });
        }

        var precision = graphTotal == 0 ? 1.0 : (double)truePositives / graphTotal;
        var recall = roslynTotal == 0 ? 1.0 : (double)truePositives / roslynTotal;
        JsonlWriter.WriteJson(Path.Combine(outDir, "accuracy.json"), new Dictionary<string, object?>
        {
            ["sampleSize"] = sample.Count,
            ["seed"] = seed,
            ["precision"] = Math.Round(precision, 4),
            ["recall"] = Math.Round(recall, 4),
            ["methods"] = rows,
        });
        Extraction.Log($"accuracy: {sample.Count} methods, precision {precision:P1}, recall {recall:P1} (gate: recall >= 98%)");
        Extraction.Log($"details: {Path.Combine(outDir, "accuracy.json")}");
    }

    /// <summary>Members that call or reference the method according to Roslyn (call sites mapped to their containing member).</summary>
    private static async Task<HashSet<string>> RoslynCallersAsync(IMethodSymbol method, Solution solution, Settings settings)
    {
        var callers = new HashSet<string>(StringComparer.Ordinal);
        var references = await SymbolFinder.FindReferencesAsync(method, solution);

        // FindReferences cascades both up (interface/base declarations) and down (overrides). The graph's
        // depth-1 callers go up only (that is what DISPATCHES_TO adds), so keep the "up" definitions -
        // computed from Roslyn symbols, not from the graph, so a missing graph edge cannot hide itself.
        var definitions = UpChain(method);
        foreach (var location in references
                     .Where(r => r.Definition.OriginalDefinition.GetDocumentationCommentId() is { } doc && definitions.Contains(doc))
                     .SelectMany(r => r.Locations))
        {
            if (location.IsCandidateLocation || location.Location.SourceTree is not { } tree)
            {
                continue;
            }

            var node = (await tree.GetRootAsync()).FindNode(location.Location.SourceSpan, getInnermostNodeForTie: true);
            if (node.AncestorsAndSelf().Any(n => n is CrefSyntax or XmlNodeSyntax)
                || node.AncestorsAndSelf().OfType<InvocationExpressionSyntax>().Any(i => i.Expression is IdentifierNameSyntax { Identifier.Text: "nameof" }))
            {
                continue; // doc-comment crefs and nameof(...) are not uses
            }

            var model = await location.Document.GetSemanticModelAsync();
            foreach (var owner in Owners(model!.GetEnclosingSymbol(location.Location.SourceSpan.Start), settings, model.Compilation))
            {
                callers.Add(owner);
            }
        }

        return callers;
    }

    /// <summary>The method, the methods it overrides, and the interface methods any of those implement.</summary>
    private static HashSet<string> UpChain(IMethodSymbol method)
    {
        var result = new HashSet<string>(StringComparer.Ordinal);
        for (var current = method; current is not null; current = current.OverriddenMethod?.OriginalDefinition)
        {
            result.Add(current.GetDocumentationCommentId()!);
            foreach (var iface in current.ContainingType.AllInterfaces)
            {
                foreach (var member in iface.GetMembers().OfType<IMethodSymbol>())
                {
                    if (SymbolEqualityComparer.Default.Equals(current.ContainingType.FindImplementationForInterfaceMember(member)?.OriginalDefinition, current)
                        && member.OriginalDefinition.GetDocumentationCommentId() is { } doc)
                    {
                        result.Add(doc);
                    }
                }
            }
        }

        return result;
    }

    /// <summary>Maps an enclosing symbol to graph owners the way the body pass attributes code.</summary>
    private static IEnumerable<string> Owners(ISymbol? enclosing, Settings settings, Compilation compilation)
    {
        // Lambdas and local functions are attributed to the member that contains them.
        while (enclosing is IMethodSymbol { MethodKind: MethodKind.AnonymousFunction or MethodKind.LocalFunction })
        {
            enclosing = enclosing.ContainingSymbol;
        }

        switch (enclosing)
        {
            case IMethodSymbol method when method.GetDocumentationCommentId() is { } doc:
                yield return Ids.Dn(doc);
                break;
            case IFieldSymbol or IPropertySymbol when enclosing.ContainingType is { } type:
                // Initializers run in the constructors (GraphBuilder.InitializerOwners).
                var builder = new GraphBuilder(settings, compilation);
                foreach (var owner in builder.InitializerOwners(type, enclosing.IsStatic))
                {
                    yield return owner;
                }

                break;
        }
    }

    private static IEnumerable<IMethodSymbol> AllMethods(INamespaceSymbol ns)
    {
        foreach (var member in ns.GetMembers())
        {
            if (member is INamespaceSymbol child)
            {
                foreach (var method in AllMethods(child))
                {
                    yield return method;
                }
            }
            else if (member is INamedTypeSymbol type)
            {
                foreach (var method in TypeMethods(type))
                {
                    yield return method;
                }
            }
        }
    }

    private static IEnumerable<IMethodSymbol> TypeMethods(INamedTypeSymbol type)
    {
        foreach (var member in type.GetMembers())
        {
            if (member is IMethodSymbol { MethodKind: MethodKind.Ordinary or MethodKind.Constructor, IsImplicitlyDeclared: false } method
                && method.GetDocumentationCommentId() is not null)
            {
                yield return method;
            }
            else if (member is INamedTypeSymbol nested)
            {
                foreach (var inner in TypeMethods(nested))
                {
                    yield return inner;
                }
            }
        }
    }

    private static GraphIndex LoadGraph(string outDir)
    {
        var index = new GraphIndex();
        foreach (var file in Directory.EnumerateFiles(outDir, "*.edges.jsonl"))
        {
            foreach (var line in File.ReadLines(file))
            {
                var edge = JsonSerializer.Deserialize(line, RecordJsonContext.Default.EdgeRecord)!;
                index.Add(edge);
            }
        }

        return index;
    }

    /// <summary>The slice of the graph the check needs: callers per target, and dispatch declarations per method.</summary>
    private sealed class GraphIndex
    {
        private readonly Dictionary<string, HashSet<string>> _callers = new(StringComparer.Ordinal);
        private readonly Dictionary<string, HashSet<string>> _declarations = new(StringComparer.Ordinal);

        public void Add(EdgeRecord edge)
        {
            switch (edge.Type)
            {
                case "CALLS" or "REFERENCES_METHOD":
                    Get(_callers, edge.End).Add(edge.Start);
                    break;
                case "IMPLEMENTS_METHOD" or "OVERRIDES":
                    Get(_declarations, edge.Start).Add(edge.End);
                    break;
            }
        }

        /// <summary>Direct callers plus callers of every declaration this method implements or overrides (transitively).</summary>
        public HashSet<string> CallersOf(string id)
        {
            var result = new HashSet<string>(StringComparer.Ordinal);
            var pending = new Stack<string>(new[] { id });
            var seen = new HashSet<string>(StringComparer.Ordinal);
            while (pending.Count > 0)
            {
                var current = pending.Pop();
                if (!seen.Add(current))
                {
                    continue;
                }

                if (_callers.TryGetValue(current, out var callers))
                {
                    result.UnionWith(callers);
                }

                if (_declarations.TryGetValue(current, out var declarations))
                {
                    foreach (var declaration in declarations)
                    {
                        pending.Push(declaration);
                    }
                }
            }

            return result;
        }

        private static HashSet<string> Get(Dictionary<string, HashSet<string>> map, string key)
        {
            if (!map.TryGetValue(key, out var set))
            {
                map[key] = set = new HashSet<string>(StringComparer.Ordinal);
            }

            return set;
        }
    }
}
