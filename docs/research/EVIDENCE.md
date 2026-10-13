# Evidence: what beats grep for coding agents

Survey of published evidence (industry and research, mostly 2024–2026, plus classic work on logs), collected in
October 2026. Each entry: source and link, status tag (see [README.md](README.md)), what was measured, the
numbers, and what it means for codegraph. IDs are referenced from IMPROVEMENT_PLAN.md and EVAL_PROTOCOL.md.

## Summary

| Approach | Best evidence | Finding code | Final result | Verdict |
|---|---|---|---|---|
| Semantic (embedding) search next to grep | Cursor A/B [H1] | +12.5 % answer accuracy (offline) | +0.3 % code retention; +2.6 % on repos with ≥ 1,000 files | real, small, grows with repo size |
| Precise code intelligence over MCP | Sourcegraph [H2] | precision@5 0.140 → 0.478; file recall 0.127 → 0.277 | small; cost −30 %, agent time −38 % | big retrieval gain, small task gain |
| IDE / LSP search tools | JetBrains [H3], LSP study [H4] | reference precision 1.00 vs 0.76 for grep | not default-on; agents often don't call them | precision on references |
| Code graph over MCP | Codebase-Memory [H5] | — | 83 % vs 92 % answer quality, 10× fewer tokens | cheaper, slightly worse |
| Hybrid BM25 + vector MCP | Zilliz [H7] | F1 0.40 vs 0.40 | −39.4 % tokens, −36.3 % tool calls | cheaper, same quality |
| Graph-guided localization agents | LocAgent [LOC1], CoSIL [LOC2], OrcaLoca [LOC3] | +7 to +19 pp localization | +3 to +6 pp resolve | modest |
| Plug-in repository graph | RepoGraph [LOC4] | — | +2.0 to +2.7 pp resolve | small |
| Giving the agent the right location | RGFL [FIX1], Loc2Repair [FIX2] | gold location given | most former misses still fail | the patch is the bottleneck |
| Execution (tests, coverage) | Agentless [LOC13], AutoCodeRover [LOC12] | — | largest single levers | do this |

Our internal pilot fits this pattern:

- the graph arm used about 28 % fewer tool calls and read about 26 % fewer files;
- both arms found the code about equally often;
- both arms edited the same method as the developer's PR in about 27 % of tasks.

---

## 1. Head-to-head: better tools vs grep for agents

<a id="h1"></a>
### H1 · Cursor: semantic search next to grep

`re-checked` · Cursor blog, 6 Nov 2025 · https://cursor.com/en-US/blog/semsearch

- **What:** Cursor's agent gets an embedding-based semantic search tool alongside grep (Cursor's own embedding
  model and index).
- **Numbers:**
  - Offline: "on average 12.5 % higher accuracy" (6.5 %–23.5 % depending on the model).
  - Online A/B: code retention +0.3 % overall and +2.6 % "on large codebases with 1,000 files or more".
  - With semantic search off, dissatisfied follow-up requests rose 2.2 %.
- **For us:** a real but small end-to-end gain that grows with codebase size. Cursor adds search to grep rather
  than replacing it.

<a id="h2"></a>
### H2 · Sourcegraph: CodeScaleBench

`re-checked` (retrieval, cost and time numbers; quote) · Sourcegraph blog, 3 Mar 2026, S. Jarmak ·
https://sourcegraph.com/blog/codescalebench-testing-coding-agents-on-large-codebases-and-multi-repo-software-engineering-tasks
· repo https://github.com/sourcegraph/CodeScaleBench

- **What:** 370 tasks in 9 languages; Claude Code + Haiku 4.5, with and without Sourcegraph's MCP tools.
- **Numbers:**
  - Precision@5 0.140 → 0.478; file recall 0.127 → 0.277.
  - Cost "$0.73 to $0.51, roughly a 30 % reduction"; agent phase "38 % shorter".
  - Tool usage: 4,813 keyword-search calls vs 587 natural-language searches; Deep Search "8 calls across 602
    MCP runs".
  - In the MCP arm the agent had no local copy of the source ("Source code isn't there"), so the arms differ in
    more than tools.
  - Their summary: "retrieval alone isn't everything".
  - The end-to-end reward change is given only per suite on the page; check the technical report in the repo.
- **For us:**
  - Retrieval metrics show large gains even when task metrics barely move, so measure retrieval separately
    (IMPROVEMENT_PLAN W1, W4).
  - Agents rarely call advanced tools, so log tool usage.

<a id="h3"></a>
### H3 · JetBrains: IDE-native search tools for agents

`re-checked` · JetBrains AI blog, May 2026, updated 29 Jul 2026, D. Shiryaev ·
https://blog.jetbrains.com/ai/2026/05/what-happens-when-you-give-agents-ide-native-seach-tools/

- **What:** gave agents IDE search over MCP.
- **Numbers:**
  - The 29 Jul update says the earlier gains were not caused by the tools: "the IDE MCP tools behind it were not
    actually called". The page still says "The quality, latency, and cost numbers are real".
  - GPT-5.4-mini and GPT-5.5 "did not call IDE search in any of their 675 trajectories".
  - GPT-5.6 median cost rose about 6 % (low reasoning) and 7 % (medium reasoning).
  - Verdict: "this setup has not earned a default-on rollout". Results are "strongest on Java and Kotlin".
- **For us:** adding a tool doesn't mean the agent uses it. Measure graph-tool calls per task and treat runs
  with zero calls separately.

<a id="h4"></a>
### H4 · Does a language server save tokens for coding agents?

`re-checked` · P. Xu, arXiv 2608.13568 (2026), "Does a Language Server Save Tokens for Coding Agents? A
Measurement Methodology and Preliminary Study" · https://arxiv.org/abs/2608.13568

- **What:** a small, preliminary study comparing grep with LSP tools on reference-finding and rename tasks.
- **Numbers (full text):**
  - Find references: precision 1.00 (LSP) vs 0.76 (grep), recall 0.66 vs 0.67, tokens +19 %.
  - On a noisy repository: F1 +0.245 with 12 % fewer tokens.
  - Files read per episode: LSP returning only locations 15.2; LSP returning locations with the code text 3.2;
    grep 4.3.
  - Renames (pass@1): grep 1.00, location-only LSP 0.67, text-inline LSP 0.83. The abstract states the rename
    result differently from its own table.
- **For us:** about 1 in 4 grep hits on references was noise. Tools must return the code line with each hit,
  or the agent reads more files than with grep.

<a id="h5"></a>
### H5 · Codebase-Memory: tree-sitter knowledge graph over MCP

`re-checked` · M. Vogel, F. Meyer-Eschenbach, S. Kohler, E. Grünewald, F. Balzer, arXiv 2603.27277 (2026) ·
https://arxiv.org/abs/2603.27277

- **Numbers:**
  - "83 % answer quality versus 92 % for a file-exploration agent, at ten times fewer tokens".
  - About 2.1× fewer tool calls (`from source`).
  - Tasks were code question answering, not bug fixing.
- **For us:** the same pattern as our pilot: a graph is much cheaper, but no better (here slightly worse) on
  quality.

<a id="h6"></a>
### H6 · The devil is in the interface

`re-checked` · X. Xu, H. Saghir, Q. Wu, M.-A. Côté et al., arXiv 2608.11386 (2026), "The Devil Is in the
Interface: Evaluating How Tool Architecture Shapes Coding Agent Behavior" · https://arxiv.org/abs/2608.11386

- **Numbers:**
  - One interface raised relevant-file access by "more than 11 %".
  - "Python CodeAct-style interfaces" matched bash-only task performance "with 41.6 % fewer steps and 56.3 %
    lower token usage".
- **For us:** how a tool is shaped (one call that does more, output form) changes cost as much as what it
  knows.

<a id="h7"></a>
### H7 · Zilliz Claude Context: hybrid BM25 + vector search MCP

`re-checked`, `vendor claim` · https://github.com/zilliztech/claude-context/blob/master/evaluation/README.md

- **Setup:** 30 SWE-bench Verified instances (15–60 min difficulty, exactly 2 files modified), GPT-4o-mini,
  3 runs per method.
- **Numbers:**
  - F1 "comparable" (0.40 vs 0.40).
  - Tokens −39.4 % (73,373 → 44,449); tool calls −36.3 % (8.3 → 5.3).
- **For us:** again efficiency, not quality.

<a id="h8"></a>
### H8 · Augment Context Engine MCP

`re-checked` (what the page says), `vendor claim` · blog, 6 Feb 2026 (updated 18 Jun 2026) ·
https://www.augmentcode.com/blog/context-engine-mcp-now-live

- **Claims:**
  - "80 % improvement" for Claude Code + Opus 4.5; "71 %" for Cursor + Opus 4.5; "30 %" for Cursor +
    Composer-1.
  - Scored on 300 Elasticsearch PRs × 3 prompts (900 attempts) across five quality dimensions.
  - How the dimensions are combined is not stated, and the grader is not disclosed.
- **For us:** not usable as evidence.

<a id="h9"></a>
### H9 · Greptile code-review benchmark

`re-checked`, `vendor claim` · July 2025 · https://www.greptile.com/benchmarks

- **Claims:**
  - 82 % bug catch rate vs Bugbot 58 %, Copilot 54 %, CodeRabbit 44 %, Graphite 6 %.
  - 50 bugs in Python, TypeScript, Go, Java and Ruby repositories (no C# or C++).
  - This is PR review, not agent navigation.

<a id="h10"></a>
### H10 · Anthropic: Claude Code chose agentic search (grep/glob) over RAG

`re-checked` · Latent Space podcast, 7 May 2025 · https://www.latent.space/p/claude-code · and The Pragmatic
Engineer, 4 Mar 2026 · https://newsletter.pragmaticengineer.com/p/building-claude-code-with-boris-cherny

- **What Boris Cherny said:**
  - Early versions used RAG; "eventually, we landed on just agentic search… it outperformed everything. By a
    lot."
  - On the evidence: "This was just vibes, so internal vibes. There's some internal benchmarks also, but mostly
    vibes."
  - Reasons given: indexes drift out of sync, and the index raises security issues.
- **For us:** grep is a strong baseline, and Anthropic's case for it is mostly qualitative. A precomputed index
  must stay fresh (index at the task's commit; incremental updates).

<a id="h11"></a>
### H11 · Cognition SWE-grep

`re-checked` · Cognition blog, 16 Oct 2025 · https://cognition.com/blog/swe-grep

- **What:** a retrieval subagent trained with multi-turn RL; 8 parallel tool calls per turn, 4 turns.
- **Numbers:**
  - Matches "the retrieval capabilities of frontier coding models, while taking an order of magnitude less
    time".
  - End to end it "accomplishes the same number of tasks in significantly lower end-to-end time".
- **For us:** speed and cost gains, not correctness gains.

<a id="h12"></a>
### H12 · Cursor: an index for regex search

`re-checked` · Cursor blog, 23 Mar 2026, V. Marti · https://cursor.com/blog/fast-regex-search

- **What:** in large monorepos "we routinely see `rg` invocations that take more than 15 seconds", so Cursor
  built a local index for regex search.
- **For us:** on very large repositories even grep needs an index.

---

## 2. Graph- and structure-guided code localization (research)

<a id="loc1"></a>
### LOC1 · LocAgent: graph-guided LLM agents for code localization

`re-checked` (headline and ablation) · Z. Chen et al., ACL 2025, arXiv 2503.09089 ·
https://arxiv.org/abs/2503.09089

- **What:** a heterogeneous code graph (contain / import / invoke / inherit) with SearchEntity, TraverseGraph
  and RetrieveEntity tools.
- **Numbers:**
  - SWE-bench Lite, function-level Acc@10: 77.37 % vs 70.07 % for OpenHands (both Claude-3.5).
  - Ablation (fine-tuned Qwen2.5-7B): removing SearchEntity drops function accuracy 71.53 → 53.28; removing
    TraverseGraph drops it only to 66.06.
  - Loc-Bench 60.7 % vs Agentless 42.7 %; downstream Pass@10 33.6 → 37.6 % (`from source`).
- **For us:** good search for the starting point matters more than graph walking (IMPROVEMENT_PLAN W4).

<a id="loc2"></a>
### LOC2 · CoSIL: iterative code-graph search for issue localization

`re-checked` · Z. Jiang et al., ASE 2025, arXiv 2503.22424 · https://arxiv.org/abs/2503.22424

- **Numbers:**
  - Top-1 function-level 43.3 % (Lite) / 44.6 % (Verified) vs Agentless 24.7 % (Qwen2.5-Coder-32B).
  - The abstract's "2.98–30.5 %" resolve gains are *relative* and measured against several baselines. Over
    plain Agentless the gain is +2.6 to +4.3 pp (e.g., 17.33 → 21.67 %).
  - Ablation: the iterative search mattered most (`from source`).

<a id="loc3"></a>
### LOC3 · OrcaLoca

`re-checked` · Z. Yu et al., ICML 2025 (PMLR 267), arXiv 2502.00350 · https://arxiv.org/abs/2502.00350

- **What:** priority-scheduled actions, action decomposition, distance-aware context pruning.
- **Numbers:** Lite function match 65.33 % vs Agentless-1.5 58.67 % (union of locations); resolved 41.0 % vs
  34.67 % (+6.33 pp).

<a id="loc4"></a>
### LOC4 · RepoGraph

`re-checked` · S. Ouyang et al., ICLR 2025, arXiv 2410.14684 · https://arxiv.org/abs/2410.14684

- **What:** a line-level repository graph added as a plug-in to existing agents.
- **Numbers:**
  - Lite +2.0 to +2.7 pp absolute (RAG +2.66, Agentless 27.33 → 29.67, AutoCodeRover +2.33, SWE-agent +2.00).
    Headline percentages in the paper are relative.
  - 1-hop context was best. Flattened 2-hop context scored 26.00, below the 27.33 baseline (summarized 2-hop:
    28.67).
- **For us:** more graph context can hurt. Keep outputs to one hop by default, ranked and summarized.

<a id="loc5"></a>
### LOC5 · CodexGraph: LLM writes Cypher over a Neo4j code graph

`re-checked` (setup and headline) · X. Liu et al., arXiv 2408.03910 · https://arxiv.org/abs/2408.03910

- **Numbers:**
  - A separate translation agent writes the Cypher from the main agent's natural-language queries.
  - Lite 22.96 % with GPT-4o, a tie with AutoCodeRover (also 22.96 %).
  - Much worse with weaker models, and about 35 % more tokens (`from source`).
- **For us:** keep fixed, parameterized tools. Don't let the model write queries.

<a id="loc6"></a>
### LOC6 · KGCompass: repository-aware knowledge graphs for repair

`re-checked` · B. Yang et al., arXiv 2503.21710 · https://arxiv.org/abs/2503.21710

- **What:** a knowledge graph linking issues and PRs to code entities; graph paths go into the repair prompt.
- **Numbers:**
  - Lite 58.3 % (175 of 300, Claude-4 Sonnet).
  - The knowledge graph raised resolved bugs from 91 (30.3 %) to 131 (43.7 %); entity paths took it to 142.
  - Voting and reproduction tests took it from 139 to 175.
- **Caveat:** the baseline does no repository search.

<a id="loc7"></a>
### LOC7 · LingmaAgent: repository knowledge graph summaries + MCTS

`from source` · Y. Ma et al., FSE 2025 Industry, arXiv 2406.01422 · https://arxiv.org/abs/2406.01422

- **Numbers:**
  - Lite 21.3 % vs SWE-agent 18.0 % (GPT-4-Turbo).
  - Removing the call graph cost only 1.7 pp; removing MCTS + summaries cost the most.

<a id="loc8"></a>
### LOC8 · CGM: Code Graph Model

`from source` · H. Tao et al., NeurIPS 2025, arXiv 2505.16901 · https://arxiv.org/abs/2505.16901

- **What:** graph-aware attention inside a fine-tuned open 72B model, plus graph RAG.
- **Numbers:** Lite 43.0 %.
- **For us:** graphs helped most when trained into the model, which isn't an option for us.

<a id="loc9"></a>
### LOC9 · SweRank: retrieve and rerank for localization

`re-checked` · R. G. Reddy et al., ICLR 2026, arXiv 2505.07849 · https://arxiv.org/abs/2505.07849

- **Numbers:**
  - Lite function-level Acc@10 88.69 % vs LocAgent (Claude-3.5) 77.37 %.
  - Cost $0.011–0.015 per issue vs $0.46–0.83 for agents: about 1–3 % (our arithmetic from Table 5).
- **For us:** a ranked retriever is a strong, cheap first step (IMPROVEMENT_PLAN W4).

<a id="loc10"></a>
### LOC10 · SHERLOC: structured diagnostic localization

`re-checked` · Tamoyan et al., EMNLP 2026 (main), arXiv 2606.24820 · https://arxiv.org/abs/2606.24820

- **Numbers:**
  - Verified file Recall@1 81.27 % vs 68.03 % for ToolTrain-32B (Qwen3-235B, 3-seed mean).
  - Resolve rate: unfiltered average +4.39 pp; the headline +5.95 pp takes the best variant per setting.

<a id="loc11"></a>
### LOC11 · ToolTrain: tool-integrated RL for repository search

`from source` · Z. Ma et al., arXiv 2508.03012 · https://arxiv.org/abs/2508.03012

- **What:** supervised fine-tuning plus RL on simple search tools.
- **Numbers:** Verified resolve 31.6 % vs Agentless 25.8 % and CoSIL 26.4 % (same patch model).

<a id="loc12"></a>
### LOC12 · AutoCodeRover

`re-checked` · Y. Zhang et al., ISSTA 2024, arXiv 2404.05427 · https://arxiv.org/abs/2404.05427

- **What:** AST-level code search APIs plus spectrum-based fault localization (SBFL: rank code by how often
  failing vs passing tests execute it).
- **Numbers:**
  - Lite 19.0 % (57 of 300) at $0.43 per issue.
  - With SBFL, resolved tasks rose 57 → 66 (19 → 22 %). That setting also gives the agent the developer-written
    tests for patch validation, so the two effects are mixed.
  - Outcomes, best of 3 runs:
    - 26.0 % resolved;
    - 29.3 % wrong patch at the correct method locations;
    - 20.0 % wrong location in the correct file;
    - 18.0 % wrong file;
    - 6.7 % no patch.
- **For us:** execution data (tests and coverage) is a localization signal.

<a id="loc13"></a>
### LOC13 · Agentless

`re-checked` · C. S. Xia et al., arXiv 2407.01489 (2024) · https://arxiv.org/abs/2407.01489

- **What:** a fixed pipeline: localize file → element → line, sample patches, select patches with tests.
- **Numbers:**
  - Lite 32.00 % (96 fixes) at $0.70 per issue.
  - Patch selection: majority voting gives 77 fixes; adding regression tests gives 81; adding reproduction tests
    gives 96.
  - Locations edited by submitted patches vs the gold patch: file 69.7 %, function 52.0 %, line 35.3 %.
- **Quote:** "it is possible to fix a bug in a different location than the ground truth, however comparing
  against the ground truth patch can still serve as an approximate measure."
- **For us:** test-based validation is the proven lever, and a fix in a different method from the PR can be
  correct.

<a id="loc14"></a>
### LOC14 · SWE-agent: agent-computer interface

`re-checked` · J. Yang et al., arXiv 2405.15793 (2024) · https://arxiv.org/abs/2405.15793

- **Numbers:**
  - Lite 18.0 % vs 11.0 % with a bare shell (GPT-4 Turbo).
  - Search ablation: one summarized search 18.0, iterative search 12.0, no search 15.7.
  - Showing full files instead of a window: 12.7 (−5.3 pp).
  - 52.0 % of unresolved instances were incorrect or overly specific implementations (labelled by GPT-4o,
    n = 248).
- **For us:** one concise ranked answer beats many small searches, and dumping whole files hurts.

<a id="loc15"></a>
### LOC15 · SWE-Search

`from source` · A. Antoniades et al., ICLR 2025, arXiv 2410.20285 · https://arxiv.org/abs/2410.20285

- **What:** Monte Carlo tree search with value feedback over the Moatless agent.
- **Numbers:** +23 % relative across 5 LLMs.

<a id="loc16"></a>
### LOC16 · CodePlan: repository-level edits planned with dependency and impact analysis

`from source` · R. Bairi et al., FSE 2024, arXiv 2309.12499 · https://arxiv.org/abs/2309.12499

- **What:** includes C# migrations.
- **Numbers:** 5 of 6 repositories pass validity checks vs 0 of 6 without planning.
- **For us:** impact analysis helps on multi-file changes. The evidence comes from migrations, not bug fixes.

<a id="loc17"></a>
### LOC17 · The SWE-Bench Illusion

`re-checked` · S. Liang, S. Garg, R. Zilouchian Moghaddam, arXiv 2506.12286 (2025) ·
https://arxiv.org/abs/2506.12286

- **Numbers:** from the issue text alone, models named buggy file paths with up to 76 % accuracy (o3, Verified),
  vs up to 53 % on repositories outside SWE-bench.
- **For us:**
  - Published localization numbers are inflated by memorization.
  - Expect lower absolute numbers on internal code, where memorization cannot help.

<a id="loc18"></a>
### LOC18 · MULocBench

`re-checked` · Zhang et al., "A Benchmark for Localizing Code and Non-Code Issues in Software Projects",
arXiv 2509.25242 · https://arxiv.org/abs/2509.25242

- **Numbers:** "even at the file level, performance metrics (Acc@5, F1) remain below 40 %". The best is 35.2 %
  (LocAgent, Claude-3.5).

<a id="loc19"></a>
### LOC19 · Surveys

`from source` · code localization and repository-level repair surveys: Jiang et al., arXiv 2512.22256 ·
https://arxiv.org/abs/2512.22256 · Li et al., arXiv 2601.11655 · https://arxiv.org/abs/2601.11655

---

## 3. Does better localization give better fixes? Is "same method as the developer" a fair score?

<a id="fix1"></a>
### FIX1 · RGFL: reasoning-guided fault localization

`re-checked` · Sepidband, Taherkhani, Pham, Hemmati, arXiv 2601.18044 · https://arxiv.org/abs/2601.18044

- **Numbers:**
  - Agentless resolve 51.6 % → 58.2 % on SWE-bench Verified (Gemini 2.5 Pro; 258 → 291 of 500).
  - Injecting the gold location into the 209 unresolved tasks (stacked: files, then elements, then lines)
    fixed:
    - 14 of 28 file-level misses (50 %);
    - 29 of 111 element-level misses (26 %);
    - 33 of 175 line-level misses (19 %).
- **For us:** when the agent missed the right method, handing it the right method still fixed only about 1 in 4
  cases. Writing the patch is the bottleneck.

<a id="fix2"></a>
### FIX2 · Loc2Repair

`re-checked` · Al Awad & Ivanov, GeCoIn 2026 workshop at IJCAI-ECAI 2026, arXiv 2606.30963 ·
https://arxiv.org/abs/2606.30963

- **Numbers:**
  - Resolve rate 44.7 % → 48.9–49.1 % with predicted files → 52.4 % with gold files (pooled over 3 models).
  - With gold files, 196–267 of 500 tasks still fail per model (39–53 %).

<a id="fix3"></a>
### FIX3 · On the role of fault-localization context for LLM-based program repair

`re-checked` · Sepidband, Pham, Hemmati, arXiv 2604.05481 · https://arxiv.org/abs/2604.05481

- **Numbers:**
  - 3.6 % (18 of 500) resolved with no file context vs 56.2 % (281 of 500) with the ground-truth files
    (GPT-5-mini).
  - LLM-retrieved elements beat call-graph expansion in 12 of 15 configurations (p = 0.0366).
  - Expanding line context gave no significant gain.
- **For us:** the right files matter enormously. Mechanically expanding along the call graph is worse than
  selective retrieval.

<a id="fix4"></a>
### FIX4 · On coding agent issue localization accuracy: an exploratory study

`re-checked` (abstract) · Coppola, Esposito, Kazman, Lenarduzzi, ESEM 2026, LIPIcs vol. 394, 59:1–59:13 ·
https://doi.org/10.4230/LIPIcs.ESEM.2026.59

- **What:** 2,441 issue-fixing commits from 10 large Java projects.
- **Numbers:** performance "progressively degraded at the class and method levels", with "consistently negative
  MCC values". We did not confirm specific method-level percentages; read the full paper before quoting any.
- **For us:** method-level agreement with the developer's fix is low across agents in general, not just ours.

<a id="fix5"></a>
### FIX5 · Understanding code agent behaviour: success and failure trajectories

`re-checked` · Majgaonkar, Fei, Li, Sarro et al., arXiv 2511.00197 (2025; venue not confirmed) ·
https://arxiv.org/abs/2511.00197

- **Numbers:**
  - "only about 27 % of successful patches modifying the same function as the gold patch" (21.3–33.5 % per
    agent and split, SWE-bench Lite and Verified).
  - Over 90 % of successful runs touch the gold files (92.2–97.3 %), but so do 72.5–81.4 % of failed runs on
    Lite (59.3–67.1 % on Verified).
- **For us:**
  - Our 0.27 same-method rate is what even *successful* agent patches show on SWE-bench.
  - Same-method agreement can't serve as the success measure.

<a id="fix6"></a>
### FIX6 · Are "solved issues" in SWE-bench really solved correctly?

`re-checked` · You Wang, M. Pradel, Zhongxin Liu, ICSE 2026, arXiv 2503.15223, DOI 10.1145/3744916.3764576 ·
https://arxiv.org/abs/2503.15223

- **Numbers:**
  - 29.6 % of test-passing patches behave differently from the gold patch (average over 3 tools).
  - Resolve rates are inflated by 6.2 pp per the abstract, 6.4 pp per the v2 full text.

<a id="fix7"></a>
### FIX7 · Evaluating agent-based program repair at Google

`re-checked` · P. Rondon, R. Wei, J. Cambronero, J. Cito et al., arXiv 2501.07531 (ICSE-SEIP 2025) ·
https://arxiv.org/abs/2501.07531

- **Numbers (Gemini 1.5 Pro, 20 samples):**
  - Test-passing patches for 73 % of 100 machine-reported bugs and 25.6 % of 78 human-reported bugs.
  - Semantically equivalent to the ground truth for only 43 % and 17.9 %.
- **For us:**
  - On an industrial codebase, passing tests is not the same as correct.
  - Combine tests with human adjudication (EVAL_PROTOCOL §3.4).

<a id="fix8"></a>
### FIX8 · Evaluating and improving fault localization

`re-checked` · S. Pearson et al., ICSE 2017, DOI 10.1109/ICSE.2017.62 · https://doi.org/10.1109/ICSE.2017.62

- **Reported:**
  - 76 % of real-world bug fixes span multiple statements, and 30 % only add new code (both cited from
    Defects4J).
  - "Other statements might be equally valid choices for a bug fix", so for omission bugs the authors used
    hand-built sets of candidate locations.

<a id="fix9"></a>
### FIX9 · DBGBench: where is the bug and how is it fixed?

`re-checked` · M. Böhme, E. Soremekun, S. Chattopadhyay, E. Ugherughe, A. Zeller, ESEC/FSE 2017, DOI 10.1145/3106237.3106255 ·
https://doi.org/10.1145/3106237.3106255

- **Numbers:**
  - 12 participants (11 professionals) debugged 27 bugs in find and grep.
  - A typical diagnosis referenced about 10 statements in 3–4 code regions.
  - 282 of 290 patches (97 %) passed the provided test, but only 182 (63 %) were correct.

<a id="fix10"></a>
### FIX10 · FaultLoc: evaluating coding agents for fault localization

`re-checked`, workshop paper · J. Tu et al., ICML 2026 workshop (Agents in the Wild) ·
https://icml.cc/virtual/2026/67968

- **What:** 900 real-world vulnerabilities. The paper's premise, not a measured result: test-based benchmarks
  let agents "succeed by mitigating symptoms at the crash site".
- **Numbers:** frontier agents reach "only 40 % overlap with ground-truth buggy lines" (from developer
  patches).
- **For us:** a reason to show upstream causes next to symptoms (IMPROVEMENT_PLAN W12, an experiment).

<a id="fix11"></a>
### FIX11 · The impact of tangled code changes

`re-checked` · K. Herzig & A. Zeller, MSR 2013, DOI 10.1109/MSR.2013.6624018 ·
https://doi.org/10.1109/MSR.2013.6624018

- **Reported:** up to 15 % of bug-fix commits contain unrelated changes.
- **For us:** clean the PR-derived gold sets before scoring.

---

## 4. Log lines to code, and logs for localization

<a id="log1"></a>
### LOG1 · Detecting large-scale system problems by mining console logs

`re-checked` · W. Xu, L. Huang, A. Fox, D. Patterson et al., SOSP 2009, DOI 10.1145/1629575.1629587 · https://doi.org/10.1145/1629575.1629587

- **What:**
  - Extracts message templates from source.
  - Expands `toString()` through the type hierarchy.
  - Finds logging wrappers by their format-string parameter.
- **Numbers:** over 99.8 % parsing accuracy.

<a id="log2"></a>
### LOG2 · SherLog: error diagnosis by connecting clues from run-time logs

`re-checked` · D. Yuan et al., ASPLOS 2010, DOI 10.1145/1736020.1736038 · https://doi.org/10.1145/1736020.1736038

- **What:**
  - Generates regex parsers from format strings; developers name the logging functions and which parameter is
    the format string.
  - When a message matches several log points, path inference (with a SAT solver) keeps only those consistent
    with the log sequence.
- **Numbers:** 8 real-world failures from 7 applications.

<a id="log3"></a>
### LOG3 · lprof: a non-intrusive request-flow profiler for distributed systems

`re-checked` · X. Zhao et al., OSDI 2014 ·
https://www.usenix.org/conference/osdi14/technical-sessions/presentation/zhao

- **What:**
  - Follows `toString()` and StringBuilder in bytecode, emitting one regex per possible override.
  - Ties go to the template with the longest constant text.
  - Request-ID dataflow groups lines by request.
- **Numbers:** 88 % of messages attributed to the right request.

<a id="log4"></a>
### LOG4 · Stitch: performance profiling based on the flow reconstruction principle

`re-checked` · X. Zhao et al., OSDI 2016 ·
https://www.usenix.org/conference/osdi16/technical-sessions/presentation/zhao

- **What:** reconstructs an object hierarchy from identifiers that co-occur across components' logs, without
  source code.
- **Numbers:** 96 % of inferred objects accurate; debugging took 5.9 vs 27.3 minutes (4.6×) in a
  14-participant user study.

<a id="log5"></a>
### LOG5 · Pensieve: non-intrusive failure reproduction

`re-checked` · Y. Zhang et al., SOSP 2017, DOI 10.1145/3132747.3132768 · https://doi.org/10.1145/3132747.3132768

- **What:** chains events backwards from an error log, preferring candidate statements whose logged values
  overlap other lines.
- **Numbers:** reproduced 13 of 18 randomly sampled real failures (72 %).

<a id="log6"></a>
### LOG6 · LogCoCo: estimating code coverage measures via execution logs

`re-checked` · B. Chen et al., ASE 2018, DOI 10.1145/3238147.3238214 · https://doi.org/10.1145/3238147.3238214

- **What:** matches each thread's log sequence against regexes of code paths and labels code Must / May /
  Must-not executed.
- **Numbers:** over 96 % accurate in 7 of 9 experiments.

<a id="log7"></a>
### LOG7 · Tracing back log data to its log statement: from research to practice

`re-checked` · D. Schipper, M. Aniche, A. van Deursen, MSR 2019, DOI 10.1109/MSR.2019.00081 ·
https://doi.org/10.1109/MSR.2019.00081

- **What:** in production at a payments company. Filters by logger class, then searches a TF-IDF index, then
  checks with a regex.
- **Numbers:**
  - 97.6 % correct (239 of 245 log statements), 4 ms median per line.
  - The 6 wrong links came from three causes: JSON-only messages, custom-made logging methods, and messages
    constructed outside the log statement.
- **For us:** the same causes as our wrapper and concatenation case (IMPROVEMENT_PLAN W10).

<a id="log8"></a>
### LOG8 · Pathidea: IR-based bug localization with execution paths rebuilt from logs

`re-checked` · A. R. Chen, T.-H. Chen, S. Wang, IEEE TSE 48(8), 2022, DOI 10.1109/TSE.2021.3071473 ·
https://doi.org/10.1109/TSE.2021.3071473

- **What:**
  - Breadth-first search over the call graph between consecutive log lines in a bug report.
  - Works at file level on purpose, because reported logs may come from older versions.
- **Numbers:** 1,273 bug reports; Top@5 recall up to 51.9 %; MAP +8–21 %.

<a id="log9"></a>
### LOG9 · Examining the stability of logging statements

`re-checked` · S. Kabinna et al., EMSE 2018, DOI 10.1007/s10664-017-9518-0 ·
https://doi.org/10.1007/s10664-017-9518-0

- **Numbers:** 20–45 % of logging statements change; the median time to the first change is 1–17 days.
- **For us:** build drift is normal. Plan for it (IMPROVEMENT_PLAN W9).

<a id="log10"></a>
### LOG10 · COCA: generative root-cause analysis with code knowledge

`re-checked` · Y. Li et al., ICSE 2025, arXiv 2503.23051 · https://arxiv.org/abs/2503.23051

- **What:**
  - Traces log calls back to templates, one per branch, and matches them against the code version the issue
    was reported on.
  - Connects the matched points across RPC calls; an LLM then localizes the root cause.
- **Numbers:** +28.3 % (exact match vs the leading method) on 106 issues from 5 distributed systems.
- **For us:** give the agent the rebuilt path plus code slices, not raw logs, and match against the build that
  wrote the log.

<a id="log11"></a>
### LOG11 · LLM-SrcLog

`re-checked` · J. Sun et al., arXiv 2512.04474 (preprint) · https://arxiv.org/abs/2512.04474

- **What:**
  - Path-sensitive static analysis follows log arguments into helper methods' return values.
  - Produces one template per path.
- **Numbers:** F1 +2–17 % and +8–35 % over two LLM-based parsers; about 1000× faster than parsing each line
  with an LLM.

<a id="log12"></a>
### LOG12 · OpenRCA: can LLMs locate the root cause of software failures?

`re-checked` · J. Xu et al., ICLR 2025 · https://openreview.net/forum?id=M4qNIzQYpd

- **What:** 335 failures with 68 GB of logs, metrics and traces.
- **Numbers:** the best agent setup solved 11.34 %.
- **For us:** agents working from raw telemetry are still weak.

<a id="log13"></a>
### LOG13 · LLM-based automated diagnosis of integration test failures at Google

`re-checked` · C. Ziftci et al., arXiv 2604.12108 (preprint) · https://arxiv.org/abs/2604.12108

- **Numbers:** 90.14 % correct (64 of 71 failures); used on 52,635 distinct failing tests.

<a id="log14"></a>
### LOG14 · Kieker: application performance monitoring and dynamic analysis

`re-checked` (framework only) · A. van Hoorn, J. Waller, W. Hasselbring, ICPE 2012 (2-page demo),
DOI 10.1145/2188286.2188326 · https://doi.org/10.1145/2188286.2188326

- **What:** a monitoring and control-flow tracing framework. The per-call record fields that are enough to
  rebuild the call tree (trace ID, execution order index `eoi`, execution stack size `ess`) are described in
  Kieker's user documentation, not in this paper (`from source`).
- **For us:** this is the model for pairing interceptor Begin/End lines (IMPROVEMENT_PLAN W7).

<a id="log15"></a>
### LOG15 · Conformance checking using cost-based fitness analysis

`re-checked` · A. Adriansyah, B. F. van Dongen, W. M. P. van der Aalst, EDOC 2011, DOI 10.1109/EDOC.2011.12 ·
https://doi.org/10.1109/EDOC.2011.12

- **What:** replays an event log on a process model, with cost functions for skipped and inserted activities
  (A*-based search).
- **For us:** the closest published basis for aligning a thread's log lines to the call graph while tolerating
  drift. We found no published HMM or Viterbi method that maps logs onto a control-flow graph, so treat this as
  an experiment, not a first step.

---

## 5. Tool and serving guidance; other code-intelligence tools

<a id="tool1"></a>
### TOOL1 · Anthropic: writing effective tools for agents

`re-checked` · 11 Sep 2025 · https://www.anthropic.com/engineering/writing-tools-for-agents

- **Recommendations:**
  - Consolidate: "Tools can consolidate functionality, handling potentially multiple discrete operations (or
    API calls) under the hood."
  - Offer a `response_format` enum, "concise" or "detailed" (72 vs 206 tokens in their example).
  - Use pagination, range selection, filtering and truncation "with sensible default parameter values".
  - Prefer natural-language names over cryptic identifiers.
  - "For Claude Code, we restrict tool responses to 25,000 tokens by default."
  - "More tools don't always lead to better outcomes."

<a id="tool2"></a>
### TOOL2 · Anthropic: effective context engineering for AI agents

`re-checked` · 29 Sep 2025 · https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

- **Quotes:**
  - Claude Code uses a hybrid model: CLAUDE.md files go into context up front, while glob and grep retrieve
    files just in time, "effectively bypassing the issues of stale indexing and complex syntax trees".
  - "One of the most common failure modes we see is bloated tool sets that cover too much functionality or lead
    to ambiguous decision points about which tool to use."

<a id="tool3"></a>
### TOOL3 · Anthropic: advanced tool use (Tool Search Tool)

`re-checked` · 24 Nov 2025 · https://www.anthropic.com/engineering/advanced-tool-use

- **Numbers:**
  - With tool search on large MCP tool sets, accuracy rose for Opus 4 (49 % → 74 %) and Opus 4.5 (79.5 % →
    88.1 %).
  - It cut token usage by 85 %: about 77k tokens of context before any work (about 72k of them tool definitions
    for 50+ MCP tools), down to about 8.7k.
- **For us:** many tool definitions cost context and accuracy. Keep the default tool set small.

<a id="tool4"></a>
### TOOL4 · Claude Code: MCP output limits

`re-checked` · https://code.claude.com/docs/en/mcp

- **Limits:**
  - Claude Code warns when any MCP tool output exceeds 10,000 tokens (a fixed threshold).
  - The default maximum is 25,000 tokens, set with `MAX_MCP_OUTPUT_TOKENS`.
  - Successful text results longer than 50,000 characters are saved to a file.

<a id="tool5"></a>
### TOOL5 · Claude Code: code intelligence (LSP) plugins

`re-checked` · https://code.claude.com/docs/en/plugins/code-intelligence ·
https://code.claude.com/docs/en/tools-reference

- **Operations:** jump to definition, find references, type information at a position, symbols in a file,
  workspace symbol search, find implementations, and "trace call hierarchies". The live tool schema has
  incoming and outgoing calls. Diagnostics are reported after edits.
- **Plugins:** C# `csharp-lsp` (binary `csharp-ls`) and C/C++ `clangd-lsp` (binary `clangd`).
  - Install with `/plugin install <name>@claude-plugins-official`, e.g.
    `/plugin install csharp-lsp@claude-plugins-official`.
  - The language server must be on the `PATH` of the shell that starts `claude`.
- **Limitation:** terminal sessions only. In cloud sessions the language servers don't start.
- **For us:** a free third arm for the eval (IMPROVEMENT_PLAN W11).

<a id="tool6"></a>
### TOOL6 · csharp-ls (the server behind `csharp-lsp`)

`re-checked` · https://github.com/razzmatazz/csharp-language-server (release 0.28.0, 13 Sep 2026)

- **What:** a Roslyn-based LSP server; install with `dotnet tool install --global csharp-ls`.
- **Requirements:**
  - Needs the .NET 10 SDK or later (since 0.21.0; the plugin README's "6.0 or later" is out of date).
  - Supports projects targeting ".NET Framework 4.8, and potentially earlier ones".
  - Legacy (non-SDK-style) `.csproj` support is not documented.
- **Call hierarchy:** supported, but `outgoingCalls` was a stub until 0.27.0 (Aug 2026).
- **For us:** it may hit the same legacy-project loading costs our extractor had to solve.

<a id="tool7"></a>
### TOOL7 · Claude Code: monorepos and large codebases

`re-checked` · https://code.claude.com/docs/en/large-codebases

- **What:** recommends a root `CLAUDE.md` plus per-directory `CLAUDE.md` files that load on demand, read-deny
  rules, and code-intelligence plugins.
- **For us:** put a short graph-usage guide in the target repository's `CLAUDE.md`, not only in an eval prompt.

<a id="tool8"></a>
### TOOL8 · Serena (LSP-based MCP server)

`re-checked` · https://oraios.github.io/serena/01-about/020_programming-languages.html ·
https://github.com/oraios/serena

- **Languages:**
  - C#: the Roslyn language server by default (needs .NET 10+, and `pwsh` on Windows), or OmniSharp as an
    option.
  - C/C++: clangd or ccls, with a `compile_commands.json` at the repository root.
- **Benchmarks:** none published. The "70–90 % token savings" figures found online come from third parties.

<a id="tool9"></a>
### TOOL9 · Aider repository map

`re-checked` · https://aider.chat/docs/repomap.html

- **What:**
  - A map of definitions and references built with tree-sitter.
  - Ranked by a graph-ranking algorithm over file dependencies.
  - Default budget `--map-tokens` 1k.
- **Evidence:** no published head-to-head with grep.

<a id="tool10"></a>
### TOOL10 · Sourcegraph SCIP indexers

`re-checked` · https://github.com/sourcegraph/scip-dotnet · https://github.com/sourcegraph/scip-clang

- **scip-dotnet:** C# and VB; .NET 8 or a Docker image.
- **scip-clang:** needs a compilation database; binaries only for x86_64 Linux and arm64 macOS, none for
  Windows.

<a id="tool11"></a>
### TOOL11 · Kythe, Glean, stack-graphs

`re-checked`

- **Kythe:** indexers for C++, Go and Java; no C# (https://github.com/kythe/kythe).
- **Meta Glean:** C++, Python, PHP, JavaScript, Rust and more, with no C# mentioned; lists RAG for AI coding
  assistants as a use (https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/).
- **GitHub stack-graphs:** archived 9 Sep 2025 (https://github.com/github/stack-graphs).

<a id="tool12"></a>
### TOOL12 · Microsoft: C++ tools for GitHub Copilot

`re-checked`

- **Visual Studio 2026 Insiders** (16 Dec 2025,
  https://devblogs.microsoft.com/cppblog/c-code-editing-tools-for-github-copilot-now-in-public-preview/):
  - tools `get_symbol_references`, `get_symbol_info`, `get_symbol_class_hierarchy`,
    `get_symbol_call_hierarchy`.
- **Copilot CLI** (22 Apr 2026,
  https://devblogs.microsoft.com/cppblog/c-code-intelligence-for-github-copilot-cli-preview/):
  - definitions, references, call hierarchies and type information;
  - needs `compile_commands.json`; MSBuild project support is "planned".
- **Not stated in either post:** numbers, and C++/CLI support.

<a id="tool13"></a>
### TOOL13 · clang-cl cannot compile C++/CLI

`re-checked` · https://llvm.googlesource.com/llvm/+/refs/heads/master/tools/msbuild/LLVM.Cpp.Common.targets

- **What:** "clang-cl does not support compiling managed code (/clr)". In current llvm-project, `/clr` is listed
  under "Unsupported" options.
- **For us:** clangd-based tools (Serena, the clangd plugin, scip-clang, Copilot's C++ tools) cannot index
  C++/CLI. codegraph reads C++/CLI from the built assemblies instead.

<a id="tool14"></a>
### TOOL14 · JetBrains Rider: C++/CLI support

`re-checked` · https://www.jetbrains.com/help/rider/CPP_CLI_Support.html

- **What:** "Find Usages and Go To Declaration work between C++/CLI and C#." Limits: `#using` directives are not
  supported, and rename does not update calls outside C++/CLI code.
- **For us:** Rider is the only other tool we found with C++/CLI ↔ C# navigation, and it is an IDE, not an
  agent tool. No agent evaluation of it exists.

---

## 6. Evaluation methodology

<a id="eval1"></a>
### EVAL1 · Adding error bars to evals

`re-checked` · E. Miller (Anthropic), "Adding Error Bars to Evals: A Statistical Approach to Language Model
Evaluations", arXiv 2411.00640 (2024) · https://arxiv.org/abs/2411.00640

- **Recommendations:**
  - Analyse paired per-task differences.
  - Use clustered standard errors when tasks are related; in the paper's examples they came out up to about 3×
    larger (DROP 3.05×).
  - Repeats help only until within-task variance divided by K is small next to between-task variance.
  - Sample size: n = (z_{α/2} + z_β)² (ω² + σ_A²/K_A + σ_B²/K_B) / δ², with δ the minimum detectable effect
    and K the answers per question.

<a id="eval2"></a>
### EVAL2 · Don't use the CLT in LLM evals with fewer than a few hundred datapoints

`re-checked` · S. Bowyer, L. Aitchison, D. Ivanova, ICML 2025 (spotlight position paper), arXiv 2503.01747 · https://arxiv.org/abs/2503.01747
· code https://github.com/sambowyer/bayes_evals

- **Recommendations:**
  - At small N, CLT and bootstrap intervals are too narrow.
  - Use Wilson or Bayesian intervals, and a paired Bayesian method for A/B comparisons.

<a id="eval3"></a>
### EVAL3 · Measuring all the noises of LLM evals

`re-checked` · S. I. Wang (FAIR at Meta), arXiv 2512.21326 · https://arxiv.org/abs/2512.21326

- **Finding:** in paired comparisons, run-to-run (prediction) noise usually exceeds the noise from sampling
  questions (about 2× on MATH500). With a fixed task pool, repeats add power.

<a id="eval4"></a>
### EVAL4 · On randomness in agentic evals

`re-checked` · Bjarnason, Silva, Monperrus (KTH), arXiv 2602.07150 (2026) · https://arxiv.org/abs/2602.07150

- **Numbers:**
  - Based on 60k SWE-bench Verified trajectories.
  - Single-run pass@1 swings by 2.2–6.0 pp; the standard deviation stays above 1.5 pp even at temperature 0.
- **Recommendations:** multiple runs, power analysis, and reporting both pass@k and pass^k.

<a id="eval5"></a>
### EVAL5 · Quantifying infrastructure noise in agentic coding evals

`re-checked` · G. Segato (Anthropic), 5 Feb 2026 · https://www.anthropic.com/engineering/infrastructure-noise

- **Finding:** container resources alone moved Terminal-Bench 2.0 scores by 6 pp (p < 0.01). "Leaderboard
  differences below 3 percentage points deserve skepticism until the eval configuration is documented and
  matched."

<a id="eval6"></a>
### EVAL6 · Demystifying evals for AI agents

`re-checked` · Grace, Hadfield, Olivares, De Jonghe (Anthropic), 9 Jan 2026 · https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

- **Recommendations:**
  - Start with 20–50 tasks drawn from real failures.
  - "it's often better to grade what the agent produced, not the path it took".
  - Run multiple trials, and track turns, tool calls, tokens and latency.
  - Calibrate LLM judges against humans.
  - Use a clean environment for each trial, and read the transcripts.

<a id="eval7"></a>
### EVAL7 · SWE-bench

`from source` · C. E. Jimenez et al., ICLR 2024, arXiv 2310.06770 · https://arxiv.org/abs/2310.06770

- **Scoring:** a task is resolved when the FAIL_TO_PASS tests pass and the PASS_TO_PASS tests still pass. The
  fix location is not scored.

<a id="eval8"></a>
### EVAL8 · OpenAI on SWE-bench Verified

`re-checked` via a verbatim mirror (2024) · `from source`, secondary coverage only (2026); openai.com blocks
fetching

- **2024**, "Introducing SWE-bench Verified" (13 Aug 2024, https://openai.com/index/introducing-swe-bench-verified/):
  - annotators flagged 38.3 % of samples for underspecified problem statements and 61.1 % "for unit tests that
    may unfairly mark valid solutions as incorrect";
  - 68.3 % of the annotated samples were filtered out, leaving 500.
- **2026**, "Why SWE-bench Verified no longer measures frontier coding capabilities" (23 Feb 2026,
  https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/). Per secondary sources:
  - OpenAI audited 138 often-failed problems and found that at least 59.4 % had flawed tests that reject
    correct submissions;
  - it also cited contamination, and now recommends SWE-bench Pro.
- **For us:** even curated test-based scoring rejects valid fixes, so adjudicate.

<a id="eval9"></a>
### EVAL9 · AI agents that matter

`re-checked` · S. Kapoor et al., arXiv 2407.01502 (2024; TMLR 2025) · https://arxiv.org/abs/2407.01502

- **Recommendation:** report cost and accuracy together (Pareto plots), not accuracy alone.

<a id="eval10"></a>
### EVAL10 · Cost-of-Pass

`re-checked` · M. H. Erol et al., arXiv 2504.13359 (2025) · https://arxiv.org/abs/2504.13359

- **Definition:** the expected cost of producing one correct solution.

<a id="eval11"></a>
### EVAL11 · τ-bench (pass^k)

`re-checked` · S. Yao et al., ICLR 2025, arXiv 2406.12045 · https://arxiv.org/abs/2406.12045

- **Definition:** pass^k means all k trials succeed, a reliability measure next to pass@k.

<a id="eval12"></a>
### EVAL12 · Approximate statistical tests for comparing supervised classification learning algorithms

`from source` · T. G. Dietterich, Neural Computation 1998, DOI 10.1162/089976698300017197 ·
https://doi.org/10.1162/089976698300017197

- **What:** McNemar's test for paired binary outcomes.

<a id="eval13"></a>
### EVAL13 · A/B testing without peeking

`from source` · E. Miller, "How Not To Run an A/B Test" (2010), https://www.evanmiller.org/how-not-to-run-an-ab-test.html
· "Simple Sequential A/B Testing" (2015), https://www.evanmiller.org/sequential-ab-testing.html

- **Finding:** repeatedly checking significance inflates false positives. Fix the sample size in advance, or use
  a pre-planned sequential rule.
