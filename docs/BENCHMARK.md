# Task benchmark (SPEC 11.3)

**Question for management:** does an agent do real engineering tasks better, cheaper or faster with the codegraph
tools than with search and file reading alone - and for which kinds of work?

## Protocol

* 30–40 tasks in `bench/tasks.yaml`, each with an answer agreed with a senior engineer who knows the code.
  Categories and counts: orientation 6–8, impact analysis 6–8, code review 4–6, testing 4–6, debugging 8–10
  (from the frozen incident set), architecture 2–4.
* Every task runs twice with the same model, prompt and turn budget:
  * **A** — built-in tools only (Read, Grep, Glob, read-only git through Bash);
  * **B** — the same plus the codegraph MCP tools.
* Recorded per run: precision and recall against the answer set (or found + rank for single-answer tasks),
  tool calls, tokens, wall-clock time, and the full transcript (`bench/results/<run>/<task>.<arm>.jsonl`).
* Results are reported **per category**; a category where the graph does not help is a finding too.

## Running it

```bash
# on the machine with the indexed codebase and the MCP server registered in bench/mcp.json
.venv/bin/python bench/run_bench.py --tasks bench/tasks.yaml --mcp-config bench/mcp.json --repo <repo root> --model <model>
.venv/bin/python bench/run_bench.py --report bench/results/<run>      # rebuilds the tables below
```

`run_bench.py --dry-run` prints the exact `claude -p` commands without running them.
The agent is asked to finish with a JSON block `{"answer": [...]}`; answers are compared case-insensitively
after trimming, as sets.

## Results

*Pending: run on the real codebase (Phase 7).* `run_bench.py --report` prints these tables.

| Category | Tasks | Recall A | Recall B | Precision A | Precision B | Tool calls A → B | Tokens A → B | Time A → B |
|---|---|---|---|---|---|---|---|---|
| orientation | | | | | | | | |
| impact | | | | | | | | |
| review | | | | | | | | |
| testing | | | | | | | | |
| debugging | | | | | | | | |
| architecture | | | | | | | | |
| **all** | | | | | | | | |
