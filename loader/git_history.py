"""Git history pass (SPEC 5.7): per-file commit counts and co-change pairs.

One `git log` over the configured window. Commits touching more than co_change_max_files files are
skipped for pairs (mass renames and reformatting say nothing about coupling). Author names are never
read (privacy). Paths are relative to repo_root (`--relative`), matching the extractors' paths.
"""
from __future__ import annotations

import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path

from codegraph_common.log import log

from .merge import GitHistory


def git(repo: Path, *args: str) -> str | None:
    """Runs git in the repo; None when git or the repository is not available."""
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        return None
    return result.stdout if result.returncode == 0 else None


def head_commit(repo: Path) -> tuple[str, bool]:
    """(HEAD sha or "nogit", working tree has uncommitted changes)."""
    sha = git(repo, "rev-parse", "HEAD")
    if sha is None:
        return "nogit", False
    status = git(repo, "status", "--porcelain", "--untracked-files=no") or ""
    return sha.strip(), bool(status.strip())


def collect(repo: Path, window_days: int, co_change_min: int, co_change_max_files: int) -> GitHistory | None:
    output = git(repo, "log", f"--since={window_days}.days", "--no-merges", "--no-renames", "--relative",
                 "--format=%x1e%H%x09%cI", "--name-only")
    if output is None:
        log("loader", "git history skipped: not a git repository (or git not installed)")
        return None
    tracked = {p.lower() for p in (git(repo, "ls-files") or "").splitlines()}

    counts: Counter[str] = Counter()
    last_date: dict[str, str] = {}
    pairs: Counter[tuple[str, str]] = Counter()
    display: dict[str, str] = {}
    for record in output.split("\x1e"):
        lines = [line for line in record.strip().splitlines() if line.strip()]
        if not lines:
            continue
        _, date = lines[0].split("\t", 1)
        files = sorted({path for path in lines[1:] if path.lower() in tracked})
        for path in files:
            key = path.lower()
            display.setdefault(key, path)
            counts[key] += 1
            last_date[key] = max(last_date.get(key, ""), date)  # ISO dates compare as text
        if 1 < len(files) <= co_change_max_files:
            for a, b in combinations(files, 2):
                pairs[(a.lower(), b.lower())] += 1

    co_changes = sorted((display[a], display[b], n) for (a, b), n in pairs.items() if n >= co_change_min)
    log("loader", f"git history: {len(counts)} files, {len(co_changes)} co-change pairs (>= {co_change_min} commits)")
    return GitHistory(files={k: (counts[k], last_date[k]) for k in counts}, pairs=co_changes)
