using System.Diagnostics;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.MSBuild;

namespace CodeGraph.Roslyn;

/// <summary>
/// Orchestrates a run: open each configured solution in its own MSBuildWorkspace (released before the
/// next one, SPEC 5.6), pick the projects to index, extract them in parallel, then write the manifest.
/// </summary>
internal static class Extraction
{
    public static async Task<int> RunAsync(Options options)
    {
        var startedAt = DateTimeOffset.UtcNow;
        var clock = Stopwatch.StartNew();
        var config = CodeGraphConfig.Load(options.ConfigPath);
        var settings = new Settings(config, options);
        var outDir = options.OutDir is { } dir
            ? Path.GetFullPath(dir)
            : Path.Combine(config.ResolvePath(config.OutputDir), "csharp");
        Directory.CreateDirectory(outDir);

        var loadReport = new List<Dictionary<string, object?>>();
        var projectReports = new List<Dictionary<string, object?>>();
        var skipped = new List<Dictionary<string, object?>>();
        var processedPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var entry in config.Solutions)
        {
            var solutionPath = config.ResolvePath(entry);
            using var workspace = CreateWorkspace(config, solutionPath, loadReport);
            Log($"loading {solutionPath}");
            var solution = await workspace.OpenSolutionAsync(solutionPath, new LoadProgress());
            var projects = ProjectSelection.Select(solution, settings, processedPaths, skipped);
            Log($"{projects.Count} C# project(s) selected from {Path.GetFileName(solutionPath)}");

            if (options.ListProjects)
            {
                projects.ForEach(p => Console.WriteLine($"{p.Name}\t{p.AssemblyName}\t{p.FilePath}"));
                continue;
            }

            if (options.AccuracySample > 0)
            {
                await Accuracy.RunAsync(solution, projects, settings, outDir, options.AccuracySample, options.AccuracySeed);
                continue;
            }

            var extractor = new ProjectExtractor(settings, solution, outDir);
            var parallel = new ParallelOptions { MaxDegreeOfParallelism = settings.ProjectParallelism };
            await Parallel.ForEachAsync(projects, parallel, async (project, ct) =>
            {
                var report = await extractor.ExtractAsync(project, ct);
                lock (projectReports)
                {
                    projectReports.Add(report);
                }
            });
        }

        if (options.ListProjects || options.AccuracySample > 0)
        {
            return ExitCodes.Ok;
        }

        var failed = projectReports
            .Where(r => (int)r["compileErrors"]! > config.MaxCompileErrors)
            .Select(r => (string)r["owner"]!)
            .Order(StringComparer.Ordinal)
            .ToList();
        WriteManifest(outDir, options, config, settings, startedAt, clock, projectReports, skipped, loadReport, failed);

        Log($"done in {clock.Elapsed.TotalSeconds:F1}s: {projectReports.Count} project(s), output in {outDir}");
        if (failed.Count > 0)
        {
            Log($"GATE FAILED: {failed.Count} project(s) exceed max_compile_errors={config.MaxCompileErrors}: {string.Join(", ", failed)}");
            Log("compile errors mean unresolved symbols and missing edges; see manifest.json for the first errors");
            return ExitCodes.GateFailed;
        }

        return ExitCodes.Ok;
    }

    private static MSBuildWorkspace CreateWorkspace(CodeGraphConfig config, string solutionPath, List<Dictionary<string, object?>> loadReport)
    {
        var properties = new Dictionary<string, string>(config.MsbuildProperties);
        // Legacy projects use $(SolutionDir) in paths; set it as Visual Studio would.
        properties.TryAdd("SolutionDir", Path.GetDirectoryName(solutionPath)! + Path.DirectorySeparatorChar);

        var workspace = MSBuildWorkspace.Create(properties);
        workspace.RegisterWorkspaceFailedHandler(args =>
        {
            lock (loadReport)
            {
                loadReport.Add(new Dictionary<string, object?>
                {
                    ["solution"] = solutionPath,
                    ["kind"] = args.Diagnostic.Kind.ToString(),
                    ["message"] = args.Diagnostic.Message,
                });
            }
        });
        return workspace;
    }

    private static void WriteManifest(string outDir, Options options, CodeGraphConfig config, Settings settings,
        DateTimeOffset startedAt, Stopwatch clock, List<Dictionary<string, object?>> projects,
        List<Dictionary<string, object?>> skipped, List<Dictionary<string, object?>> loadReport, List<string> failed)
    {
        var nodeCounts = new SortedDictionary<string, long>(StringComparer.Ordinal);
        var edgeCounts = new SortedDictionary<string, long>(StringComparer.Ordinal);
        foreach (var project in projects)
        {
            Accumulate(nodeCounts, (Dictionary<string, int>)project["nodeCounts"]!);
            Accumulate(edgeCounts, (Dictionary<string, int>)project["edgeCounts"]!);
        }

        var manifest = new Dictionary<string, object?>
        {
            ["tool"] = "CodeGraph.Roslyn",
            ["version"] = typeof(Extraction).Assembly.GetName().Version?.ToString(),
            ["roslynVersion"] = typeof(Compilation).Assembly.GetName().Version?.ToString(),
            ["runtime"] = Environment.Version.ToString(),
            ["os"] = System.Runtime.InteropServices.RuntimeInformation.OSDescription,
            ["runId"] = options.RunId,
            ["config"] = Path.GetFullPath(options.ConfigPath),
            ["repoRoot"] = settings.RepoRoot,
            ["solutions"] = config.Solutions.Select(config.ResolvePath).ToList(),
            ["onlyProjects"] = options.Projects.Count > 0 ? options.Projects : null,
            ["diagnosticsMode"] = settings.DiagnosticsMode,
            ["startedAt"] = startedAt.ToString("O"),
            ["finishedAt"] = DateTimeOffset.UtcNow.ToString("O"),
            ["durationSeconds"] = Math.Round(clock.Elapsed.TotalSeconds, 1),
            ["projects"] = projects.OrderBy(p => (string)p["owner"]!, StringComparer.Ordinal)
                .Select(p => p.Where(kv => kv.Key != "files").ToDictionary(kv => kv.Key, kv => kv.Value)).ToList(),
            ["skippedProjects"] = skipped,
            ["workspaceDiagnostics"] = loadReport.Count,
            ["counts"] = new Dictionary<string, object?> { ["nodes"] = nodeCounts, ["edges"] = edgeCounts },
            ["gate"] = new Dictionary<string, object?> { ["maxCompileErrors"] = config.MaxCompileErrors, ["failedProjects"] = failed },
        };
        JsonlWriter.WriteJson(Path.Combine(outDir, "manifest.json"), manifest);
        JsonlWriter.WriteJson(Path.Combine(outDir, "load_report.json"), new Dictionary<string, object?> { ["diagnostics"] = loadReport });
    }

    private static void Accumulate(SortedDictionary<string, long> total, Dictionary<string, int> part)
    {
        foreach (var (key, value) in part)
        {
            total[key] = total.GetValueOrDefault(key) + value;
        }
    }

    public static void Log(string message) => Console.Error.WriteLine($"[roslyn {DateTime.Now:HH:mm:ss}] {message}");

    /// <summary>Prints one line per project evaluation, so a slow load on a big solution shows where time goes.</summary>
    private sealed class LoadProgress : IProgress<ProjectLoadProgress>
    {
        public void Report(ProjectLoadProgress value)
        {
            if (value.Operation == ProjectLoadOperation.Resolve)
            {
                var tfm = value.TargetFramework is { } t ? $" ({t})" : "";
                Log($"  loaded {Path.GetFileName(value.FilePath)}{tfm} in {value.ElapsedTime.TotalSeconds:F1}s");
            }
        }
    }
}
