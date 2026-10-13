# Research: can a code graph beat grep for coding agents? (October 2026)

This folder is the hand-off for the next round of work: what the world has published about giving coding agents
something better than grep, what that means for codegraph, what to build next, and how to prove it.

## Bottom line (plain language)

1. **Nobody has shown that better search makes an agent's fixes much more correct.** Cursor, Sourcegraph,
   JetBrains and the published papers all find what our internal pilot found: better code tools make the agent
   cheaper and faster (typically 25–40 % fewer tool calls, tokens or cost), while the final result moves little
   (0–5 points) or not at all ([H1](EVIDENCE.md#h1), [H2](EVIDENCE.md#h2), [H3](EVIDENCE.md#h3), [H5](EVIDENCE.md#h5), [LOC4](EVIDENCE.md#loc4)).
2. **Where structured tools clearly beat grep:**
   - precision on "who calls / uses X" (grep returns many wrong hits) ([H4](EVIDENCE.md#h4));
   - finding the right files on large codebases ([H1](EVIDENCE.md#h1), [H2](EVIDENCE.md#h2));
   - questions grep cannot answer: C# → C++/CLI → C++ paths, interface and factory dispatch, log lines whose text
     is not in the source.

   We found no other agent tool that links C# ↔ C++/CLI ↔ C++ ([TOOL13](EVIDENCE.md#tool13), [TOOL14](EVIDENCE.md#tool14)).
3. **What does improve fix quality is running code:** reproduction tests, test-based patch selection and
   coverage-based fault localization. More context does not ([LOC12](EVIDENCE.md#loc12), [LOC13](EVIDENCE.md#loc13), [FIX1](EVIDENCE.md#fix1), [FIX2](EVIDENCE.md#fix2), [FIX3](EVIDENCE.md#fix3)).
4. **"Edited the same method as the developer" is a weak score.** Even *successful* agent patches on SWE-bench
   edit the developer's function only about 27 % of the time, the same as our 0.27 ([FIX5](EVIDENCE.md#fix5)).
   Valid fixes often live elsewhere, and passing tests is not proof either ([LOC13](EVIDENCE.md#loc13), [FIX6](EVIDENCE.md#fix6), [FIX7](EVIDENCE.md#fix7), [FIX8](EVIDENCE.md#fix8)).
5. **Logs to code is well studied:** each of our hard log-matching cases has a published technique ([LOG1](EVIDENCE.md#log1)–[LOG15](EVIDENCE.md#log15)).
6. **So:**
   - keep the graph and use it **with** grep;
   - prove (a) less noise, (b) lower cost and time for the same result, and (c) answers grep cannot give;
   - show fix quality is **not worse**;
   - raise fix quality through test execution.

## Files

| File | For | Contents |
|---|---|---|
| [EVIDENCE.md](EVIDENCE.md) | humans checking sources | every source with link, what it measured, its numbers, and whether we re-checked them |
| [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) | the implementation agent | 13 prioritized work items: why (evidence IDs), what to build, where, acceptance tests |
| [EVAL_PROTOCOL.md](EVAL_PROTOCOL.md) | the implementation agent, reviewers | how to measure each claim: deterministic benchmarks, agent A/B design, scoring, statistics, budget |

**Implementation agent:** read this page, then IMPROVEMENT_PLAN.md (start with W1–W3), then EVAL_PROTOCOL.md.
Evidence IDs in brackets ([H2], [LOC1], ...) are headings in EVIDENCE.md.

## Status tags used in EVIDENCE.md

- `re-checked`: the numbers and quotes were checked against the source page while writing this document.
- `from source`: taken from the paper or page during the research pass but not independently re-checked. Verify
  before quoting externally.
- `vendor claim`: published by the vendor about its own product; method or grader not disclosed, not replicated.

Our own statistical estimates are labelled "our calculation".
