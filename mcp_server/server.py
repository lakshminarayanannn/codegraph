"""MCP server (SPEC 10): registers the graph tools over stdio for Claude Code and other agents.

Never print to stdout here: stdout carries the MCP protocol. Logs go to stderr.
Results are compact JSON text (no indentation): agents pay per token.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from codegraph_common.config import Config

from .graph import ReadOnlyGraph
from .tools import Tools

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

try:
    from mcp.types import ToolAnnotations

    READ_ONLY = ToolAnnotations.model_validate({"readOnlyHint": True, "destructiveHint": False,
                                                "idempotentHint": True, "openWorldHint": False})
except Exception:  # very old SDKs have no annotations; the tools are read-only regardless
    READ_ONLY = None

INSTRUCTIONS = """\
Code knowledge graph of this codebase (C#, native C++ and C++/CLI), built by compilers - not by search.
Workflow: call find_symbol first to get an exact id, then pass that id to the other tools (names also
work; an ambiguous name returns candidates). Every result has file:line locations, a confidence
("exact" = compiler-proven; "heuristic" = name-based or historical), and index_commit/stale: if stale
is true the code changed since indexing, so verify line numbers by reading the file.
Interface and virtual calls are resolved through DISPATCHES_TO edges; calls into native code through
P/Invoke and C++/CLI are connected (path_to_native). Reflection, DI and XAML wiring are NOT resolved.
"""


def build_server(config: Config) -> Any:
    graph = ReadOnlyGraph(config)
    tools = Tools(graph, config)
    server = _Server(name="codegraph", instructions=INSTRUCTIONS)

    def register(function: Callable[..., str]) -> None:
        options: dict[str, Any] = {"name": function.__name__, "description": function.__doc__}
        if READ_ONLY is not None:
            options["annotations"] = READ_ONLY
        try:
            server.tool(structured_output=False, **options)(function)
        except TypeError:  # SDKs without structured_output
            server.tool(**options)(function)

    def run(tool: str, /, **arguments: Any) -> str:
        return json.dumps(tools.call(tool, **arguments), separators=(",", ":"), ensure_ascii=False, default=str)

    # ---- navigation and understanding --------------------------------------------------------

    def find_symbol(name: str, kind: str | None = None, limit: int = 20) -> str:
        """Find symbols by name: exact id, fully-qualified name, short name ("Move"), "Type.Member", then full-text.
        kind filters by label or kind: method, type, field, property, event, interface, class, nativefunction.
        Returns ids to pass to every other tool, with file:line."""
        return run("find_symbol", name=name, kind=kind, limit=limit)

    def outline(target: str, limit: int = 200) -> str:
        """Structure without bodies. target = a repo-relative file path, a type (id or name) or an assembly name.
        Gives members with signatures and file:line, base types, interfaces, derived types."""
        return run("outline", target=target, limit=limit)

    def dependencies_of(symbol: str, level: str = "type") -> str:
        """What a method or type uses (calls, reads, writes, inherits, P/Invokes), grouped by target type
        (level="type") or assembly (level="assembly"). External (BCL/NuGet) uses are listed separately."""
        return run("dependencies_of", symbol=symbol, level=level)

    def callers_of(symbol: str, max_depth: int = 4, limit: int = 100) -> str:
        """Who calls this (transitively, up to max_depth <= 8): direct calls, interface/virtual dispatch,
        method-group references, event subscriptions, and managed callers of native code.
        Each caller has its depth and the shortest call path with file:line of every call site."""
        return run("callers_of", symbol=symbol, max_depth=max_depth, limit=limit)

    def callees_of(symbol: str, max_depth: int = 3, limit: int = 200) -> str:
        """What this calls, as a tree up to max_depth <= 6 (excludes BCL/NuGet). Follows dispatch to
        implementations and P/Invoke / C++/CLI calls into native code."""
        return run("callees_of", symbol=symbol, max_depth=max_depth, limit=limit)

    def implementations_of(symbol: str) -> str:
        """For an interface/abstract/virtual method: every implementation and override (DISPATCHES_TO).
        For a type: every type that inherits or implements it."""
        return run("implementations_of", symbol=symbol)

    def path_to_native(symbol: str, max_depth: int = 8) -> str:
        """Where execution starting at this managed method crosses into native C++ (via P/Invoke exports
        or C++/CLI IJW calls), with the full path."""
        return run("path_to_native", symbol=symbol, max_depth=max_depth)

    def entry_points(kind: str | None = None, limit: int = 200) -> str:
        """Entry points: main, ui_handler, remote, native_callback, scheduled, plugin (kind filters)."""
        return run("entry_points", kind=kind, limit=limit)

    def entry_points_reaching(symbol: str, max_depth: int = 8) -> str:
        """Which entry points can reach this symbol (UI handlers, Main, remote calls, native callbacks),
        grouped by kind, each with a call path."""
        return run("entry_points_reaching", symbol=symbol, max_depth=max_depth)

    def assembly_graph(layer: str | None = None, include_transitive: bool = False) -> str:
        """Assemblies (C#, C++/CLI, native modules) with their layer and references (project, binary,
        pinvoke, ijw). layer filters to assemblies touching that layer."""
        return run("assembly_graph", layer=layer, include_transitive=include_transitive)

    # ---- change planning, review and testing -------------------------------------------------

    def impact_of_change(symbol: str, change_kind: str = "behaviour", max_depth: int = 4) -> str:
        """Impact of changing a symbol. change_kind="signature" or "removal": everything that MUST change
        (the override/implementation family and all direct users, by assembly). "behaviour": transitive
        callers that MAY be affected, plus the entry points and tests among them."""
        return run("impact_of_change", symbol=symbol, change_kind=change_kind, max_depth=max_depth)

    def diff_impact(base_ref: str, head_ref: str = "HEAD", max_depth: int = 8) -> str:
        """Review a change between two git refs: maps changed lines to methods, then for each method gives
        public-API flag, entry points reaching it and tests reaching it; lists changed methods no test reaches."""
        return run("diff_impact", base_ref=base_ref, head_ref=head_ref, max_depth=max_depth)

    def tests_reaching(symbol: str, max_depth: int = 8, limit: int = 100) -> str:
        """Test methods with a call path to this symbol, nearest first: which tests to run for a change."""
        return run("tests_reaching", symbol=symbol, max_depth=max_depth, limit=limit)

    def co_changed_files(file: str, limit: int = 30) -> str:
        """Files that historically change in the same commits as this file (git history; heuristic)."""
        return run("co_changed_files", file=file, limit=limit)

    # ---- architecture health -----------------------------------------------------------------

    def layer_violations() -> str:
        """Assembly references from a lower layer to a higher one (layers from codegraph.yaml), with example calls."""
        return run("layer_violations")

    def unreferenced_symbols(assembly: str | None = None, kind: str = "method", limit: int = 200) -> str:
        """Dead-code CANDIDATES: non-public methods (kind="method") or types (kind="type") nothing references.
        Reflection/DI/XAML/serialization uses are invisible to the graph: verify before deleting."""
        return run("unreferenced_symbols", assembly=assembly, kind=kind, limit=limit)

    def hotspots(metric: str = "fan_in", limit: int = 50) -> str:
        """Most-called methods (fan_in), most-calling methods (fan_out), or most-changed files (churn)."""
        return run("hotspots", metric=metric, limit=limit)

    # ---- debugging, data and config flow -----------------------------------------------------

    def find_log_source(log_line: str, limit: int = 10) -> str:
        """Which code wrote this log line. Paste the raw line (timestamps are stripped); returns the
        message template, the emitting method with file:line and level. matched=true means the template's
        regex matched the line exactly."""
        return run("find_log_source", log_line=log_line, limit=limit)

    def find_exception_source(message: str, exception_type: str | None = None, limit: int = 10) -> str:
        """Which throw statements produce this exception message (optionally only this exception type)."""
        return run("find_exception_source", message=message, exception_type=exception_type, limit=limit)

    def writers_of(symbol: str) -> str:
        """Methods that write this field or property (including initializers), with file:line."""
        return run("writers_of", symbol=symbol)

    def readers_of(symbol: str) -> str:
        """Methods that read this field or property, with file:line."""
        return run("readers_of", symbol=symbol)

    def config_usage(key: str) -> str:
        """Methods that read this configuration key (exact key, else keys containing the text)."""
        return run("config_usage", key=key)

    def index_status() -> str:
        """Last index run: commit, time, node/edge counts, warnings, and whether it is stale."""
        return run("index_status")

    for function in (find_symbol, outline, dependencies_of, callers_of, callees_of, implementations_of, path_to_native,
                     entry_points, entry_points_reaching, assembly_graph, impact_of_change, diff_impact, tests_reaching,
                     co_changed_files, layer_violations, unreferenced_symbols, hotspots, find_log_source,
                     find_exception_source, writers_of, readers_of, config_usage, index_status):
        register(function)
    return server
