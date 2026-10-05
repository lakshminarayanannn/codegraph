using System.Text.RegularExpressions;
using CodeGraph.Shared;

namespace CodeGraph.Bridge;

/// <summary>
/// Optional heuristic source locations for C++/CLI symbols (SPEC 7.3 step 6): metadata has no file or line,
/// so search the project's .cpp/.h files for "Class::Member(" definitions and "ref class Class" declarations.
/// Every location found this way is marked locationConfidence = heuristic.
/// </summary>
internal sealed partial class SourceLocator
{
    private readonly Dictionary<(string Type, string Member), (string File, int Line)> _members = new();
    private readonly Dictionary<string, (string File, int Line)> _types = new(StringComparer.Ordinal);

    public static SourceLocator Build(string sourceDir, string repoRoot)
    {
        var locator = new SourceLocator();
        if (!Directory.Exists(sourceDir))
        {
            return locator;
        }

        var files = Directory.EnumerateFiles(sourceDir, "*.*", SearchOption.AllDirectories)
            .Where(f => f.EndsWith(".cpp", StringComparison.OrdinalIgnoreCase) || f.EndsWith(".h", StringComparison.OrdinalIgnoreCase))
            .Order(StringComparer.Ordinal);
        foreach (var file in files)
        {
            var relative = Ids.RelativeTo(repoRoot, file);
            var lines = File.ReadAllLines(file);
            for (var i = 0; i < lines.Length; i++)
            {
                foreach (Match match in MemberDefinition().Matches(lines[i]))
                {
                    locator._members.TryAdd((match.Groups["type"].Value, match.Groups["member"].Value), (relative, i + 1));
                }

                if (TypeDeclaration().Match(lines[i]) is { Success: true } declaration)
                {
                    locator._types.TryAdd(declaration.Groups["type"].Value, (relative, i + 1));
                }
            }
        }

        return locator;
    }

    public (string File, int Line)? Find(string typeName, string? memberName)
    {
        if (memberName is null)
        {
            return _types.TryGetValue(typeName, out var type) ? type : null;
        }

        return _members.TryGetValue((typeName, memberName), out var member) ? member : null;
    }

    // "StageWrapper::Move(", "StageWrapper::~StageWrapper(", "StageWrapper::!StageWrapper("
    [GeneratedRegex(@"\b(?<type>\w+)::(?<member>[~!]?\w+)\s*\(")]
    private static partial Regex MemberDefinition();

    // "public ref class StageWrapper", "ref struct X", "value class Y", "interface class Z"
    [GeneratedRegex(@"\b(?:ref|value|interface)\s+(?:class|struct)\s+(?<type>\w+)")]
    private static partial Regex TypeDeclaration();
}
