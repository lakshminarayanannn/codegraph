# Improvement plan: what to build next, and why

Audience: the implementation agent and reviewers. Read [README.md](README.md) first. Every "why" cites an entry in
[EVIDENCE.md](EVIDENCE.md) by ID ([LOC1], [H4], ...). [EVAL_PROTOCOL.md](EVAL_PROTOCOL.md) defines how each item
is measured.

## 0. Ground rules

1. **The claim we are building toward:**
   - the graph gives the agent **less noise** (fewer wrong hits, less to read);
   - it gives **lower cost and time** for the same outcome;
   - it gives **answers grep cannot give**: C# → C++/CLI → C++ paths, log lines whose text is not in the source,
     and calls through interfaces, factories and events.

   We do **not** claim more correct fixes from better search; nobody has shown a large gain of that kind
   [H1–H6, FIX1–FIX3]. Fix quality is shown "not worse" (non-inferiority) and is improved through test
   execution (W6).
2. **SPEC principles stay:** deterministic and local, no source code leaves the machine, no embeddings or LLM in
   indexing, provenance (`file:line`, confidence) on every fact, config-driven (no codebase-specific code),
   read-only MCP tools.
3. **Every item ships with its measurement** (Layer 1 = deterministic, no LLM; Layer 2 = agent A/B, see
   EVAL_PROTOCOL.md). An item is done when its number is in the report.
4. **No product, customer or company names in this repository;** use generic terms.

## 1. Starting point (assumed)

This plan assumes the team's working branch as described in the internal review (Oct 2026):

| Area | State |
|---|---|
| Extraction | Roslyn, bridge and native extractors run on the real codebase. Precision/recall vs Roslyn `SymbolFinder` 1.00/0.99. Also: implicit calls, factory narrowing, RTA-lite dispatch, event/async continuation edges, log-wrapper inference, C++/CLI log templates from IL, statement order and conditions (`seq`/`cond`) on body edges |
| Runtime overlay | one recorded session (~70k lines) → OBSERVED / PRECEDES / INFERRED_PATH. Log matcher: 85.9 % exact, 89.6 % including ambiguous |
| Serving | 37 MCP tools including `context_pack`, `message_sequence`, `trace_path`, `scenarios`, `creators_of`; typical outputs 7–15k characters |
| Eval | 60 PR-derived tasks and 18 log-lineage tasks (10 grep-trivial, 8 grep-hard); paired two-arm runner; bootstrap report with covariates. The baseline arm had Read/Grep/Glob/Edit/Write and no shell, so **neither arm could run tests** |
| Results | Graph arm: −28 % tool calls, −26 % files read, +0.14 file precision on grep-trivial log tasks. Finding the code: no change. Edited the PR's method: 0.27 in both arms. In v1 the graph arm took 1.7 more steps to reach the first true method |

**On the base prototype** in this repository (commit `eda2649`), the anchor files are:

- `extractors/CodeGraph.Roslyn/Accuracy.cs`: oracle harness, `--accuracy N`;
- `extractors/CodeGraph.Roslyn/Templates.cs`: log templates;
- `logmatch/`: the matcher;
- `mcp_server/tools.py` and `mcp_server/server.py`: the tools;
- `codegraph_common/schema.py`: indexes, including full-text;
- `loader/merge.py`: derived properties;
- `bench/run_bench.py`: the agent A/B runner.

The working branch may use different file names; find the equivalent.

## 2. Priorities

| ID | Work item | Why | Effort | Payoff | Measured by |
|---|---|---|---|---|---|
| **P0** | **Prove it: cheap and deterministic** | | | | |
| W1 | Grep-noise benchmark for "who calls / uses X" | [H2] [H4] | S–M | high: the headline number | Layer 1 |
| W2 | Log-line grep-hardness census on a real session | [LOG7] [LOG8] [LOG9] [LOG10] | S | high | Layer 1 |
| W3 | Fix the scoring and statistics of the agent eval | [FIX1]–[FIX11] [EVAL1]–[EVAL13] | S | high: makes later numbers defensible | Layer 2 |
| **P1** | **Improve the agent's results** | | | | |
| W4 | `locate`: free text (issue, log lines, stack trace) → ranked methods with code | [LOC1] [LOC9] [LOC14] [TOOL1] | M | high | Layer 1 (Acc@k), then Layer 2 |
| W5 | Output budgets, code at each hit, pagination, small default tool set, CLAUDE.md guide | [TOOL1]–[TOOL4] [TOOL7] [H3] [H4] | S–M | medium–high | Layer 2 |
| W6 | Execution: both arms may run tests; runnable `tests_reaching`; reproduce-first steering | [LOC12] [LOC13] [FIX1] [FIX2] [FIX6] [FIX7] | S–M | highest for fix quality | Layer 2 |
| **P2** | **Logs: the graph's clearest edge** | | | | |
| W7 | Interceptor Begin/End lines → per-thread observed call tree as anchors | [LOG14] [LOG3] [LOG4] | S | medium | W2 census |
| W8 | Sequence-aware disambiguation of identical templates in sibling methods | [LOG2] [LOG5] [LOG6] [LOG8] | M | medium | W2 census |
| W9 | Build drift: match against the build that wrote the log | [LOG10] [LOG8] [LOG9] | M | medium–high | W2 census |
| W10 | Message text built outside the log call (helpers, StringBuilder, ToString) | [LOG1] [LOG3] [LOG7] [LOG11] | M | medium | W2 census |
| **P3** | **Experiments** | | | | |
| W11 | Third eval arm: grep + Claude Code LSP plugins | [TOOL5] [TOOL6] [TOOL13] [H4] | S | answers "why not just LSP?" | Layer 2 |
| W12 | "Upstream cause" hints next to a symptom | [FIX10] (weak) | S | unknown | ablation |
| W13 | Latency: measure before optimizing | pilot | S | low–medium | transcripts |

**Order:** W1 → W2 → W3 → W5 → W4 → W6 → W7–W10 → W11–W13.

W1–W3 produce the first defensible proof report. Apart from re-scoring existing transcripts, they need no new
agent runs.

---

## 3. Work items

### W1 · Grep-noise benchmark: how noisy is grep, exactly?

**Why.** The premise "grep has a lot of noise" can be measured directly, without an agent, for the question agents
ask most: *who calls / uses this?* Retrieval-level measurements are where structured tools show large, repeatable
gains:

- on Sourcegraph's benchmark, precision@5 went from 0.140 to 0.478 while task results moved little [H2];
- on find-references tasks, a language server had precision 1.00 vs 0.76 for grep, at equal recall [H4].

The extractor already has the oracle (Roslyn `SymbolFinder.FindReferencesAsync`) and a seeded sampling harness
(`--accuracy`).

**What to build.** A `--grep-noise N` mode next to `--accuracy`. It uses the same solution load, the same seeded
sampling and the same oracle.

1. **Sample** N methods (default 300), seeded and stratified. Report the size of each stratum.
   - S1: unique short name (no other indexed method has it).
   - S2: common short name (≥ 5 indexed methods share it).
   - S3: overloaded within its type.
   - S4: interface, abstract, virtual or override (called through dispatch).
   - S5: constructors (grep has to use the type name).
   - S6: members invoked implicitly (operators, conversions, `GetEnumerator`, `Dispose`, `Deconstruct`); expect
     grep recall below 1.
2. **Grep arm (what an agent does).** For method `M` with short name `n` (for a constructor, the type name), take
   every whole-word occurrence of `n` in the raw text of all indexed `.cs` files, comments and strings included.
   This is equivalent to `rg -n -w n -g '*.cs'`. Build one identifier-position index over the raw text so the
   cost is proportional to the hits. Also report two "smarter grep" variants: `n\s*\(` (call-shaped) and
   `\.n\s*\(` (member call).
3. **Classify every hit** with Roslyn (the documents are already loaded):

   | Class | Rule |
   |---|---|
   | `COMMENT` | inside trivia: comment, doc comment, disabled `#if` text |
   | `STRING` | inside a string, char or interpolated-text token |
   | `DOC_OR_NAMEOF` | inside a `cref` or `nameof(...)` (the oracle skips these) |
   | `DECLARATION` | the declaration of `M` or of a member of its override/implementation family |
   | `TRUE_REFERENCE` | the identifier binds (`GetSymbolInfo`, including candidate symbols) to `M` or to a declaration in `UpChain(M)`, the same rule the oracle uses |
   | `SAME_NAME_OTHER` | binds to a different symbol with the same name |

   Also classify per **line**, because the agent reads lines: a line is true if any hit on it is
   `TRUE_REFERENCE`. For names with more than 2,000 hits, classify a seeded sample of 2,000 and scale (report the
   exact totals).
4. **Grep recall** = oracle reference locations on a line with a grep hit ÷ all oracle reference locations. Grep
   misses calls that never spell the name: implicit calls, operators, `foreach`, `using`, `await`.
5. **Volume**:
   - the bytes of `path:line:text` output that `rg -n` would print;
   - the number of distinct files with hits, i.e. the files the agent must open to check.
6. **Graph arm**:
   - the graph's depth-1 callers (existing accuracy logic): precision and recall vs the oracle;
   - the byte size of the `callers_of(symbol, max_depth=1)` response. A small Python step calls the tool function
     for each sampled ID against Neo4j.
7. **Output**: `out/csharp/grep_noise.json` holds, per method, the stratum, counts per class, precision, recall,
   bytes and files, plus the graph's precision, recall and bytes. A Markdown table per stratum gives median, p90
   and 95 % intervals. The report generates the headline automatically:

   > For N sampled methods, grep returned a median of X lines (p90 Y) across Z files. P % of those lines were
   > not uses of the method (comments Pc %, strings Ps %, other symbols with the same name Po %), and grep missed
   > R % of real uses. The graph returned the compiler's callers (precision p, recall r) in B× fewer bytes.

**W1b, native and cross-language.**

- Repeat steps 2–7 for native functions using a libclang oracle: resolve `DeclRefExpr`/`MemberRefExpr` cursors to
  the target USR in the translation units the native extractor already parses.
- For C# → C++/CLI → C++ chains there is no external oracle. Hand-verify 20 chains from `path_to_native` and turn
  them into capability tasks for the agent eval (EVAL_PROTOCOL §3.2).

**Where (base prototype):**

- `extractors/CodeGraph.Roslyn/Accuracy.cs`, plus the flag in `Program.cs`;
- report script `bench/grep_noise_report.py`;
- results in `docs/ACCURACY.md`.

**Acceptance.**

- **Fixture test.** Add a common-name case to the fixture: `Move` on three unrelated types, plus one mention in a
  comment, one in a string and one interface call. The test asserts the exact count per class.
- **Real code.** N = 300 completes in the project-scoped accuracy run. Proportions are reported with Wilson 95 %
  intervals and medians with a bootstrap over methods.
- The headline sentence is in the report and in `docs/ACCURACY.md`.

**Pitfalls.**

- Partial classes and generated files: count them separately.
- Extension methods: bind through `ReducedFrom`, as the extractor normalizes.
- Names used in XAML or config files are outside `.cs` files. Optionally report an all-text-files variant.

### W2 · Log-line grep-hardness census

**Why.** The pilot's log tasks were grep-trivial by construction (the literal text is in 1–3 files), so both arms
solved them in one step. What matters is the share of *real* lines that grep cannot place:

- a production log-to-code tool's few misses came from custom logging methods, messages built outside the log
  statement, and JSON-only messages [LOG7];
- logs written by an older build drift from the current code [LOG8, LOG10];
- logging statements change often [LOG9].

**What to build.** `bench/log_census.py --session <file> --config <cfg>`:

1. **Dedupe** runtime messages by `(category, text with digits / hex / GUIDs masked)`. For each distinct message,
   derive the query an agent would grep for: the longest run of the message without digits, quotes or hex, at
   least 12 characters. Fall back to the three longest words. Search it as a fixed string in all product source
   files.
2. **Grep class**, using the eval's grep-hardness definitions:

   | Class | Rule |
   |---|---|
   | `unique` | the query occurs in 1–3 files |
   | `ambiguous` | the query occurs in ≥ 4 files |
   | `absent` | the query occurs in no file (wrapper-built, concatenated or drifted) |
   | `short` | the message has no run of ≥ 12 characters |
3. **Matcher outcome** for the same message: `exact`, `ambiguous` (k candidates), `drift` (fallback match) or
   `none`.
4. **Truth.** Draw a seeded sample of 100–200 messages, stratified by grep class. Two people label the true
   emitting method; report Cohen's kappa.
5. **Report**, per grep class:
   - the share of lines and the share of distinct messages;
   - the matcher's exact, ambiguous and none rates;
   - matcher precision on the labelled sample, with a Wilson 95 % interval;
   - on the labelled sample, how often grep's hit list contains the right file.

**Acceptance.**

- The census re-runs deterministically from a session file and an index.
- It states what share of real lines are grep-hard and how the matcher does on them. These are the denominators
  for W7–W10.

### W3 · Fix the scoring and statistics of the agent eval

Full rules are in EVAL_PROTOCOL.md §3.4–3.6. The implementation work:

1. **Gold sets.** Take the PR's non-test methods and remove unrelated (tangled) changes [FIX11]. Report:
   - gold-file hit;
   - any gold method touched;
   - gold-method recall.

   "Edited the same method as the PR" becomes a diagnostic, not the success measure [LOC13, FIX4–FIX9].
2. **Adjudication** of off-gold or untested patches:
   - labels: correct / valid alternative / symptom-only / wrong / no change;
   - blinded: strip the `mcp__codegraph__*` tool names and graph IDs from what raters see;
   - two raters on ≥ 15 tasks; report kappa.
3. **Statistics:**
   - exact McNemar or a paired Bayesian test for binary outcomes;
   - Wilcoxon signed-rank on log-ratios for cost, calls and time;
   - Wilson or Bayesian intervals at small n;
   - pre-registered primary metrics and a fixed n (`bench/preregistration.yaml`);
   - per-slice reporting [EVAL1, EVAL2, EVAL12, EVAL13].
4. **Tool-usage table** per run: calls, characters returned and errors, per tool [H2, H3].

On the base prototype, `bench/run_bench.py` already records tool calls, tokens and time per arm. Extend its
`score()`/`report()`, or replace it with the working branch's runner.

### W4 · `locate`: free text → ranked methods with code

**Why.**

- In LocAgent's ablation, removing the entity-search tool dropped function-level accuracy from 71.5 % to
  53.3 %; removing graph traversal dropped it only to 66.1 % [LOC1]. Finding the *start* matters more than
  walking the graph.
- A retriever plus reranker beat agent-based localization at a fraction of the cost [LOC9].
- One summarized search beat iterative search [LOC14].
- Anthropic recommends consolidating multi-step workflows into one tool [TOOL1].
- In the pilot, the graph arm took 1.7 more steps to reach the first true method: it navigated before it had a
  start.

**What to build.** A tool `locate(text, k=5, include_code=true, budget_chars=4000)`:

1. **Parse the input:**
   - identifiers: `PascalCase`/`camelCase` tokens, `Type.Member`, `ns::fn`;
   - file paths and quoted strings;
   - exception type names;
   - stack frames (`at Ns.Type.Method(...) in path:line`);
   - log lines (structured JSON fields, or the configured prefix regex).
2. **Anchors** (high precision, scored first):
   - stack frames → methods;
   - log lines → `find_log_source` (exact ranks above ambiguous);
   - exception messages → `find_exception_source`;
   - exact `fqn` or `Type.Member` mentions → the resolver.
3. **Lexical candidates.** Full-text search over a per-method document with these fields:
   - name words, split from camelCase (`HomeAsync` → `home async`);
   - containing-type words and namespace words;
   - doc summary;
   - emitted log and exception template text.

   Compute the derived properties (`nameWords`, `typeWords`, `emitsText`) at load time and add a full-text index
   over them. The base prototype's `symbol_text` covers only `name` and `fqn`. Query with Lucene field boosts,
   e.g. `nameWords:(...)^4 typeWords:(...)^2 docSummary:(...) emitsText:(...)`.
4. **Graph re-rank.**
   - For each candidate, add Σ over anchors of w / (1 + d), where d is the undirected hop distance (≤ 2) over
     CALLS / DISPATCHES_TO / CREATES / NOTIFIES.
   - Run a bounded BFS from the anchors, reusing the traverser's neighbour cache.
   - Penalize tests, generated code and externals unless the text asks for them.
5. **Output**, within `budget_chars`, per candidate:
   - `fqn`, signature and `file:line`;
   - 3–8 lines of code: the declaration line and the matching lines, read from `repo_root`. Mark `stale` when the
     file hash differs from the indexed hash;
   - `why`: the signals that fired, with their scores;
   - `confidence`;
   - a suggested next call, `context_pack(id)`.
6. **Deterministic:** same input and same index give the same output. No embeddings.

**Where (base prototype):**

- `mcp_server/locate.py` (new);
- `mcp_server/server.py`: register the tool and list it first in `INSTRUCTIONS`;
- `codegraph_common/schema.py`: the new full-text index;
- `loader/merge.py`: the derived properties;
- fixture tests.

**Acceptance, offline first (Layer 1).** Use the 60 PR tasks:

- input = the task text the agent sees; truth = the gold methods and files;
- report Acc@1, Acc@5 and Acc@10 at file and method level, both all-gold-in-top-k and any-gold-in-top-k, split
  by the "names a symbol?" covariate;
- compare against two baselines: BM25 over file text (a grep-like retriever) and `find_symbol` full text.

Make `locate` the recommended first call only if method Acc@5 beats the BM25 baseline in an exact McNemar test at
p < 0.05 [EVAL12]. After that, measure first-true-method step and tool calls in Layer 2.

**Risks.**

- Issue texts that name no identifier are the hard case.
- Published benchmarks overstate localization because models memorize popular repositories [LOC17, LOC18].
  Expect lower absolute numbers on internal code.

### W5 · Output shape, default tool set, CLAUDE.md guide

**Why.**

- Anthropic's tool-design advice [TOOL1]: offer concise vs detailed responses (72 vs 206 tokens in their
  example), paginate and truncate with sensible defaults, and prefer readable names.
- Claude Code warns above 10,000 tokens per MCP output [TOOL4].
- "Bloated tool sets" are a common failure mode [TOOL2]; tool search improved accuracy on large tool sets
  [TOOL3].
- An LSP tool that returned only locations made the agent open 15.2 files per episode, vs 3.2 when the code text
  came with each hit [H4].
- Agents under-use advanced tools: 8 Deep Search calls in 602 runs [H2]; two models never called IDE search in
  675 trajectories [H3].
- In the pilot, graph outputs averaged 7–15k characters per call.

**What to build.**

1. Every list-returning tool takes:
   - `detail="concise"|"full"`, default concise;
   - `budget_chars`, default 4,000;
   - `cursor`. When truncated, the response ends with `{"more": n, "cursor": "..."}`.
2. Concise items carry `fqn`, `file:line`, the one source line at the call or use site (`code`), the relation and
   the confidence. Include IDs only where a follow-up needs them; the resolver already accepts `fqn`.
3. Rank list items in this order:
   - direct before transitive;
   - product code before tests, unless the tool is about tests;
   - exact before heuristic;
   - then by name.
4. Default profile of about 10 core tools: `locate`, `context_pack`, `find_symbol`, `callers_of`, `callees_of`,
   `implementations_of`, `impact_of_change`, `tests_reaching`, `find_log_source`, `trace_path` (or
   `path_to_native`). Everything else goes behind `--profile full`. Status tools leave the default profile, since
   every result already carries `index_commit`/`stale`.
5. Tool descriptions say *when* to prefer the tool over grep:
   - names shared by many symbols;
   - calls through interfaces, factories or events;
   - C# → C++/CLI → C++;
   - a log line whose text is not in the source.
6. Ship a short `CLAUDE.md` snippet for the target repository. It covers when to use which graph tool, and that
   grep stays fine for unique literals [TOOL7]. That way the guidance also exists outside the eval prompt.

**Acceptance.**

- In Layer 2 transcripts, the p90 graph-tool output is ≤ 4,000 characters.
- Usage share per tool is reported.
- A tool-set ablation (core vs full) on ≥ 20 tasks shows no loss in success and no rise in cost.

### W6 · Execution support for fix quality

**Why.**

- In published systems, fix correctness moves with execution, not retrieval:
  - reproduction tests and test-based patch selection [LOC13];
  - spectrum-based fault localization, measured together with test-based validation [LOC12];
  - search with patch evaluation [LOC15].
- Locations alone fall short:
  - handed the developer's exact method, agents still fixed only about 1 in 4 of their method-level misses
    [FIX1];
  - with the gold files, 39–53 % of tasks still failed [FIX2].
- Passing tests is not proof either:
  - 73 % of machine-reported bugs got a test-passing patch, but only 43 % got one equivalent to the developer's
    fix [FIX7];
  - 29.6 % of test-passing patches behave differently from the gold patch [FIX6].
- In the pilot neither arm could run anything, which caps fix quality in both arms and leaves `tests_reaching`
  with no use.

**What to build.**

1. **Eval permission.** Allow both arms the test runner as a single allow-listed command, e.g.
   `Bash(vstest.console.exe:*)` or `Bash(dotnet test:*)`, wherever the environment can run the relevant tests.
   Record which tasks have runnable tests.
2. **Runnable `tests_reaching`.** Add a config `test_runner.command` template, e.g.
   `vstest.console.exe {assembly} /Tests:{tests}`, and fill it per test assembly. Order tests nearest first, cap
   the list, and flag "no test reaches this".
3. **`verify_plan(symbols | base_ref)`.** Return the tests reaching the changed methods (via `diff_impact`) as
   runnable commands, plus the changed methods no test reaches.
4. **Steering** (both arms get the same execution permission; only the graph arm gets the tools):
   1. reproduce: find or write a failing test;
   2. fix;
   3. run the tests that reach the change;
   4. with several candidate fixes, keep the one whose tests pass.
5. **Optional SBFL.** When per-test coverage exists, compute an Ochiai suspiciousness score from failing and
   passing tests and use it as a `locate` signal [LOC12].

**Acceptance.**

- On tasks with runnable PR tests, report resolution per arm (PR tests pass and the other reaching tests still
  pass), using the protocol's statistics.
- On the fixture, the commands `tests_reaching` returns actually run.

### W7 · Interceptor Begin/End lines → per-thread call tree

**Why.** Interceptor lines name their method, so they need no template matching. Paired per thread, they give an
*observed call tree*: the strongest anchor for every other line inside the bracket. Monitoring frameworks record
exactly this (trace ID, execution order index, call depth) [LOG14]. Request-flow reconstruction from logs relies
on such anchors [LOG3, LOG4].

**What to build.** The working branch already resolves Begin/End lines to methods directly. Add:

- per thread, push on Begin and pop on End; on a missing End, pop to the matching frame;
- observed call-tree edges (parent → child, with count and median duration);
- during matching, a line inside the bracket of method X prefers candidates in X, or reachable from X within 2
  call hops.

**Acceptance.** On the session, report:

- the share of non-interceptor lines that fall inside a bracket;
- how many ambiguous matches become exact;
- precision on the W2 labelled sample, which must not drop.

### W8 · Identical templates in sibling methods

**Why.** Path-based disambiguation is the classic answer:

- SherLog keeps only log points consistent with the log sequence [LOG2];
- LogCoCo matches per-thread sequences against code paths [LOG6];
- Pathidea searches the call graph between consecutive log lines [LOG8];
- Pensieve prefers the candidate whose logged values overlap other lines [LOG5];
- lprof prefers the template with the longest constant text [LOG3].

**What to build.** The working branch has nearest-unambiguous-neighbour context (1 call hop) and an LCS
tie-break. Score each candidate c for line ℓ on thread t:

- **bracket fit** (W7);
- **order fit**: if the previous anchor on t is in method X at statement order s, prefer c = X with order > s, or
  c reachable from X within 2 hops. Use the `seq`/`cond` metadata on EMITS edges;
- **successor fit**: the same test toward the next anchor;
- **value overlap**: values filling c's holes that also appear in neighbouring lines;
- **literal length**: more constant text wins.

Return ranked candidates with scores. Report "ambiguous" when the top two are within a margin, and never discard
candidates.

**Acceptance.** On the ambiguous lines (about 2,500 in the pilot session), report:

- top-1 precision on a labelled sample of ≥ 100 lines;
- the share still ambiguous.

Lines that were already exact must not lose precision.

### W9 · Build drift

**Why.**

- COCA matches logs against the code version the issue was reported on [LOG10].
- Pathidea deliberately falls back to file level because reported logs may come from older versions [LOG8].
- 20–45 % of logging statements change [LOG9].

**What to build.** The working branch has an ordered-literal-segment fallback. Add:

1. **Build identification.** A config `build_id` rule (a regex over session header lines or file names) maps to a
   commit, through release tags or a build manifest.
2. **Template snapshots per shipped build.** Export Message nodes, emitter IDs and regexes per indexed release
   commit. They are small, and the per-commit managed-only index already exists for the eval.
3. **Matching order.** Match against the session's build first, then against the current index.
   - Map old emitters to current methods by ID, then by git rename history.
   - Mark the result `drift: true` and lower its confidence.
   - When unsure, answer at type or file level rather than guess a method.

**Acceptance.** On the drifted lines (about 2,400 in the pilot session), compare match rate and labelled
precision between the build-matched snapshot and the current-index fallback.

### W10 · Message text built outside the log call

**Why.**

- Source-based template extraction expands `toString()` through the type hierarchy and handles wrappers [LOG1].
- lprof follows `toString()` and StringBuilder [LOG3].
- LLM-SrcLog follows log arguments into helpers' return values [LOG11].
- Custom logging methods and messages built outside the log statement were among the causes of a production
  log-to-code tool's misses [LOG7].

**What to build.** The working branch infers wrappers that forward a string parameter. Add the following to the
template builder (base prototype: `extractors/CodeGraph.Roslyn/Templates.cs`, `Builder.Append`):

1. **Product helpers returning strings.** When a log argument, or part of a concatenation, is a call to a product
   method whose return expressions can all be templated, inline those templates as alternatives. Limit: ≤ 8 per
   log point, depth ≤ 2.
2. **Local `StringBuilder`.** `Append` / `AppendFormat` / `AppendLine` chains on a builder that does not escape the
   method become one template.
3. **Local string variables** assigned before the log call become alternatives, one per branch.
4. **`ToString()` overrides** of product types used as holes are inlined as sub-templates.

**Acceptance.**

- The unmatched-line count on the session drops.
- Labelled precision is unchanged.
- Template growth is bounded and reported.

### W11 · Third eval arm: grep + LSP plugins

**Why.** The obvious reviewer question is "why not just a language server?"

- Claude Code has LSP plugins for C# (`csharp-lsp` → csharp-ls) and C/C++ (`clangd-lsp` → clangd) [TOOL5].
- csharp-ls needs the .NET 10 SDK and supports .NET Framework 4.8 projects, but legacy-csproj support is
  undocumented [TOOL6]. The legacy-project loading problems the working branch solved may come back.
- clang-cl cannot compile `/clr` code, so clangd does not cover C++/CLI [TOOL13].
- A small study found LSP precision 1.00 vs 0.76 for grep on references, with no recall gain [H4].

**What to build.** Arm C = arm A + the two plugins, with the same prompt. Record setup time, load failures and
coverage. A failed setup on this codebase is a result, not a reason to skip the arm.

### W12 · "Upstream cause" hints (hypothesis)

**Why.** The pilot agent often finds the right method and then edits a caller or helper instead (or the reverse).
A workshop paper measured only about 40 % overlap between agents' fixes and developers' buggy lines, and argues
that test-based benchmarks reward patching the symptom at the crash site [FIX10]. This is weak evidence.

**What to build.** In `context_pack` and `locate`, for a symptom method (one that throws or logs the error), list
"upstream producers" as labelled candidates:

- writers of the fields read in the failing condition (from the `cond` metadata);
- callers passing the arguments, ≤ 2 hops.

Keep this behind an ablation switch, and drop it if Layer 2 shows no effect.

### W13 · Latency

The pilot saw +32 s wall time on log tasks. Before optimizing the server, split each tool call's time in the
transcripts into server time (tool_use → tool_result) and model time.

- If model turns dominate, the fix is fewer, more complete calls (W4, W5).
- If server time dominates, profile the slow queries. The base prototype's queries take 40 ms–0.9 s on a
  418k-line synthetic graph (docs/DECISIONS.md D-33).

---

## 4. What not to do

- **Don't replace grep.** Ship alongside it; the vendors who measured keep both [H1, TOOL5, TOOL12].
- **Don't return more context by default:**
  - flattened 2-hop context fell below the no-graph baseline [LOC4];
  - full-file views cost about 5 pp [LOC14];
  - expanding line context did not help [FIX3].
- **Don't let the model write Cypher.** It was worse with weaker models and used more tokens [LOC5].
- **Don't headline a composite score or "same method as the PR"** [FIX4–FIX9, EVAL6].
- **Don't start with HMM or Viterbi log alignment** before W7–W9. No published method maps logs onto a
  control-flow graph that way [LOG15], and the simpler anchors usually suffice.
- **Don't add embeddings to the core.** Deterministic lexical + graph ranking comes first (W4). A local reranker
  is an optional later experiment if W4 plateaus [LOC9].

## 5. Definition of done for the proof report

1. W1 table and headline sentence for C#, plus native if available.
2. W2 census with labelled precision.
3. Agent eval v3 under EVAL_PROTOCOL.md:
   - pre-registered metrics;
   - slices reported separately;
   - cost, time and calls with intervals;
   - fix quality as non-inferiority;
   - a capability slice (cross-language, grep-hard logs).
4. A one-page summary that states only what the numbers support.
