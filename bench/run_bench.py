"""Task benchmark harness (SPEC 11.3): each task runs twice with Claude Code in print mode -
arm A with built-in read-only tools, arm B with the same plus the codegraph MCP tools - and is scored.

  python bench/run_bench.py --tasks bench/tasks.yaml --repo <repo root> --codegraph-config <codegraph.yaml>
                            [--model M] [--max-turns 30] [--only id1,id2] [--dry-run]
  python bench/run_bench.py --report bench/results/<run>

Transcripts (stream-json) and scores are written to bench/results/<run>/. Nothing here talks to Neo4j directly.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

READ_ONLY = "Read,Grep,Glob,Bash(git log:*),Bash(git show:*),Bash(git diff:*)"
ARMS = {"A": READ_ONLY, "B": READ_ONLY + ",mcp__codegraph"}
SUFFIX = ("\n\nWork in this repository and do not modify files. When you are done, end your reply with a fenced "
          "JSON block of the form {\"answer\": [\"...\", \"...\"]}.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="bench/tasks.yaml")
    parser.add_argument("--repo", help="repository the agent works in")
    parser.add_argument("--codegraph-config", help="codegraph.yaml used by the MCP server in arm B")
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--only", help="comma-separated task ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", help="print the per-category tables of a finished run folder")
    args = parser.parse_args()
    if args.report:
        print(report(Path(args.report)))
        return 0
    if not (args.repo and args.codegraph_config):
        parser.error("--repo and --codegraph-config are required to run tasks")

    tasks = yaml.safe_load(Path(args.tasks).read_text())["tasks"]
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    run_dir = Path("bench/results") / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    configs = write_mcp_configs(run_dir, Path(args.codegraph_config).resolve())

    for task in tasks:
        for arm in ARMS:
            command = claude_command(task, arm, configs[arm], args)
            if args.dry_run:
                print(f"[{task['id']} {arm}] cd {args.repo} && {' '.join(repr(c) if ' ' in c else c for c in command)}")
                continue
            result = run(command, Path(args.repo), run_dir / f"{task['id']}.{arm}.jsonl")
            result.update(task_id=task["id"], category=task["category"], arm=arm, **score(task, result.get("answer") or []))
            (run_dir / f"{task['id']}.{arm}.score.json").write_text(json.dumps(result, indent=2))
            print(f"[{task['id']} {arm}] recall={result.get('recall')} precision={result.get('precision')} "
                  f"tools={result['tool_calls']} tokens={result['tokens']} {result['seconds']}s", file=sys.stderr)
    if not args.dry_run:
        (run_dir / "report.md").write_text(report(run_dir))
        print(report(run_dir))
    return 0


def write_mcp_configs(run_dir: Path, codegraph_config: Path) -> dict[str, Path]:
    """Arm A: no MCP servers at all. Arm B: only codegraph (absolute paths, this Python)."""
    empty = run_dir / "mcp_none.json"
    empty.write_text(json.dumps({"mcpServers": {}}))
    graph = run_dir / "mcp_codegraph.json"
    graph.write_text(json.dumps({"mcpServers": {"codegraph": {
        "command": sys.executable, "args": ["-m", "mcp_server", "--config", str(codegraph_config)]}}}, indent=2))
    return {"A": empty, "B": graph}


def claude_command(task: dict[str, Any], arm: str, mcp_config: Path, args: argparse.Namespace) -> list[str]:
    command = ["claude", "-p", task["prompt"].strip() + SUFFIX, "--output-format", "stream-json", "--verbose",
               "--allowedTools", ARMS[arm], "--max-turns", str(args.max_turns),
               "--strict-mcp-config", "--mcp-config", str(mcp_config)]
    return command + (["--model", args.model] if args.model else [])


def run(command: list[str], repo: Path, transcript: Path) -> dict[str, Any]:
    started = time.time()
    process = subprocess.run(command, cwd=repo, capture_output=True, text=True)
    transcript.write_text(process.stdout)
    tool_calls, final = 0, {}
    for line in process.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            tool_calls += sum(1 for block in event.get("message", {}).get("content", []) if block.get("type") == "tool_use")
        elif event.get("type") == "result":
            final = event
    usage = final.get("usage") or {}
    tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"tool_calls": tool_calls, "tokens": tokens, "output_tokens": usage.get("output_tokens"),
            "turns": final.get("num_turns"), "seconds": round(time.time() - started, 1),
            "answer": extract_answer(final.get("result") or ""), "exit_code": process.returncode}


def extract_answer(text: str) -> list[str]:
    """The last JSON object with an "answer" list in the reply (fenced or not)."""
    for candidate in reversed(re.findall(r"\{[^{}]*\"answer\"[^{}]*\}", text, re.DOTALL)):
        try:
            value = json.loads(candidate)["answer"]
            return [str(v) for v in value] if isinstance(value, list) else [str(value)]
        except (json.JSONDecodeError, KeyError):
            continue
    return []


def normalize(item: str) -> str:
    """Case-insensitive, without parameter lists or trailing "()": "Ns.T.M(double)" == "ns.t.m"."""
    return re.sub(r"\(.*\)$", "", item.strip()).strip().lower()


def score(task: dict[str, Any], answer: list[str]) -> dict[str, Any]:
    expected = {normalize(a) for a in task["answer"]}
    given = [normalize(a) for a in answer]
    if task.get("scoring") == "single":
        rank = next((i + 1 for i, item in enumerate(given) if item in expected), None)
        return {"found": rank is not None, "rank": rank, "recall": 1.0 if rank else 0.0, "precision": None}
    hits = len(expected & set(given))
    return {"recall": round(hits / len(expected), 3) if expected else 1.0,
            "precision": round(hits / len(set(given)), 3) if given else 0.0}


def report(run_dir: Path) -> str:
    rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(run_dir.glob("*.score.json")):
        result = json.loads(path.read_text())
        rows[(result["category"], result["arm"])].append(result)
        rows[("all", result["arm"])].append(result)

    def mean(results: list[dict[str, Any]], key: str) -> str:
        values = [r[key] for r in results if r.get(key) is not None]
        return f"{statistics.mean(values):.2f}" if values else "-"

    lines = ["| Category | Tasks | Recall A | Recall B | Precision A | Precision B | Tool calls A → B | Tokens A → B | Time (s) A → B |",
             "|---|---|---|---|---|---|---|---|---|"]
    categories = sorted({c for c, _ in rows if c != "all"}) + ["all"]
    for category in categories:
        a, b = rows.get((category, "A"), []), rows.get((category, "B"), [])
        lines.append(f"| {category} | {len(a)} | {mean(a, 'recall')} | {mean(b, 'recall')} | {mean(a, 'precision')} | "
                     f"{mean(b, 'precision')} | {mean(a, 'tool_calls')} → {mean(b, 'tool_calls')} | "
                     f"{mean(a, 'tokens')} → {mean(b, 'tokens')} | {mean(a, 'seconds')} → {mean(b, 'seconds')} |")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
