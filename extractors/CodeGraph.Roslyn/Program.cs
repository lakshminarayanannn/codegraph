namespace CodeGraph.Roslyn;

/// <summary>Command-line entry point: parse options, run the extraction, map the outcome to an exit code.</summary>
internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        var options = Options.Parse(args, out var error);
        if (options is null)
        {
            Console.Error.WriteLine(error);
            Console.Error.WriteLine(Options.Usage);
            return ExitCodes.Usage;
        }

        try
        {
            return await Extraction.RunAsync(options);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[roslyn] fatal: {ex}");
            return ExitCodes.Fatal;
        }
    }
}

/// <summary>Exit codes the index scripts act on.</summary>
internal static class ExitCodes
{
    public const int Ok = 0;
    public const int Fatal = 1;
    public const int GateFailed = 3;   // output written, but a project exceeded max_compile_errors
    public const int Usage = 64;
}

/// <summary>Command-line options (SPEC 5.5).</summary>
internal sealed class Options
{
    public const string Usage = """
        usage: CodeGraph.Roslyn --config <codegraph.yaml> [--out <dir>] [--project <name>]...
                                [--run-id <id>] [--diagnostics fast|full] [--max-parallelism <n>]
                                [--list-projects] [--accuracy <sample size>] [--accuracy-seed <n>]
          --project        limit extraction to these projects (project or assembly name); repeatable
          --diagnostics    fast: parse + declaration errors; full: complete compile (slower)
          --accuracy       compare graph callers with Roslyn FindReferences for N random methods
        """;

    public string ConfigPath { get; private set; } = "";
    public string? OutDir { get; private set; }
    public List<string> Projects { get; } = new();
    public string? RunId { get; private set; }
    public string? Diagnostics { get; private set; }
    public int? MaxParallelism { get; private set; }
    public bool ListProjects { get; private set; }
    public int AccuracySample { get; private set; }
    public int AccuracySeed { get; private set; } = 42;

    public static Options? Parse(string[] args, out string error)
    {
        var options = new Options();
        error = "";
        for (var i = 0; i < args.Length; i++)
        {
            // Every option except the flags takes a value: read it and advance.
            string Next() => i + 1 < args.Length ? args[++i] : throw new ArgumentException($"{args[i]} needs a value");
            try
            {
                switch (args[i])
                {
                    case "--config": options.ConfigPath = Next(); break;
                    case "--out": options.OutDir = Next(); break;
                    case "--project": options.Projects.Add(Next()); break;
                    case "--run-id": options.RunId = Next(); break;
                    case "--diagnostics": options.Diagnostics = Next(); break;
                    case "--max-parallelism": options.MaxParallelism = int.Parse(Next()); break;
                    case "--list-projects": options.ListProjects = true; break;
                    case "--accuracy": options.AccuracySample = int.Parse(Next()); break;
                    case "--accuracy-seed": options.AccuracySeed = int.Parse(Next()); break;
                    default: error = $"unknown argument: {args[i]}"; return null;
                }
            }
            catch (Exception ex) when (ex is ArgumentException or FormatException)
            {
                error = ex.Message;
                return null;
            }
        }

        if (options.ConfigPath.Length == 0)
        {
            error = "--config is required";
            return null;
        }

        if (options.Diagnostics is not (null or "fast" or "full"))
        {
            error = "--diagnostics must be fast or full";
            return null;
        }

        return options;
    }
}
