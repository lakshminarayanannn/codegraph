using System.Diagnostics;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;

namespace CodeGraph.Roslyn;

/// <summary>
/// Extracts one project: compile, count errors, run the symbol pass, run the body pass over all
/// syntax trees in parallel, and write &lt;assembly&gt;.nodes.jsonl / .edges.jsonl / .meta.json.
/// </summary>
internal sealed class ProjectExtractor(Settings settings, Solution solution, string outDir)
{
    public async Task<Dictionary<string, object?>> ExtractAsync(Project project, CancellationToken cancellationToken)
    {
        var clock = Stopwatch.StartNew();
        var compilation = await project.GetCompilationAsync(cancellationToken)
                          ?? throw new InvalidOperationException($"no compilation for {project.Name}");
        var graph = new GraphBuilder(settings, compilation);

        var (errorCount, firstErrors) = CompileErrors(compilation, settings.DiagnosticsMode, cancellationToken);
        EmitAssembly(project, compilation, graph);
        var files = EmitFiles(compilation, graph);

        new SymbolPass(graph).Run(cancellationToken);

        // Binding method bodies is the expensive part: one semantic model per tree, trees in parallel.
        var invalid = 0;
        var parallel = new ParallelOptions { MaxDegreeOfParallelism = settings.Parallelism, CancellationToken = cancellationToken };
        Parallel.ForEach(compilation.SyntaxTrees, parallel, tree =>
        {
            var pass = new BodyPass(graph, compilation.GetSemanticModel(tree));
            pass.Run(cancellationToken);
            Interlocked.Add(ref invalid, pass.InvalidOperations);
        });

        // Fast mode cannot see body-level errors directly; operations that failed to bind stand in for them.
        if (settings.DiagnosticsMode != "full")
        {
            errorCount += invalid;
        }

        var nodes = graph.Nodes.Drain();
        var edges = graph.Edges.Drain();
        var meta = new Dictionary<string, object?>
        {
            ["owner"] = graph.AssemblyName,
            ["project"] = ProjectSelection.BaseName(project.Name),
            ["projectPath"] = graph.RelativePath(project.FilePath),
            ["language"] = "cs",
            ["documents"] = files.Count,
            ["compileErrors"] = errorCount,
            ["unresolvedOperations"] = invalid,
            ["diagnosticsMode"] = settings.DiagnosticsMode,
            ["firstErrors"] = firstErrors,
            ["nodes"] = nodes.Count,
            ["edges"] = edges.Count,
            ["nodeCounts"] = nodes.GroupBy(n => n.Label).ToDictionary(g => g.Key, g => g.Count()),
            ["edgeCounts"] = edges.GroupBy(e => e.Type).ToDictionary(g => g.Key, g => g.Count()),
            ["seconds"] = Math.Round(clock.Elapsed.TotalSeconds, 2),
            ["extractedAt"] = DateTimeOffset.UtcNow.ToString("O"),
            ["files"] = files,
        };
        JsonlWriter.WriteOwner(outDir, graph.AssemblyName, nodes, edges, meta);

        var status = errorCount > settings.Config.MaxCompileErrors ? $"  ** {errorCount} compile error(s) **" : "";
        Extraction.Log($"  {graph.AssemblyName}: {nodes.Count} nodes, {edges.Count} edges in {clock.Elapsed.TotalSeconds:F1}s{status}");
        return meta;
    }

    /// <summary>
    /// Error-severity diagnostics. fast = parse + declaration diagnostics (cheap); full = a complete
    /// compile, which binds every method body a second time (SPEC 5.1 gate).
    /// </summary>
    private static (int Count, List<string> First) CompileErrors(Compilation compilation, string mode, CancellationToken cancellationToken)
    {
        var diagnostics = mode == "full"
            ? compilation.GetDiagnostics(cancellationToken)
            : compilation.GetParseDiagnostics(cancellationToken).AddRange(compilation.GetDeclarationDiagnostics(cancellationToken));
        var errors = diagnostics.Where(d => d.Severity == DiagnosticSeverity.Error).ToList();
        return (errors.Count, errors.Take(20).Select(d => d.ToString()).ToList());
    }

    private void EmitAssembly(Project project, Compilation compilation, GraphBuilder graph)
    {
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Assembly",
            Id = graph.AssemblyId,
            Name = graph.AssemblyName,
            Fqn = graph.AssemblyName,
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = graph.RelativePath(project.FilePath),
            Kind = "csharp",
            TargetFramework = TargetFramework(project, compilation),
            IsTestAssembly = graph.Rules.IsTestAssembly,
        });

        // Direct project references (listed in the project file), transitive ones (SDK-style projects flow
        // them), and binary references to assemblies this run also indexes (C++/CLI, other solutions).
        var projectFile = graph.RelativePath(project.FilePath);
        var direct = DirectProjectReferences(project.FilePath);
        var linked = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var reference in project.ProjectReferences)
        {
            if (solution.GetProject(reference.ProjectId) is { } referenced)
            {
                var kind = direct.Contains(Path.GetFileName(referenced.FilePath ?? "")) ? "project" : "transitive";
                graph.AddEdge("REFERENCES", graph.AssemblyId, Ids.Assembly(referenced.AssemblyName), projectFile, null, kind: kind);
                linked.Add(referenced.AssemblyName);
            }
        }

        var indexed = new HashSet<string>(solution.Projects.Select(p => p.AssemblyName), StringComparer.OrdinalIgnoreCase);
        indexed.UnionWith(settings.CppCliNames);
        foreach (var reference in compilation.References)
        {
            if (compilation.GetAssemblyOrModuleSymbol(reference) is IAssemblySymbol assembly
                && indexed.Contains(assembly.Name) && !linked.Contains(assembly.Name) && assembly.Name != graph.AssemblyName)
            {
                graph.AddEdge("REFERENCES", graph.AssemblyId, Ids.Assembly(assembly.Name), projectFile, null, kind: "binary");
            }
        }
    }

    /// <summary>File names of the &lt;ProjectReference Include="..."&gt; items written in the project file itself.</summary>
    private static HashSet<string> DirectProjectReferences(string? projectPath)
    {
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (projectPath is null || !File.Exists(projectPath))
        {
            return names;
        }

        try
        {
            var document = System.Xml.Linq.XDocument.Load(projectPath);
            foreach (var item in document.Descendants().Where(e => e.Name.LocalName == "ProjectReference"))
            {
                if (item.Attribute("Include")?.Value is { } include)
                {
                    names.Add(Path.GetFileName(include.Replace('\\', '/')));
                }
            }
        }
        catch (System.Xml.XmlException)
        {
            // Unreadable project file: every reference is reported as transitive rather than failing the run.
        }

        return names;
    }

    /// <summary>One :File per syntax tree (including generated ones under obj/), with a content hash.</summary>
    private static List<string> EmitFiles(Compilation compilation, GraphBuilder graph)
    {
        var files = new List<string>();
        foreach (var tree in compilation.SyntaxTrees)
        {
            var path = graph.RelativePath(tree.FilePath);
            if (path.Length == 0)
            {
                continue;
            }

            files.Add(path);
            var text = tree.GetText();
            var generated = IsGenerated(path);
            graph.Nodes.Add(new NodeRecord
            {
                Label = "File",
                Id = Ids.File(path),
                Name = Path.GetFileName(path),
                Fqn = path,
                Path = path,
                Lang = "cs",
                Assembly = graph.AssemblyName,
                File = path,
                LineStart = 1,
                LineEnd = text.Lines.Count,
                // Generated files change with every build (versions, commit hashes): their hash means nothing.
                Sha1 = generated ? null : Ids.Sha1Hex(text.ToString()),
                Generated = generated ? true : null,
            });
        }

        files.Sort(StringComparer.Ordinal);
        return files;
    }

    private static bool IsGenerated(string path) =>
        path.StartsWith("../", StringComparison.Ordinal)
        || path.Contains("/obj/", StringComparison.OrdinalIgnoreCase)
        || path.StartsWith("obj/", StringComparison.OrdinalIgnoreCase)
        || path.EndsWith(".g.cs", StringComparison.OrdinalIgnoreCase)
        || path.EndsWith(".g.i.cs", StringComparison.OrdinalIgnoreCase);

    /// <summary>From [assembly: TargetFramework(".NETFramework,Version=v4.8")], else the "(net48)" project name suffix.</summary>
    private static string? TargetFramework(Project project, Compilation compilation)
    {
        var attribute = compilation.Assembly.GetAttributes()
            .FirstOrDefault(a => a.AttributeClass?.ToDisplayString() == "System.Runtime.Versioning.TargetFrameworkAttribute");
        if (attribute?.ConstructorArguments.FirstOrDefault().Value is string framework)
        {
            return framework;
        }

        var open = project.Name.LastIndexOf('(');
        return open > 0 && project.Name.EndsWith(')') ? project.Name[(open + 1)..^1] : null;
    }
}
