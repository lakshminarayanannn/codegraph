using System.Collections.Concurrent;

namespace CodeGraph.Shared;

/// <summary>
/// Collects the edges of one owner (assembly) from many threads and merges repeats:
/// "A calls B on lines 10, 14 and 30" becomes one CALLS edge with line=10, lines=[10,14,30], count=3.
/// One edge per pair keeps traversals fast and path counts meaningful (docs/DECISIONS.md, D-07).
/// </summary>
public sealed class EdgeAggregator
{
    private readonly ConcurrentDictionary<EdgeKey, Entry> _edges = new();

    /// <summary>Identity of an edge. Discriminator separates otherwise equal edges (e.g. two handlers of one event).</summary>
    private readonly record struct EdgeKey(string Type, string Start, string End, string Kind, string Via, string Discriminator);

    private sealed class Entry(EdgeRecord first)
    {
        public EdgeRecord Record = first;
        public readonly List<int> Lines = new();
        public int Count;
    }

    public int Count => _edges.Count;

    public void Add(EdgeRecord edge, string? discriminator = null)
    {
        var key = new EdgeKey(edge.Type, edge.Start, edge.End, edge.Kind ?? "", edge.Via ?? "", discriminator ?? "");
        var entry = _edges.GetOrAdd(key, _ => new Entry(edge));
        lock (entry)
        {
            entry.Count++;
            if (edge.Line is > 0)
            {
                entry.Lines.Add(edge.Line.Value);
            }

            // Threads arrive in any order; keeping the earliest location makes the output deterministic.
            if (!ReferenceEquals(entry.Record, edge))
            {
                entry.Record = Merge(entry.Record, edge);
            }
        }
    }

    /// <summary>Returns the merged edges sorted by key, so two runs over the same code write identical files.</summary>
    public List<EdgeRecord> Drain()
    {
        var result = new List<EdgeRecord>(_edges.Count);
        foreach (var (key, entry) in _edges.OrderBy(e => e.Key.Type, StringComparer.Ordinal)
                     .ThenBy(e => e.Key.Start, StringComparer.Ordinal)
                     .ThenBy(e => e.Key.End, StringComparer.Ordinal)
                     .ThenBy(e => e.Key.Kind, StringComparer.Ordinal)
                     .ThenBy(e => e.Key.Via, StringComparer.Ordinal)
                     .ThenBy(e => e.Key.Discriminator, StringComparer.Ordinal))
        {
            var record = entry.Record;
            var lines = entry.Lines.Distinct().Order().ToArray();
            if (lines.Length > 0 && record.File is not null)
            {
                // Line numbers only make sense within one file; the record keeps its earliest file.
                record.Line = lines[0];
            }

            record.Lines = lines.Length > 1 ? lines : null;
            record.Count = entry.Count > 1 ? entry.Count : null;
            result.Add(record);
        }

        return result;
    }

    private static EdgeRecord Merge(EdgeRecord kept, EdgeRecord incoming)
    {
        var earliest = IsEarlier(incoming, kept) ? incoming : kept;
        var other = ReferenceEquals(earliest, kept) ? incoming : kept;

        // Optional facts are OR-ed: if any occurrence was a native callback or a UI event, the edge is.
        earliest.CallbackKind ??= other.CallbackKind;
        earliest.UiEvent ??= other.UiEvent;
        earliest.Handler ??= other.Handler;
        earliest.ExceptionType ??= other.ExceptionType;
        earliest.Level ??= other.Level;
        return earliest;
    }

    private static bool IsEarlier(EdgeRecord a, EdgeRecord b)
    {
        var byFile = string.CompareOrdinal(a.File ?? "", b.File ?? "");
        if (byFile != 0)
        {
            return byFile < 0;
        }

        return (a.Line ?? int.MaxValue) < (b.Line ?? int.MaxValue)
               || ((a.Line ?? 0) == (b.Line ?? 0) && (a.IlOffset ?? int.MaxValue) < (b.IlOffset ?? int.MaxValue));
    }
}

/// <summary>Collects nodes from many threads; the first record for an ID wins unless a better one arrives.</summary>
public sealed class NodeSet
{
    private readonly ConcurrentDictionary<string, NodeRecord> _nodes = new(StringComparer.Ordinal);

    public int Count => _nodes.Count;

    public bool Contains(string id) => _nodes.ContainsKey(id);

    /// <summary>Adds a node. A real node replaces an :External stub with the same ID; otherwise the earliest location wins.</summary>
    public void Add(NodeRecord node)
    {
        _nodes.AddOrUpdate(node.Id, node, (_, existing) => Prefer(existing, node));
    }

    public List<NodeRecord> Drain() =>
        _nodes.Values.OrderBy(n => n.Label, StringComparer.Ordinal).ThenBy(n => n.Id, StringComparer.Ordinal).ToList();

    private static NodeRecord Prefer(NodeRecord existing, NodeRecord candidate)
    {
        if (existing.Label == "External" && candidate.Label != "External")
        {
            return candidate;
        }

        if (candidate.Label == "External" || existing.Label != candidate.Label)
        {
            return existing;
        }

        var byFile = string.CompareOrdinal(candidate.File ?? "", existing.File ?? "");
        return byFile < 0 || (byFile == 0 && (candidate.LineStart ?? int.MaxValue) < (existing.LineStart ?? int.MaxValue))
            ? candidate
            : existing;
    }
}
