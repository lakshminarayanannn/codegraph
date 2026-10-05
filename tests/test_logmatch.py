"""Log matcher ranking (SPEC 9, steps 2-4) with a stub query function - no database needed."""
from logmatch.matcher import find_sources, lucene_query
from logmatch.templates import compile_template


def candidate(message_id: str, template: str, kind: str = "log", exception_type: str | None = None) -> dict:
    compiled = compile_template(template)
    return {"id": message_id, "template": template, "regex": compiled.regex, "literalChars": compiled.literal_chars,
            "kind": kind, "level": "info", "score": 1.0,
            "emitters": [{"id": f"dn:M:{message_id}", "file": "a.cs", "line": 1, "exceptionType": exception_type}]}


def test_matching_templates_rank_first_then_by_literal_text():
    rows = [candidate("msg:b", "Moving {0}"), candidate("msg:a", "Moving {0} to {1} mm"), candidate("msg:c", "Stopped")]
    result = find_sources(lambda query, params: rows, "[INFO] Moving X to 5 mm", r"^\[\w+\]\s+")
    assert [r["messageId"] for r in result["results"]] == ["msg:a", "msg:b", "msg:c"]
    assert [r["matched"] for r in result["results"]] == [True, True, False]


def test_exception_type_filter():
    rows = [candidate("msg:a", "bad", "exception", "dn:T:System.InvalidOperationException"),
            candidate("msg:b", "bad", "exception", "dn:T:System.ArgumentException")]
    result = find_sources(lambda query, params: rows, "bad", None, kind="exception", exception_type="ArgumentException")
    assert [r["messageId"] for r in result["results"]] == ["msg:b"]


def test_lucene_special_characters_are_escaped():
    assert lucene_query(["a+b", "c"]) == r"a\+b OR c"
