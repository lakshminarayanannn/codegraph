using System.Text.RegularExpressions;
using CodeGraph.Shared;
using Microsoft.CodeAnalysis;

namespace CodeGraph.Roslyn;

/// <summary>Chooses which projects of a loaded solution to extract, and records why others were skipped.</summary>
internal static partial class ProjectSelection
{
    public static List<Project> Select(Solution solution, Settings settings, HashSet<string> processedPaths,
        List<Dictionary<string, object?>> skipped)
    {
        var selected = new List<Project>();

        // A multi-targeted project appears once per target framework, all with the same file path.
        var byFile = solution.Projects
            .Where(p => p.FilePath is not null)
            .GroupBy(p => p.FilePath!, StringComparer.OrdinalIgnoreCase)
            .OrderBy(g => g.Key, StringComparer.OrdinalIgnoreCase);

        foreach (var group in byFile)
        {
            var project = PickTargetFramework(group, settings.Config.Roslyn.PreferTargetFramework);
            var baseName = BaseName(project.Name);

            if (processedPaths.Contains(group.Key))
            {
                continue; // shared between solutions: extracted once (SPEC 5.1)
            }

            string? reason = null;
            if (project.Language != LanguageNames.CSharp)
            {
                reason = $"language {project.Language} is not indexed by the Roslyn extractor";
            }
            else if (settings.CppCliNames.Contains(project.AssemblyName))
            {
                reason = "C++/CLI assembly: indexed by the bridge extractor";
            }
            else if (Glob.AnyMatch(settings.Config.Roslyn.ExcludeProjects, baseName)
                     || Glob.AnyMatch(settings.Config.Roslyn.ExcludeProjects, project.AssemblyName))
            {
                reason = "excluded by roslyn.exclude_projects";
            }

            if (reason is not null)
            {
                skipped.Add(new Dictionary<string, object?> { ["project"] = project.Name, ["path"] = group.Key, ["reason"] = reason });
                continue;
            }

            if (settings.OnlyProjects.Count > 0 && !settings.OnlyProjects.Any(n =>
                    string.Equals(n, baseName, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(n, project.AssemblyName, StringComparison.OrdinalIgnoreCase)))
            {
                continue; // --project filter (incremental runs)
            }

            processedPaths.Add(group.Key);
            selected.Add(project);
        }

        return selected;
    }

    /// <summary>"Foo(net48)" -> "Foo": the name MSBuildWorkspace gives one target of a multi-targeted project.</summary>
    public static string BaseName(string projectName) => TfmSuffix().Replace(projectName, "");

    private static Project PickTargetFramework(IGrouping<string, Project> group, string preferred)
    {
        var ordered = group.OrderBy(p => p.Name, StringComparer.Ordinal).ToList();
        if (preferred.Length > 0)
        {
            var match = ordered.FirstOrDefault(p => p.Name.EndsWith($"({preferred})", StringComparison.OrdinalIgnoreCase));
            if (match is not null)
            {
                return match;
            }
        }

        return ordered[0];
    }

    [GeneratedRegex(@"\([^()]+\)$")]
    private static partial Regex TfmSuffix();
}
