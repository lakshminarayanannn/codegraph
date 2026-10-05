# Accuracy (SPEC 11.2)

**Question:** do the graph's callers of a method match what the compiler knows?
**Reference:** Roslyn `SymbolFinder.FindReferencesAsync`, the engine behind Visual Studio's "Find All References".
**Gate:** recall ≥ 98% for C#.

## Method

`CodeGraph.Roslyn --config <cfg> --accuracy 50 [--accuracy-seed 42]` (after a normal extraction):

1. Collects every ordinary method and constructor of the selected projects, sorts them by ID, and samples 50 with a
   fixed seed (repeatable).
2. For each method, asks Roslyn for references to the method and to the declarations it implements or overrides
   (computed from Roslyn symbols, so a missing graph edge cannot hide itself). Doc-comment `cref`s and `nameof`
   are not uses and are skipped. Each reference is mapped to its containing member the way the extractor
   attributes code (lambdas and local functions → the member; field initializers → the constructors).
3. Compares with the graph's depth-1 callers: CALLS and REFERENCES_METHOD into the method, plus callers of the
   interface/base declarations it implements or overrides (what DISPATCHES_TO adds), read from the extractor output.
4. Writes `out/csharp/accuracy.json`: precision, recall, and per method the Roslyn count, graph count, `missing`
   (Roslyn found, graph lacks) and `extra` (graph has, Roslyn did not find).

Investigate every `missing` entry: the usual causes are compile errors in the calling project (unresolved symbols),
calls the extractor does not model (e.g. `dynamic`), or a project that was not indexed.

## Results

| Codebase | Date | Methods | Roslyn references | Precision | Recall | Notes |
|---|---|---|---|---|---|---|
| MiniPlant fixture (macOS) | 2026-10-04 | 49 (all) | 57 | 100% | 100% | `out/fixture/csharp/accuracy.json` |
| Real codebase (Windows) | *pending* | 50 | | | | run during Phase 2 (docs/RUNBOOK_WINDOWS.md) |

## Misses on the real codebase

*To be filled during Phase 2: one row per missing caller with its cause and whether it was fixed.*

| Method | Missing caller | Cause | Fix |
|---|---|---|---|
