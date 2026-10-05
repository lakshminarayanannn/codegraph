using System.Diagnostics;
using CodeGraph.Shared;

namespace CodeGraph.Bridge;

/// <summary>
/// Component C (SPEC 7): reads built C++/CLI (mixed-mode) assemblies and emits their managed symbols, the
/// managed-to-native calls the compiler compiled into IL, and which managed types wrap native classes.
/// </summary>
internal static class Program
{
    private const string Usage = """
        usage: CodeGraph.Bridge --config <codegraph.yaml> [--out <dir>] [--assembly <dll>]...
               CodeGraph.Bridge --ids-only --assembly <dll>      print every member's documentation ID
               CodeGraph.Bridge --list-native --assembly <dll>   print every pinvokeimpl method (Phase 0 spike)
          --assembly     override cppcli_assemblies from the config (repeatable)
          --ids-only     ID-equality check (SPEC 7.3 step 1): compare with the Roslyn extractor's IDs
          --list-native  how native targets appear in IL: name, ImplMap (module, import name), flags
        """;

    private static int Main(string[] args)
    {
        string? configPath = null, outDir = null;
        var assemblies = new List<string>();
        var idsOnly = false;
        var listNative = false;
        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--config" when i + 1 < args.Length: configPath = args[++i]; break;
                case "--out" when i + 1 < args.Length: outDir = args[++i]; break;
                case "--assembly" when i + 1 < args.Length: assemblies.Add(Path.GetFullPath(args[++i])); break;
                case "--ids-only": idsOnly = true; break;
                case "--list-native": listNative = true; break;
                default:
                    Console.Error.WriteLine($"unknown argument {args[i]}\n{Usage}");
                    return 64;
            }
        }

        try
        {
            if (listNative)
            {
                assemblies.ForEach(BridgeExtractor.ListNativeStubs);
                return 0;
            }

            if (idsOnly)
            {
                foreach (var id in assemblies.SelectMany(BridgeExtractor.ManagedIds).Order(StringComparer.Ordinal))
                {
                    Console.WriteLine(id);
                }

                return 0;
            }

            if (configPath is null)
            {
                Console.Error.WriteLine(Usage);
                return 64;
            }

            return Run(CodeGraphConfig.Load(configPath), outDir, assemblies);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[bridge] fatal: {ex}");
            return 1;
        }
    }

    private static int Run(CodeGraphConfig config, string? outDir, List<string> overrides)
    {
        var clock = Stopwatch.StartNew();
        var output = outDir is not null ? Path.GetFullPath(outDir) : Path.Combine(config.ResolvePath(config.OutputDir), "bridge");
        Directory.CreateDirectory(output);
        var targets = overrides.Count > 0
            ? overrides.Select(p => new CppCliAssembly(p, Path.GetFileNameWithoutExtension(p), null)).ToList()
            : config.CppCli;

        var reports = new List<Dictionary<string, object?>>();
        var warnings = new List<string>();
        foreach (var target in targets)
        {
            if (!File.Exists(target.Path))
            {
                warnings.Add($"{target.Path} not found: build the C++/CLI projects before indexing");
                Log($"warning: {target.Path} not found");
                continue;
            }

            var report = new BridgeExtractor(config, target, output).Extract();
            reports.Add(report);
            Log($"{report["owner"]}: {report["nodes"]} nodes, {report["edges"]} edges, " +
                $"{report["nativeCalls"]} native call(s), {report["indirectNativeCalls"]} indirect (calli)");
        }

        JsonlWriter.WriteJson(Path.Combine(output, "manifest.json"), new Dictionary<string, object?>
        {
            ["tool"] = "CodeGraph.Bridge",
            ["version"] = typeof(Program).Assembly.GetName().Version?.ToString(),
            ["decompilerVersion"] = typeof(ICSharpCode.Decompiler.Metadata.PEFile).Assembly.GetName().Version?.ToString(),
            ["runtime"] = Environment.Version.ToString(),
            ["finishedAt"] = DateTimeOffset.UtcNow.ToString("O"),
            ["durationSeconds"] = Math.Round(clock.Elapsed.TotalSeconds, 1),
            ["assemblies"] = reports,
            ["warnings"] = warnings,
        });
        return 0;
    }

    public static void Log(string message) => Console.Error.WriteLine($"[bridge {DateTime.Now:HH:mm:ss}] {message}");
}
