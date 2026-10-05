"""Git history pass (SPEC 5.7) and diff parsing for diff_impact."""
import os
import subprocess

from loader.git_history import collect, head_commit
from mcp_server.diff import changed_ranges, parse

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def commit(repo, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=ENV)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True, env=ENV)


def test_commit_counts_and_co_changes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit(tmp_path, {"src/A.cs": "1", "src/B.cs": "1", "src/C.cs": "1"}, "one")
    commit(tmp_path, {"src/A.cs": "2", "src/B.cs": "2"}, "two")
    commit(tmp_path, {"src/A.cs": "3"}, "three")
    history = collect(tmp_path, window_days=3650, co_change_min=2, co_change_max_files=30)
    assert history.files["src/a.cs"][0] == 3 and history.files["src/b.cs"][0] == 2
    assert history.pairs == [("src/A.cs", "src/B.cs", 2)]
    sha, dirty = head_commit(tmp_path)
    assert len(sha) == 40 and dirty is False


def test_diff_ranges_for_modified_added_and_deleted_files(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit(tmp_path, {"a.cs": "1\n2\n3\n4\n5\n", "gone.cs": "x\ny\n"}, "base")
    (tmp_path / "gone.cs").unlink()
    commit(tmp_path, {"a.cs": "1\n2\nTHREE\n4\n5\n6\n", "new.cs": "n\n"}, "head")
    changes = {c.path: c for c in changed_ranges(tmp_path, "HEAD~1", "HEAD")}
    assert changes["a.cs"].status == "modified" and changes["a.cs"].ranges == [(3, 3), (6, 6)]
    assert changes["new.cs"].status == "added" and changes["gone.cs"].status == "deleted"
    assert changes["gone.cs"].ranges == [(1, 2)]


def test_pure_deletion_hunk_maps_to_its_position():
    diff = "--- a/x.cs\n+++ b/x.cs\n@@ -10,2 +9,0 @@\n-old\n-old\n"
    assert parse(diff)[0].ranges == [(9, 9)]
