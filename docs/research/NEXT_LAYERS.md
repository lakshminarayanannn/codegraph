# Next layers: beyond the code fact layer

An external review proposed what a bug-resolution system needs beyond indexing the source. This page compares
that proposal with what codegraph already has, and specifies the gaps as work items for the implementation
agent. It extends [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) (W1–W13) and changes none of them.

Core idea of the review: source tells you what *can* happen. Root causes come from what *does* happen on a
running system (runtime evidence) and from what went wrong *last time* (history). The code graph is the fact
layer those two attach to.

## 1. Already covered

| Proposal | Where it lives today |
|---|---|
| Projects, assembly references, build configuration | Roslyn extractor (MSBuildWorkspace), `REFERENCES` edges, `assembly_graph` |
| Types, members, inheritance, implementations, overrides | Roslyn extractor; `DISPATCHES_TO` |
| Call graph, field reads/writes, object creation | `CALLS`, `READS`/`WRITES`; `CREATES` and factories on the working branch |
| Log and exception text → emitting method | `EMITS` / `THROWS` + logmatch, `find_log_source`, `find_exception_source` |
| Configuration keys read | `READS_CONFIG`, `config_usage` (rules in `config_accessors`) |
| Native C++ symbols and calls | libclang extractor (clang-cl mode) |
| **Stable symbol IDs across commits** | Roslyn documentation-comment IDs (`dn:` prefix), clang USRs (`cpp:`) |
| C++/CLI via compiled-assembly metadata, lower confidence on heuristic edges | bridge extractor (ILSpy metadata + IL scan); `confidence` on every edge |
| Provenance on every edge | `confidence` (exact / heuristic), `via`, `file:line`; runtime `OBSERVED` edges on the working branch |
| Co-change and churn | `CO_CHANGES_WITH`, `hotspots(metric="churn")` |
| Runtime log overlay | `OBSERVED` / `PRECEDES` / `INFERRED_PATH` on the working branch |
| Coverage ("which tests exercise which methods") | static `tests_reaching`; `EXECUTED` from Cobertura on the working branch |
| Incremental indexer | `scripts/incremental_index.*` (owner-scoped; proven equal to a full rebuild) |
| Narrow MCP tools instead of raw Cypher | 23 tools (base) / 37 (working branch) |
| Replay-resolved-bugs eval | PR-derived A/B harness (see EVAL_PROTOCOL.md) |

## 2. Gaps worth building

Priority order: N1 → N2 → N3 → N4 → N5 → N6 → N7 → N8.

### N1 · Experience layer: bug → fix-commit history, and `similar_past_bugs`

**Why.**

- History is the second source of root causes.
- Past fixes say *where* similar symptoms were really fixed. That speaks directly to the "edits a different
  method than the PR" problem.

**First check (one day, no code).** Measure how consistently fix commits name a work-item ID. Report:

- the share of merged PRs and commits with a ticket ID;
- the share of those whose ticket has symptom text.

If the share is low, link recovery (by PR title, branch name or time window) is a separate project. Decide that
before building.

**What to build.**

1. **Ingest.** Work items (ID, title, symptom text, component, attached log/stack excerpts, resolution notes) and
   their fix commits/PRs. The tracker is read through its API by the loader (offline export allowed); store only
   what is needed.
2. **Graph.**
   - `(:Bug {id, title, component, createdAt, resolvedAt})-[:FIXED_BY]->(:Commit {sha, date})`.
   - `(:Commit)-[:CHANGED {lines}]->(:Method)`, by mapping diff hunks to methods (reuse the `diff_impact` line
     mapping).
   - `(:Bug)-[:MENTIONS {how: log|exception|stack|identifier}]->(:Message|:Method|:Type)`: what the symptom text
     resolves to (logmatch, the exception matcher, stack-frame parsing, identifier lookup).
3. **Tool `similar_past_bugs(text, k=5)`.** Input: symptom text, log lines, or a stack trace. Rank past bugs by:
   - shared matched log templates or exception sites (strongest);
   - shared stack-frame methods;
   - graph proximity between the methods they mention (≤ 2 hops);
   - BM25 over title and symptom text.

   Return each bug with its fixed methods (`file:line`), the fix commit, and why it matched.
4. **Signal for `locate` (W4).** Methods changed by similar past bugs get a boost.

**Eval rule (leakage).** For a task at commit C, only bugs *resolved before* C may be visible. Index history as
of the task's parent commit, as the graph already is.

**Acceptance.** On the PR-derived tasks, report how often the gold method or file appears among the fixed
methods of the top-5 similar past bugs, compared with `locate` alone.

### N2 · Stack traces and crash dumps as anchors

**Why.** A stack frame names the method exactly. It is a stronger anchor than any log line.

**What to build.**

1. **Parser** for .NET stack traces (`at Ns.Type.Method(args) in path:line`, including async state-machine
   frames `<Method>d__N.MoveNext`, lambdas `<>c__DisplayClass`, and generic arity) → `dn:` IDs through the
   resolver.
2. **Native frames** (`module!function+0xoffset`, MSVC-decorated names) → native nodes, via the mangled names the
   native extractor already records (`clang_Cursor_getCXXManglings`) and the exports.
3. **Mixed-mode frames** (C++/CLI) → bridge nodes.
4. **Tool `resolve_stack(text)`.** Returns each frame mapped to a graph node (or `unresolved` with the reason),
   plus the static call path between consecutive frames, which checks that the trace is consistent with this
   build.
5. **Crash dumps (spike, Windows).** Extract managed stacks with `dotnet-dump analyze` / ClrMD and native stacks
   with dbghelp (needs PDBs for the installed build), then feed the text to `resolve_stack`. Record which dump
   types and builds work.

**Acceptance.**

- Fixture tests for each frame shape.
- On real traces from work items: the share of frames resolved, by kind.

### N3 · Exemplars for feature work

**Why.** Feature work runs forward from intent. A senior engineer copies the pattern of the most similar existing
implementation; grep finds names, not structural siblings.

**What to build.** Tool `exemplars(target, k=5)`. The target is an interface, base type, attribute, factory or
registration site, or a free-text concept that `locate` turns into one. Candidates are types that:

- implement the same interface or derive from the same base;
- carry the same attributes;
- are created by the same factory (`CREATES` / `when`);
- are registered at the same registration or event-subscription sites.

Rank by:

- the number of shared structural features;
- recent changes (git), so the current pattern wins over legacy;
- having tests (`tests_reaching`).

Return each exemplar with its members that override or implement the extension point, its registration site,
and its tests.

**Acceptance.** For 10 recent feature PRs, does the closest existing implementation the developer actually
followed (labelled by a senior engineer) appear in the top 5?

### N4 · Catch sites and error codes (where errors are swallowed)

**Why.** An exception in a report has an origin (`THROWS`, already there) and often a place where it was
swallowed or turned into a different error. That second place is frequently the bug.

**What to build.**

1. `(:Method)-[:CATCHES {exceptionType, action: rethrow|wrap|log|swallow|return_code, file, line}]->(:Type)`,
   from Roslyn `ICatchClauseOperation`. `action` is derived from the handler body: `throw;`, `throw new …`, a
   log call, empty, or `return <enum/const>`.
2. **Error-code enums.** Uses of configured error-code enum members (config `error_code_enums`) produce
   `RETURNS_CODE` / `CHECKS_CODE` edges.
3. **Tool `error_flow(exception_type | code)`.** Returns where the error is thrown, caught, swallowed or
   converted, and who checks the code.

### N5 · Threading and concurrency signals

**Why.** Races and deadlocks are common root causes in multi-process, multi-threaded control software, and grep
can't connect the pieces.

**What to build.** The working branch already has `CONTINUES_IN` for tasks, threads, the dispatcher and timers.
Add:

- `(:Method)-[:LOCKS {file, line}]->(:Field|:Type)`: the lock object of `lock(x)` / `Monitor.Enter`, resolved to
  its field or type;
- `[:WAITS]` for `Wait()`, `.Result`, `Join`, and `WaitOne` on synchronization objects.

**Tool `concurrency_of(symbol)`.** Reports:

- locks taken on paths through the symbol;
- lock-order pairs (A then B on one path, B then A on another) as deadlock candidates;
- fields written from more than one thread context without a common lock, as race candidates.

All of these are heuristic candidates, never facts.

### N6 · Build-versioned graph for field bugs

This is the same need as W9: the graph as of the build installed on the machine where the failure happened. It
extends W9 from log templates to the whole graph:

1. map build → commit;
2. index that commit on demand (the managed-only per-commit index takes minutes on the working branch);
3. have all tools answer against it when the bug states a build;
4. keep the N1 history visible up to that commit.

### N7 · Anchored human knowledge (later)

**Why.** Invariants, "talk to X before touching this", hardware timing assumptions and odd workarounds can't be
extracted from code.

**What to build.**

1. `(:Claim {text, author, createdAt, reviewedBy, status})-[:ABOUT {anchorCommit, anchorHash}]->(:Method|:Type|:File)`.
2. **Staleness.** When the anchor's code hash changes in an incremental index, mark the claim `stale` and list it
   for re-review.
3. **Loop.** The system drafts a subsystem page from the graph; a senior engineer corrects it in about 10
   minutes; the corrections are stored as claims.
4. **Serving.** Claims appear in `context_pack`, labelled as human claims with their date and status.

### N8 · Access control

**Why.** The index and the graph are a full copy of proprietary code structure and messages.

**What to build.**

- Keep everything local (SPEC rule). Use the read-only Neo4j account for the MCP server (exists: `codegraph_ro`).
- Bind Neo4j to localhost.
- Keep the code snippets that `locate` returns (W4) read from the local checkout, never stored in third-party
  services.
- Document who may run the indexer and where the database files live.
- Never commit indexes, transcripts or session logs to this repository (`out/` and `bench/results/` stay
  git-ignored).

## 3. How this changes the build order

Build order from the review: fact layer → bug ↔ fix history → runtime traces → concept/claims layer. Mapped onto
our plans:

| Step | Items |
|---|---|
| now (prove it) | W1, W2, W3 (IMPROVEMENT_PLAN.md) |
| next (localization) | W4 `locate` + N2 stack traces + N1 similar past bugs (feeding `locate`) |
| then (fix quality) | W5, W6 |
| then (runtime) | W7–W10 + N6 build-versioned graph |
| then (bug and feature breadth) | N4 error flow, N3 exemplars, N5 concurrency |
| later | N7 claims, W11–W13 |
| always | N8 access rules |

**Measure every new layer the same way:**

- deterministic Layer 1 first (does the gold method or file show up in the top k?);
- then the agent A/B (EVAL_PROTOCOL.md).

These items are proposals. Unlike W1–W13, they don't yet have published evidence in EVIDENCE.md behind them.
Treat their payoff as unproven until their Layer 1 numbers are in.
