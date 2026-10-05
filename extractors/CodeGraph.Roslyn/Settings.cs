using CodeGraph.Shared;

namespace CodeGraph.Roslyn;

/// <summary>Everything the extractor needs from config and the command line, resolved once.</summary>
internal sealed class Settings
{
    // Test attributes recognized without configuration (SPEC 5.7).
    private static readonly string[] DefaultTestAttributes =
    {
        "NUnit.Framework.TestAttribute",
        "NUnit.Framework.TestCaseAttribute",
        "NUnit.Framework.TestCaseSourceAttribute",
        "NUnit.Framework.TheoryAttribute",
        "Microsoft.VisualStudio.TestTools.UnitTesting.TestMethodAttribute",
        "Microsoft.VisualStudio.TestTools.UnitTesting.DataTestMethodAttribute",
        "Xunit.FactAttribute",
        "Xunit.TheoryAttribute",
    };

    // Assemblies whose presence in the references makes an assembly a test assembly.
    public static readonly string[] TestFrameworkAssemblies =
    {
        "nunit.framework",
        "Microsoft.VisualStudio.TestPlatform.TestFramework",
        "Microsoft.VisualStudio.QualityTools.UnitTestFramework",
        "xunit.core",
        "xunit.v3.core",
    };

    public Settings(CodeGraphConfig config, Options options)
    {
        Config = config;
        RepoRoot = config.RepoRootFull;
        DiagnosticsMode = options.Diagnostics ?? config.Roslyn.Diagnostics;
        Parallelism = options.MaxParallelism ?? (config.Roslyn.MaxParallelism > 0 ? config.Roslyn.MaxParallelism : Environment.ProcessorCount);
        OnlyProjects = options.Projects;
        CppCliNames = new HashSet<string>(config.CppCli.Select(c => c.Name), StringComparer.OrdinalIgnoreCase);
        TestAttributes = new HashSet<string>(DefaultTestAttributes.Concat(config.TestAttributes), StringComparer.Ordinal);
        EntryPointAttributes = config.EntryPointAttributes
            .GroupBy(r => r.Attribute, StringComparer.Ordinal)
            .ToDictionary(g => g.Key, g => g.First().Kind, StringComparer.Ordinal);
        UiEventBaseTypes = new HashSet<string>(config.UiEventBaseTypes, StringComparer.Ordinal);
    }

    public CodeGraphConfig Config { get; }
    public string RepoRoot { get; }
    public string DiagnosticsMode { get; }
    public int Parallelism { get; }
    public List<string> OnlyProjects { get; }

    /// <summary>Assemblies built from C++/CLI: the bridge extractor owns their nodes (SPEC 5.3, last paragraph).</summary>
    public HashSet<string> CppCliNames { get; }

    public HashSet<string> TestAttributes { get; }
    public Dictionary<string, string> EntryPointAttributes { get; }
    public HashSet<string> UiEventBaseTypes { get; }

    /// <summary>Projects are processed concurrently, and each also binds its documents in parallel.</summary>
    public int ProjectParallelism => Math.Max(1, Math.Min(4, Parallelism / 2));
}
