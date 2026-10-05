"""Template -> regex (SPEC 9): .NET composite format and printf styles, literal character counts."""
import re

from logmatch.templates import compile_template, search_tokens, strip_prefix


def matches(template: str, line: str, style: str = "dotnet") -> bool:
    compiled = compile_template(template, style)
    return compiled.regex is not None and re.search(compiled.regex, line) is not None


def test_dotnet_placeholders_become_groups():
    compiled = compile_template("Moving {0} to {1} mm")
    assert compiled.regex == r"Moving\ (.+?)\ to\ (.+?)\ mm"
    assert compiled.literal_chars == len("Moving ") + len(" to ") + len(" mm")
    assert matches("Moving {0} to {1} mm", "2026-01-01 [INFO] Moving X to 12.5 mm")


def test_format_items_and_named_holes():
    assert matches("Offset {0,8:F3} um for {stage}", "Offset    0.500 um for X")


def test_escaped_braces_are_literal():
    compiled = compile_template("Set {{x}} to {0}")
    assert matches("Set {{x}} to {0}", "Set {x} to 5")
    assert compiled.literal_chars == len("Set {x} to ")


def test_printf_conversions():
    template = "Target %.3f mm is outside the travel range (%s, %d%%)"
    assert matches(template, "ERROR Target 150.000 mm is outside the travel range (X, 50%)", style="printf")
    assert compile_template("%-10s|%5lld|%I64u|%p", "printf").regex == r"(.+?)\|(.+?)\|(.+?)\|(.+?)"


def test_dynamic_and_placeholder_only_templates_have_no_regex():
    assert compile_template("<dynamic>").regex is None
    assert compile_template("{0}").regex is None
    assert compile_template("%s", "printf").literal_chars == 0


def test_control_characters_never_appear_raw_in_the_regex():
    compiled = compile_template("first line\nsecond {0}")
    assert "\n" not in compiled.regex
    assert re.search(compiled.regex, "first line\nsecond 42")


def test_prefix_stripping_and_search_tokens():
    prefix = r"^\d{4}-\d{2}-\d{2}[ T][\d:.,]+\s+\[\w+\]\s+"
    line = strip_prefix("2026-10-04 10:11:12,345 [INFO] Moving stage X to position", prefix)
    assert line == "Moving stage X to position"
    assert search_tokens(line) == ["position", "Moving", "stage"]
