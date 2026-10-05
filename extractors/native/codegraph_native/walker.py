"""Walks one translation unit (SPEC 6.3) and returns plain dictionaries (picklable across processes).

Only cursors located inside the repository are visited: system and SDK headers are pruned at the top
level, which is what keeps a windows.h-heavy translation unit fast in Python. Headers inside the repo
are visited from every translation unit that includes them; the caller deduplicates by USR.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import clang.cindex as ci

from .clangtools import PARSE_KEEP_GOING, all_manglings, overridden_usrs

K = ci.CursorKind
FUNCTION_KINDS = {K.FUNCTION_DECL, K.CXX_METHOD, K.CONSTRUCTOR, K.DESTRUCTOR, K.FUNCTION_TEMPLATE, K.CONVERSION_FUNCTION}
TYPE_KINDS = {K.CLASS_DECL, K.STRUCT_DECL, K.UNION_DECL, K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION}
SCOPE_KINDS = {K.NAMESPACE, K.LINKAGE_SPEC, K.UNEXPOSED_DECL}
TYPE_KIND_NAMES = {K.CLASS_DECL: "class", K.STRUCT_DECL: "struct", K.UNION_DECL: "union", K.CLASS_TEMPLATE: "class",
                   K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION: "class"}


def parse(source: str, args: list[str], repo_root: str, log_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Parses one source file and returns its records plus diagnostics and the repo headers it included."""
    index = ci.Index.create()
    try:
        tu = index.parse(source, args=args, options=PARSE_KEEP_GOING)
    except ci.TranslationUnitLoadError as error:
        return {"source": source, "failed": str(error), "functions": [], "types": [], "edges": [], "messages": [],
                "files": [], "includes": {}, "errors": 1, "firstErrors": [str(error)]}
    walker = Walker(repo_root, log_rules)
    walker.walk(tu.cursor)
    errors = [d for d in tu.diagnostics if d.severity >= ci.Diagnostic.Error]
    includes = {}
    for inclusion in tu.get_includes():
        relative = walker.relative(inclusion.include.name)
        if relative is not None:
            includes[relative] = file_sha1(inclusion.include.name)
    return {
        "source": source,
        "functions": list(walker.functions.values()),
        "types": list(walker.types.values()),
        "edges": walker.edges,
        "messages": list(walker.messages.values()),
        "files": sorted(walker.files),
        "includes": includes,
        "errors": len(errors),
        "firstErrors": [f"{d.location.file}:{d.location.line}: {d.spelling}" for d in errors[:5]],
        "externalCalls": walker.external_calls,
    }


def file_sha1(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()


class Walker:
    def __init__(self, repo_root: str, log_rules: list[dict[str, Any]]):
        self.repo_root = os.path.normcase(os.path.abspath(repo_root))
        self.log_rules = {rule["name"]: rule for rule in log_rules}
        self.functions: dict[str, dict[str, Any]] = {}
        self.types: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.messages: dict[str, dict[str, Any]] = {}
        self.files: set[str] = set()
        self.external_calls = 0
        self._relative: dict[str, str | None] = {}

    # ---- locations -----------------------------------------------------------------------------

    def relative(self, path: str | None) -> str | None:
        """Repo-relative path with forward slashes, or None outside the repository (cached per file)."""
        if not path:
            return None
        cached = self._relative.get(path)
        if cached is None and path not in self._relative:
            full = os.path.normcase(os.path.abspath(path))
            inside = full == self.repo_root or full.startswith(self.repo_root + os.sep)
            cached = os.path.relpath(os.path.abspath(path), self.repo_root).replace("\\", "/") if inside else None
            self._relative[path] = cached
        return cached

    def where(self, cursor: ci.Cursor) -> str | None:
        location = cursor.location
        return self.relative(location.file.name) if location.file else None

    # ---- traversal -----------------------------------------------------------------------------

    def walk(self, cursor: ci.Cursor) -> None:
        for child in cursor.get_children():
            path = self.where(child)
            if path is not None:
                self.visit(child, path)

    def visit(self, cursor: ci.Cursor, path: str) -> None:
        kind = cursor.kind
        if kind in SCOPE_KINDS:
            self.walk(cursor)
        elif kind in TYPE_KINDS:
            self.on_type(cursor, path)
            self.walk(cursor)  # members, nested types, inline method bodies
        elif kind in FUNCTION_KINDS:
            self.on_function(cursor, path)

    def on_type(self, cursor: ci.Cursor, path: str) -> None:
        usr = cursor.get_usr()
        if not usr or not cursor.is_definition():
            return
        type_id = "cpp:" + usr
        self.files.add(path)
        parent = cursor.semantic_parent
        self.types[type_id] = {
            "id": type_id, "name": cursor.spelling, "fqn": qualified_name(cursor), "kind": TYPE_KIND_NAMES[cursor.kind],
            "file": path, "lineStart": cursor.extent.start.line, "lineEnd": cursor.extent.end.line,
            "typeId": "cpp:" + parent.get_usr() if parent is not None and parent.kind in TYPE_KINDS else None,
            "dllexport": has_dllexport(cursor),
        }
        for child in cursor.get_children():
            if child.kind == K.CXX_BASE_SPECIFIER:
                base = child.referenced or child.type.get_declaration()
                if base is not None and base.get_usr() and self.where(base) is not None:
                    self.edge("INHERITS", type_id, "cpp:" + base.get_usr(), path, child.location.line)

    def on_function(self, cursor: ci.Cursor, path: str) -> None:
        usr = cursor.get_usr()
        if not usr:
            return
        function_id = "cpp:" + usr
        self.files.add(path)
        parent = cursor.semantic_parent
        is_method = parent is not None and parent.kind in TYPE_KINDS
        mangled = safe_mangled(cursor)
        record = {
            "id": function_id, "name": cursor.spelling, "qualifiedName": qualified_name(cursor), "mangledName": mangled,
            "signature": f"{cursor.result_type.spelling} {cursor.displayname}".strip(),
            "isMethod": is_method, "isVirtual": cursor.is_virtual_method(), "isStatic": cursor.is_static_method(),
            "isDefinition": cursor.is_definition(), "paramCount": sum(1 for _ in cursor.get_arguments()),
            "isExternC": (not is_method) and bool(mangled) and mangled == cursor.spelling,
            "file": path, "lineStart": cursor.extent.start.line, "lineEnd": cursor.extent.end.line,
            "typeId": "cpp:" + parent.get_usr() if is_method else None,
            "dllexport": has_dllexport(cursor) or (is_method and has_dllexport(parent)),
            "manglings": all_manglings(cursor) if cursor.kind in (K.CONSTRUCTOR, K.DESTRUCTOR) else None,
        }
        existing = self.functions.get(function_id)
        if existing is None or (record["isDefinition"] and not existing["isDefinition"]):
            self.functions[function_id] = record

        if cursor.kind == K.CXX_METHOD and cursor.is_virtual_method():
            for base_usr in overridden_usrs(cursor):
                self.edge("OVERRIDES", function_id, "cpp:" + base_usr, path, cursor.location.line)
        if cursor.is_definition():
            self.scan_body(cursor, function_id, path)

    # ---- bodies --------------------------------------------------------------------------------

    def scan_body(self, function: ci.Cursor, function_id: str, path: str) -> None:
        """Calls (static targets) and log calls inside one function body; lambda bodies are tagged via=lambda."""
        stack: list[tuple[ci.Cursor, str | None]] = [(child, None) for child in function.get_children()]
        while stack:
            node, via = stack.pop()
            if node.kind == K.LAMBDA_EXPR:
                via = "lambda"
            elif node.kind == K.CALL_EXPR:
                self.on_call(node, function_id, path, via)
            stack.extend((child, via) for child in node.get_children())

    def on_call(self, call: ci.Cursor, function_id: str, path: str, via: str | None) -> None:
        target = call.referenced
        if target is None or target.kind not in FUNCTION_KINDS or not target.get_usr():
            return  # function pointers, dependent calls in templates: no static target
        if self.where(target) is None:
            self.external_calls += 1  # CRT, Win32, std: outside the indexed code
            return
        kind = "ctor" if target.kind == K.CONSTRUCTOR else "call"
        self.edge("CALLS", function_id, "cpp:" + target.get_usr(), path, call.location.line, kind=kind, via=via)
        rule = self.log_rules.get(target.spelling)
        if rule is not None:
            self.on_log(call, rule, function_id, path, via)

    def on_log(self, call: ci.Cursor, rule: dict[str, Any], function_id: str, path: str, via: str | None) -> None:
        arguments = list(call.get_arguments())
        index = int(rule.get("template_arg", 0))
        if index >= len(arguments):
            return
        template = string_literal(arguments[index]) or "<dynamic>"
        level = None
        if rule.get("level_arg") is not None and int(rule["level_arg"]) < len(arguments):
            level = level_name(arguments[int(rule["level_arg"])], rule.get("levels") or {})
        message_id = "msg:" + hashlib.sha1(f"{template}|{function_id}".encode()).hexdigest()
        self.messages[message_id] = {"id": message_id, "kind": "log", "level": level, "template": template,
                                     "templateStyle": "printf", "file": path, "lineStart": call.location.line,
                                     "lineEnd": call.location.line}
        self.edge("EMITS", function_id, message_id, path, call.location.line, via=via, level=level)

    def edge(self, rel: str, start: str, end: str, path: str, line: int, **properties: Any) -> None:
        self.edges.append({"type": rel, "start": start, "end": end, "file": path, "line": line,
                           **{k: v for k, v in properties.items() if v is not None}})


# ---- helpers -------------------------------------------------------------------------------------

def qualified_name(cursor: ci.Cursor) -> str:
    """"MiniPlant::Native::NativeStage::Move" - namespaces and classes up the semantic parents."""
    parts = []
    current: ci.Cursor | None = cursor
    while current is not None and current.kind != K.TRANSLATION_UNIT:
        if current is cursor or current.kind in TYPE_KINDS or current.kind == K.NAMESPACE:
            parts.append(current.spelling or ("(anonymous namespace)" if current.kind == K.NAMESPACE else "(anonymous)"))
        current = current.semantic_parent
    return "::".join(reversed(parts))


def safe_mangled(cursor: ci.Cursor) -> str | None:
    try:
        return cursor.mangled_name or None
    except Exception:  # templates and some special members have no mangled name
        return None


def has_dllexport(cursor: ci.Cursor | None) -> bool:
    return cursor is not None and any(child.kind == K.DLLEXPORT_ATTR for child in cursor.get_children())


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "?": "?"}
_LITERAL = re.compile(r'^(?:u8|u|U|L)?"(.*)"$', re.DOTALL)


def string_literal(argument: ci.Cursor) -> str | None:
    """The text of the first string literal inside an argument (adjacent literals "a" "b" concatenated)."""
    for node in argument.walk_preorder():
        if node.kind == K.STRING_LITERAL:
            tokens = [t.spelling for t in node.get_tokens()] or [node.spelling]
            parts = [_unescape(m.group(1)) for t in tokens if (m := _LITERAL.match(t))]
            return "".join(parts) if parts else None
    return None


def _unescape(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape.startswith("x"):
            return chr(int(escape[1:], 16))
        if escape[0] in "01234567" and len(escape) > 1:
            return chr(int(escape, 8))
        return _ESCAPES.get(escape, escape)
    return re.sub(r"\\(x[0-9a-fA-F]+|[0-7]{1,3}|.)", replace, text)


def level_name(argument: ci.Cursor, levels: dict[Any, str]) -> str | None:
    """LOG_ERROR -> "error"; a number is mapped through the rule's `levels` table when given."""
    for node in argument.walk_preorder():
        if node.kind == K.DECL_REF_EXPR and node.referenced is not None and node.referenced.kind == K.ENUM_CONSTANT_DECL:
            name = node.referenced.spelling.lower()
            return re.sub(r"^(log_?level_|loglevel_|log_|level_|lvl_)", "", name)
        if node.kind == K.INTEGER_LITERAL:
            token = next(iter(node.get_tokens()), None)
            if token is not None:
                return str(levels.get(int(token.spelling, 0), levels.get(token.spelling, token.spelling)))
    return None
