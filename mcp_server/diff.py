"""Changed line ranges between two git refs (for diff_impact).

`git diff --unified=0` lists every hunk with its line range in the new file ("+c,d"). A pure deletion
has d = 0 at line c: the code around line c changed, so it counts as [c, c]. Deleted files use the old
ranges, since only the old version exists. Runs locally; nothing leaves the machine.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/~^@{}\-]+$")


@dataclass
class FileChange:
    path: str                                    # repo-relative, forward slashes
    status: str                                  # modified | added | deleted
    ranges: list[tuple[int, int]] = field(default_factory=list)


def changed_ranges(repo: Path, base_ref: str, head_ref: str) -> list[FileChange]:
    for ref in (base_ref, head_ref):
        if not _SAFE_REF.match(ref) or ref.startswith("-"):
            raise ValueError(f"not a git ref: {ref!r}")
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--unified=0", "--no-color", "--no-ext-diff", "--relative", f"{base_ref}..{head_ref}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return parse(result.stdout)


def parse(diff_text: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            old_path = None if line[4:] == "/dev/null" else line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            new_path = None if line[4:] == "/dev/null" else line[4:].removeprefix("b/")
            if new_path is None:
                current = FileChange(path=old_path or "", status="deleted")
            else:
                current = FileChange(path=new_path, status="added" if old_path is None else "modified")
            changes.append(current)
        elif current is not None and (match := _HUNK.match(line)):
            old_start, old_count, new_start, new_count = match.groups()
            if current.status == "deleted":
                start, count = int(old_start), int(old_count or 1)
            else:
                start, count = int(new_start), int(new_count if new_count is not None else 1)
            current.ranges.append((start, start + max(count, 1) - 1))
    return changes
