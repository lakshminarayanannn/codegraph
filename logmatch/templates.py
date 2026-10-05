"""Template -> regex (SPEC 9, step 1), computed once at index time and stored in Message.regex.

Two template styles exist:
  dotnet  .NET composite format, as the C# extractor writes it: "{0}", "{name}", "{0,8:F2}" are holes,
          "{{" and "}}" are literal braces.
  printf  native format strings: "%s", "%5.2f", "%lld", "%I64u" are holes, "%%" is a literal percent.
Literal text is escaped; every hole becomes a lazy group "(.+?)". `literalChars` counts the literal
characters: the more literal text a template has, the more specific a match against it is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DYNAMIC = "<dynamic>"
HOLE = "(.+?)"

# %[flags][width][.precision][length]conversion  (MSVC length modifiers included)
_PRINTF_SPEC = re.compile(
    r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|j|z|t|L|I64|I32|I|w)?[diouxXeEfFgGaAcsSpnZ]"
)

# Control characters are written as escapes so the regex string itself never holds a raw newline.
_CONTROL = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}


@dataclass(frozen=True)
class CompiledTemplate:
    regex: str | None      # None for "<dynamic>" templates: nothing to match on
    literal_chars: int


def compile_template(template: str, style: str = "dotnet") -> CompiledTemplate:
    if not template or template == DYNAMIC:
        return CompiledTemplate(None, 0)
    pieces = _split_printf(template) if style == "printf" else _split_dotnet(template)
    regex_parts: list[str] = []
    literal_chars = 0
    for is_hole, text in pieces:
        if is_hole:
            if not regex_parts or regex_parts[-1] != HOLE:  # adjacent holes add nothing but ambiguity
                regex_parts.append(HOLE)
        else:
            literal_chars += len(text)
            regex_parts.append("".join(_CONTROL.get(c) or re.escape(c) for c in text))
    if literal_chars == 0:
        return CompiledTemplate(None, 0)
    return CompiledTemplate("".join(regex_parts), literal_chars)


def _split_dotnet(template: str) -> list[tuple[bool, str]]:
    """Splits a composite format string into (is_hole, literal_text) pieces."""
    pieces: list[tuple[bool, str]] = []
    literal: list[str] = []
    i = 0
    while i < len(template):
        c = template[i]
        if c in "{}" and template[i:i + 2] in ("{{", "}}"):
            literal.append(c)  # escaped brace
            i += 2
            continue
        if c == "{":
            end = template.find("}", i + 1)
            if end > i:
                if literal:
                    pieces.append((False, "".join(literal)))
                    literal = []
                pieces.append((True, ""))
                i = end + 1
                continue
        literal.append(c)  # unmatched brace: keep it as text
        i += 1
    if literal:
        pieces.append((False, "".join(literal)))
    return pieces


def _split_printf(template: str) -> list[tuple[bool, str]]:
    pieces: list[tuple[bool, str]] = []
    position = 0
    for match in re.finditer(r"%%|" + _PRINTF_SPEC.pattern, template):
        if match.start() > position:
            pieces.append((False, template[position:match.start()]))
        if match.group(0) == "%%":
            pieces.append((False, "%"))
        else:
            pieces.append((True, ""))
        position = match.end()
    if position < len(template):
        pieces.append((False, template[position:]))
    return pieces


def strip_prefix(line: str, prefix_regex: str | None) -> str:
    """Removes the configured timestamp/thread prefix (SPEC 9, step 5)."""
    if prefix_regex:
        line = re.sub(prefix_regex, "", line, count=1)
    return line.strip()


def search_tokens(line: str, count: int = 3) -> list[str]:
    """The `count` longest alphabetic tokens (SPEC 9, step 2): the most selective words for full-text search."""
    seen: dict[str, str] = {}
    for token in re.findall(r"[A-Za-z]{3,}", line):
        seen.setdefault(token.lower(), token)
    ordered = sorted(seen.values(), key=lambda t: (-len(t), t.lower()))
    return ordered[:count]
