# Evaluation protocol: how to prove (or disprove) the graph's value

Audience: the implementation agent and reviewers. Evidence IDs refer to [EVIDENCE.md](EVIDENCE.md); work items
(W1…W13) refer to [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md).

## 1. What we claim and how each claim is tested

| Claim | Test | Layer |
|---|---|---|
| C1 **Less noise.** The graph's answer to "who calls / uses X" has far fewer wrong hits than grep, at equal or better recall | W1 grep-noise benchmark | 1 (no LLM) |
| C2 **Logs.** What share of real log lines grep cannot place, and how the matcher does on them | W2 census | 1 |
| C3 **A better start.** `locate` puts the gold method in the top 5 more often than a grep-like retriever | W4 offline Acc@k | 1 |
| C4 **Same outcome for less.** Fewer tool calls, files read, tokens, cost and time | agent A/B | 2 |
| C5 **Capability.** Tasks grep handles badly: cross-language paths, grep-hard logs, dispatch-heavy impact | agent A/B, capability slice | 2 |
| C6 **Fix quality not worse** | agent A/B, non-inferiority | 2 |

"More correct fixes" is not a claim unless the data shows it. No published system has shown a large gain of that
kind from better search [H1–H6, FIX1–FIX3].

## 2. Layer 1: deterministic benchmarks (run on every index)

- **Which:** W1, W2, and W4 offline, as defined in IMPROVEMENT_PLAN.md.
- **Run conditions:** fixed seeds, one index commit for every benchmark, and the commit recorded in each report.
- **Intervals:**
  - proportions: Wilson 95 % intervals;
  - medians: a bootstrap over methods or messages;
  - paired hit/miss comparisons (W4 vs baseline): exact McNemar [EVAL12].

## 3. Layer 2: agent A/B

### 3.1 Arms

| Arm | Tools |
|---|---|
| A (baseline) | Read, Grep, Glob, Edit, Write, plus the allow-listed test runner where tests can run (W6) |
| B (graph) | A + the codegraph MCP tools (default profile) + the short usage guide |
| C (optional) | A + the Claude Code LSP plugins `csharp-lsp` and `clangd-lsp` [TOOL5] |

Hold everything else equal across arms:

- the same model ID, Claude Code version, system prompt, budget and max turns;
- a fresh worktree at the PR's parent commit, with the graph indexed at that commit;
- arm order randomized per task and interleaved in time;
- infrastructure failures re-run in **both** arms, never in only one [EVAL5, EVAL6].

### 3.2 Task slices (fixed before the run)

| Slice | n | Source | Success |
|---|---|---|---|
| PR-derived | all 60 | merged PRs (existing) | §3.4 |
| Log lineage, grep-trivial | 10 | existing | names the emitting `Type.Method` and file |
| Log lineage, grep-hard | ≥ 20 (expand from 8 using the W2 census) | W2 labelled sample | same |
| Capability: cross-language and dispatch impact | 20 | W1b chains; impact sets checked against the Roslyn oracle | answer-set precision and recall ≥ 0.9 (pre-registered) |

Record these covariates for every task:

- names a symbol?
- quotes a log line?
- grep-hardness
- files touched
- crosses projects?
- cross-language?
- tests runnable?

### 3.3 Metrics

- **Primary** (pre-register exactly two):
  - P1, efficiency: the paired log-ratio of cost (B/A), or of tool calls if cost is too noisy;
  - P2, task success (§3.4).
- **Secondary** (Holm-corrected):
  - tool calls, files read, tokens, wall time;
  - first step that reaches a gold method or file;
  - gold-file hit and gold-method recall;
  - char precision of reads;
  - hallucinated symbols;
  - graph-tool output characters;
  - graph-tool calls and errors per tool.
- **Value summaries:**
  - cost per successful task (cost-of-pass) [EVAL10];
  - cost vs success per slice [EVAL9];
  - pass^2 on repeated tasks [EVAL11].

### 3.4 Scoring PR tasks

1. **Tests first (objective).** The PR's tests must fail before the change and pass after it, and the other tests
   reaching the changed methods must still pass. This is SWE-bench's FAIL_TO_PASS / PASS_TO_PASS rule [EVAL7].
2. **Location is a diagnostic only.**
   - The gold set is the PR's non-test methods, minus unrelated (tangled) changes [FIX11].
   - Report gold-file hit, any gold method touched, and gold-method recall.
   - Never score success as "edited the same method as the PR". Valid fixes often live elsewhere [LOC13, FIX8,
     FIX9], and method-level agreement is low across agents in general [FIX4, FIX5].
3. **Adjudicate every patch that is off the gold set or has no test.**
   - Labels: *correct*, *valid alternative*, *symptom-only*, *wrong*, *no change*.
   - Blind the raters: strip `mcp__codegraph__*` tool names and graph IDs from what they see.
   - Two raters on ≥ 15 tasks; report Cohen's kappa; resolve disagreements by discussion.
4. **LLM judge: secondary only.** Blind it the same way, calibrate it against ≥ 20 human-labelled patches, and
   report the agreement [EVAL6].

**Success (P2)** = the tests pass (when runnable) AND the adjudication is *correct* or *valid alternative*.
Passing tests alone is not enough [FIX6, FIX7].

### 3.5 Statistics

- **Unit and pairing.** The unit is the task, and the arms are paired by task. If tasks cluster (e.g., by product
  area), use clustered standard errors [EVAL1].
- **Binary outcomes** (success, gold hit): exact McNemar on the discordant pairs [EVAL12], or the paired Bayesian
  method [EVAL2]. Report the difference with a 95 % interval, not only a p-value.
- **Continuous outcomes** (cost, calls, time, files): Wilcoxon signed-rank on log(B/A).
  - Report the median ratio.
  - Below about 30 tasks per slice, use exact or Bayesian intervals instead of bootstrap or CLT [EVAL2].
- **Sample size.** Fix n before the run and don't peek, or use a sequential rule planned in advance [EVAL13].
- **Noise floor.** Treat rate differences under about 3 pp as noise [EVAL5].
- **Repeats.** First run every task once (K = 1). Then, if the budget allows, run all tasks a second time (K = 2)
  to measure run-to-run noise and report pass^2 [EVAL1, EVAL3, EVAL4]. Never re-run only the surprising tasks.

### 3.6 What n can show (our calculation)

Assumptions: normal approximation, 80 % power, α = 0.05, and 20–30 % of tasks discordant between arms (one arm
succeeds, the other fails).

| Paired tasks | Smallest detectable success difference (two-sided) | Non-inferiority margin provable if the true difference is 0 (one-sided) |
|---|---|---|
| 40 | 20–24 pp | 18–22 pp |
| 60 | 16–20 pp | 14–18 pp |
| 80 | 14–17 pp | 12–15 pp |

Implications:

- With the existing 60 PR tasks, small correctness differences cannot be shown either way.
- Report correctness as an interval and a non-inferiority statement. Never write "no difference" based on a
  non-significant test.
- Efficiency effects are large enough to detect at small n. The pilot measured −6.5 tool calls, 95 % CI (−10.5,
  −3.0), on 12 tasks. Lead with efficiency (P1), noise (C1) and capability (C5).

### 3.7 Budget

The pilot cost about $2 and 15 minutes per paired task.

| Item | Runs | Cost | Wall time |
|---|---|---|---|
| Layer 1 (W1, W2, W4 offline) | — | $0 | hours of compute |
| PR slice, K = 1 | 60 pairs | ≈ $120 | ≈ 15 h |
| Log slices (10 trivial + 20 hard) | 30 pairs | ≈ $60 | ≈ 7.5 h |
| Capability slice | 20 pairs | ≈ $40 | ≈ 5 h |
| Optional arm C on 30 tasks | 30 single runs | ≈ $30 | ≈ 4 h |
| Optional K = 2 repeat of the PR slice | 60 pairs | ≈ $120 | ≈ 15 h |

### 3.8 Pre-registration

Create `bench/preregistration.yaml` and commit it before the first v3 run. Its commit hash goes in the report.

```yaml
version: 3
frozen_at: "<date>"                 # before any v3 run
model: "<pinned model id>"
claude_code: "<pinned version>"
budget_usd_per_run: 3
max_turns: 30
arms: [A, B]                        # add C if run
tasks:
  pr: {n: 60, ids_sha256: "<hash of the sorted task ids>"}
  log_grep_trivial: {n: 10, ids_sha256: "..."}
  log_grep_hard: {n: 20, ids_sha256: "..."}
  capability: {n: 20, ids_sha256: "..."}
primary:
  - {name: cost_log_ratio, test: wilcoxon_signed_rank}
  - {name: success, test: exact_mcnemar, non_inferiority_margin: 0.15}
secondary: [tool_calls, files_read, tokens, wall_time, first_gold_step, gold_file_hit,
            gold_method_recall, char_precision, hallucinated_symbols, graph_output_chars]
multiplicity: holm
slices: [names_symbol, quotes_log, grep_hardness, crosses_projects, cross_language, area, tests_runnable]
repeats: {k: 1}
stopping: fixed_n
```

### 3.9 Reporting rules

- Report each slice on its own, never pooled into the headline. Give every metric with its interval.
- Include a tool-usage table: calls, characters and errors per tool per arm, plus the number of B runs with zero
  graph calls. Those runs stay in the analysis (intention to treat) and are also listed separately [H2, H3].
- For every discordant task, add a one-line human reading of both transcripts.
- Wording:
  - efficiency: "fewer / cheaper / faster, with CI";
  - fix quality: "not worse than A by more than m pp";
  - never "no difference".

## 4. Threats to validity

| Threat | Mitigation |
|---|---|
| Prompt asymmetry: the usage guide is only in arm B | keep it short and factual; give arm A an equally long generic "how to search" note |
| Index and code out of sync | index at the task's parent commit (existing) |
| Model memorization of the code | low on internal code; published numbers are inflated by it [LOC17] |
| PR-derived task selection bias | stratify; report covariate splits, never pooled averages only |
| Agent doesn't use the tools | per-task graph-call counts; intention-to-treat analysis [H2, H3] |
| Infrastructure noise and outages | same machine, interleaved arms, re-run failures in both arms [EVAL5] |
| Judge bias | blinding, human calibration, judge reported as secondary only [EVAL6] |
