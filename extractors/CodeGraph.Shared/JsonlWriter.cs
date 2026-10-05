using System.Text.Json;
using System.Text.Json.Serialization.Metadata;

namespace CodeGraph.Shared;

/// <summary>Writes the per-assembly output files: &lt;assembly&gt;.nodes.jsonl, .edges.jsonl and .meta.json.</summary>
public static class JsonlWriter
{
    private static readonly JsonWriterOptions WriterOptions = new()
    {
        // Keep non-ASCII text (log messages, paths) readable instead of \u-escaping it.
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        Indented = false,
    };

    /// <summary>One JSON object per line. Written to a temp file and renamed, so readers never see half a file.</summary>
    public static void Write<T>(string path, IEnumerable<T> records, JsonTypeInfo<T> typeInfo)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var temp = path + ".tmp";
        using (var stream = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
        using (var writer = new Utf8JsonWriter(stream, WriterOptions))
        {
            foreach (var record in records)
            {
                JsonSerializer.Serialize(writer, record, typeInfo);
                writer.Flush();
                stream.WriteByte((byte)'\n');
                writer.Reset();
            }
        }

        File.Move(temp, path, overwrite: true);
    }

    /// <summary>Writes the records of one owner: nodes, edges and a small metadata file.</summary>
    public static void WriteOwner(string outDir, string owner, IEnumerable<NodeRecord> nodes, IEnumerable<EdgeRecord> edges,
        Dictionary<string, object?> meta)
    {
        var safe = SafeFileName(owner);
        Write(Path.Combine(outDir, safe + ".nodes.jsonl"), nodes, RecordJsonContext.Default.NodeRecord);
        Write(Path.Combine(outDir, safe + ".edges.jsonl"), edges, RecordJsonContext.Default.EdgeRecord);
        WriteJson(Path.Combine(outDir, safe + ".meta.json"), meta);
    }

    public static void WriteJson(string path, Dictionary<string, object?> value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(value, PrettyOptions));
        File.Move(temp, path, overwrite: true);
    }

    /// <summary>Owner names become file names; keep them readable but never let them escape the folder.</summary>
    public static string SafeFileName(string owner)
    {
        var invalid = Path.GetInvalidFileNameChars();
        return new string(owner.Select(c => invalid.Contains(c) || c == '/' || c == '\\' ? '_' : c).ToArray());
    }

    // Meta and manifest files are small and human-read: reflection-based, indented JSON is fine there.
    private static readonly JsonSerializerOptions PrettyOptions = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };
}
