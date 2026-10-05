using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.Build.Framework;

namespace CodeGraph.CompileCommands
{
    /// <summary>
    /// msbuild Solution.sln /t:Rebuild -logger:CompileCommandsLogger.dll;out=compile_commands.json
    /// Records every CL (MSVC compiler) invocation and writes one compilation-database entry per source file.
    /// The CL task compiles several files per invocation: options are shared, sources are split out.
    /// </summary>
    public sealed class CompileCommandsLogger : ILogger
    {
        private static readonly string[] SourceExtensions = { ".c", ".cc", ".cpp", ".cxx", ".c++" };
        private readonly List<string> _entries = new List<string>();
        private string _output = "compile_commands.json";

        public LoggerVerbosity Verbosity { get; set; }

        public string? Parameters { get; set; }

        public void Initialize(IEventSource eventSource)
        {
            foreach (var parameter in (Parameters ?? "").Split(';'))
            {
                if (parameter.StartsWith("out=", StringComparison.OrdinalIgnoreCase))
                {
                    _output = parameter.Substring(4);
                }
            }

            eventSource.MessageRaised += OnMessage;
        }

        private void OnMessage(object sender, BuildMessageEventArgs e)
        {
            if (!(e is TaskCommandLineEventArgs command) || !string.Equals(command.TaskName, "CL", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            var directory = Path.GetDirectoryName(command.ProjectFile ?? "") ?? "";
            var arguments = Split(command.CommandLine);
            var sources = arguments.Where(IsSource).ToList();
            var options = arguments.Where(a => !IsSource(a)).ToList();
            foreach (var source in sources)
            {
                var file = Path.GetFullPath(Path.Combine(directory, source));
                var entry = options.Concat(new[] { file }).Select(Quote);
                lock (_entries)
                {
                    _entries.Add($"{{\"directory\":{Quote(directory)},\"file\":{Quote(file)},\"arguments\":[{string.Join(",", entry)}]}}");
                }
            }
        }

        public void Shutdown()
        {
            File.WriteAllText(_output, "[\n" + string.Join(",\n", _entries) + "\n]\n", new UTF8Encoding(false));
            Console.WriteLine($"CompileCommandsLogger: {_entries.Count} entries written to {Path.GetFullPath(_output)}");
        }

        private static bool IsSource(string argument) =>
            !argument.StartsWith("/") && !argument.StartsWith("-")
            && SourceExtensions.Contains(Path.GetExtension(argument.Trim('"')), StringComparer.OrdinalIgnoreCase);

        /// <summary>Windows command-line splitting (CommandLineToArgvW rules).</summary>
        private static List<string> Split(string commandLine)
        {
            var result = new List<string>();
            var current = new StringBuilder();
            bool inQuotes = false, started = false;
            for (var i = 0; i < commandLine.Length; i++)
            {
                var c = commandLine[i];
                if (c == '\\')
                {
                    var end = i;
                    while (end < commandLine.Length && commandLine[end] == '\\') end++;
                    var count = end - i;
                    if (end < commandLine.Length && commandLine[end] == '"')
                    {
                        current.Append('\\', count / 2);
                        if (count % 2 == 1) { current.Append('"'); end++; }
                    }
                    else
                    {
                        current.Append('\\', count);
                    }

                    i = end - 1;
                    started = true;
                }
                else if (c == '"')
                {
                    inQuotes = !inQuotes;
                    started = true;
                }
                else if ((c == ' ' || c == '\t') && !inQuotes)
                {
                    if (started) { result.Add(current.ToString()); current.Clear(); started = false; }
                }
                else
                {
                    current.Append(c);
                    started = true;
                }
            }

            if (started) result.Add(current.ToString());
            return result;
        }

        private static string Quote(string value) =>
            "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
