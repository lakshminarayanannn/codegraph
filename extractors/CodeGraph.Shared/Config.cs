using System.Text.RegularExpressions;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace CodeGraph.Shared;

/// <summary>
/// The parts of config/codegraph.yaml the C# extractors need. Sections used only by the Python
/// tools (neo4j, git_history, ...) are ignored here. Relative paths resolve against the config file's folder.
/// </summary>
public sealed class CodeGraphConfig
{
    public string RepoRoot { get; set; } = ".";
    public List<string> Solutions { get; set; } = new();
    public Dictionary<string, string> MsbuildProperties { get; set; } = new();
    public int MaxCompileErrors { get; set; }
    public string OutputDir { get; set; } = "out";
    public RoslynSettings Roslyn { get; set; } = new();
    public List<object> CppcliAssemblies { get; set; } = new();
    public List<LogMethodRule> LogMethods { get; set; } = new();
    public List<ConfigAccessorRule> ConfigAccessors { get; set; } = new();
    public List<string> TestAttributes { get; set; } = new();
    public List<string> TestAssemblyPatterns { get; set; } = new();
    public List<EntryPointAttributeRule> EntryPointAttributes { get; set; } = new();
    public List<string> UiEventBaseTypes { get; set; } = new();

    [YamlIgnore] public string ConfigDirectory { get; private set; } = "";

    /// <summary>C++/CLI assemblies, normalized from either "path" strings or {path, name, source_dir} maps.</summary>
    [YamlIgnore] public List<CppCliAssembly> CppCli { get; private set; } = new();

    [YamlIgnore] public string RepoRootFull => ResolvePath(RepoRoot);

    public static CodeGraphConfig Load(string path)
    {
        var deserializer = new DeserializerBuilder()
            .WithNamingConvention(UnderscoredNamingConvention.Instance)
            .IgnoreUnmatchedProperties()
            .Build();
        var fullPath = Path.GetFullPath(path);
        var config = deserializer.Deserialize<CodeGraphConfig>(File.ReadAllText(fullPath)) ?? new CodeGraphConfig();
        config.ConfigDirectory = Path.GetDirectoryName(fullPath)!;
        config.CppCli = config.CppcliAssemblies.Select(config.ParseCppCli).ToList();
        return config;
    }

    public string ResolvePath(string path) =>
        Path.GetFullPath(Path.IsPathRooted(path) ? path : Path.Combine(ConfigDirectory, path));

    private CppCliAssembly ParseCppCli(object item)
    {
        // YAML gives either a scalar ("bin/X.dll") or a mapping ({path: ..., name: ..., source_dir: ...}).
        if (item is string scalar)
        {
            var full = ResolvePath(scalar);
            return new CppCliAssembly(full, Path.GetFileNameWithoutExtension(full), null);
        }

        var map = ((Dictionary<object, object>)item).ToDictionary(kv => kv.Key.ToString()!, kv => kv.Value?.ToString());
        var path = ResolvePath(map["path"]!);
        var name = map.GetValueOrDefault("name") ?? Path.GetFileNameWithoutExtension(path);
        var source = map.GetValueOrDefault("source_dir") is { } dir ? ResolvePath(dir) : null;
        return new CppCliAssembly(path, name, source);
    }
}

public sealed class RoslynSettings
{
    public int MaxParallelism { get; set; }
    public string Diagnostics { get; set; } = "fast";
    public List<string> ExcludeProjects { get; set; } = new();
    public string PreferTargetFramework { get; set; } = "";
}

public sealed record CppCliAssembly(string Path, string Name, string? SourceDir);

/// <summary>A configured logging API (SPEC 5.4, 13).</summary>
public sealed class LogMethodRule
{
    public string? Type { get; set; }              // exact containing type
    public string? TypeImplements { get; set; }    // containing type, a base type or an implemented interface
    public List<string> Methods { get; set; } = new();
    public int TemplateArg { get; set; }           // parameter ordinal of the message/format argument
    public string LevelFrom { get; set; } = "method"; // method | arg | fixed
    public int? LevelArg { get; set; }
    public string? Level { get; set; }
    public string? TemplateStyle { get; set; }     // literal | composite (default: composite if name ends in "Format")
}

/// <summary>A configured configuration accessor (SPEC 5.3, 13).</summary>
public sealed class ConfigAccessorRule
{
    public string Member { get; set; } = "";
    public string Kind { get; set; } = "indexer";  // indexer | method | property
    public int KeyArg { get; set; }
}

public sealed class EntryPointAttributeRule
{
    public string Attribute { get; set; } = "";
    public string Kind { get; set; } = "plugin";
}

/// <summary>Glob patterns from config ("*.Tests", "MiniPlant.*") matched case-insensitively.</summary>
public static class Glob
{
    public static bool IsMatch(string pattern, string text) =>
        Regex.IsMatch(text, "^" + Regex.Escape(pattern).Replace(@"\*", ".*").Replace(@"\?", ".") + "$",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    public static bool AnyMatch(IEnumerable<string> patterns, string text) => patterns.Any(p => IsMatch(p, text));
}
