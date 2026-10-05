using System.Security.Cryptography;
using System.Text;

namespace CodeGraph.Shared;

/// <summary>
/// Stable node IDs (SPEC 4.1). Every extractor builds IDs only through these helpers, and the
/// Python side (codegraph_common/ids.py) implements the same rules, so all sources join on IDs.
/// </summary>
public static class Ids
{
    /// <summary>Managed symbol: "dn:" + documentation-comment ID (Roslyn and ILSpy produce the same string).</summary>
    public static string Dn(string documentationCommentId) => "dn:" + documentationCommentId;

    public static string Assembly(string name) => "asm:" + name;

    /// <summary>File IDs are repo-relative, forward slashes, lowercase (Windows paths are case-insensitive).</summary>
    public static string File(string repoRelativePath) => "file:" + NormalizePath(repoRelativePath).ToLowerInvariant();

    public static string Export(string dll, string entryPoint) => "exp:" + NormalizeDll(dll) + "!" + entryPoint;

    /// <summary>Same text logged from two methods gives two nodes: the emitting method is what we want to find.</summary>
    public static string Message(string template, string emitterId) => "msg:" + Sha1Hex(template + "|" + emitterId);

    public static string Config(string key) => "cfg:" + key;

    public static string NormalizePath(string path) => path.Replace('\\', '/');

    /// <summary>
    /// DllImport("StageNative"), ("StageNative.dll") and ("..\bin\StageNative.DLL") name the same module:
    /// keep the file name, lowercase it, and append ".dll" unless it already ends in .dll or .exe
    /// (the loader's probing appends .dll the same way).
    /// </summary>
    public static string NormalizeDll(string dll)
    {
        var name = System.IO.Path.GetFileName(NormalizePath(dll)).ToLowerInvariant();
        return name.EndsWith(".dll", StringComparison.Ordinal) || name.EndsWith(".exe", StringComparison.Ordinal)
            ? name
            : name + ".dll";
    }

    public static string Sha1Hex(string text) => Convert.ToHexStringLower(SHA1.HashData(Encoding.UTF8.GetBytes(text)));

    public static string Sha1Hex(byte[] data) => Convert.ToHexStringLower(SHA1.HashData(data));

    /// <summary>Repo-relative path with forward slashes; paths outside the repo keep their "../" prefix.</summary>
    public static string RelativeTo(string repoRoot, string fullPath) =>
        NormalizePath(System.IO.Path.GetRelativePath(repoRoot, fullPath));
}
