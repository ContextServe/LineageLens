# LineageLens

Evidence-labelled Python code lineage for humans and coding agents.

LineageLens builds a code graph from Python source — entry points, call paths,
method contracts (inputs/outputs), risk signals, and (opt-in) generated
documentation. Every signal carries an evidence label so you can always tell a
**fact** from a **heuristic** from a **probabilistic model output**.

## Evidence & Trust Model — deterministic vs probabilistic

LineageLens separates **three trust tiers**. This is a promise, not a footnote:
probabilistic output is never presented as fact, and a heuristic is never passed
off as an observed runtime value.

### 1. Deterministic — extracted facts (reproducible to the line)

Pure static-AST reads. The same source always yields the same values, and each
value traces to a file + line.

| Signal | Evidence label |
| ------ | -------------- |
| Symbol identity: kind, name, file, line, module | `static_ast` |
| Parameter names, order, defaults | `static_ast` |
| Author-written parameter and return type annotations | `annotation` |
| Decorators, async-ness | `static_ast` |
| Raw call sites: what was called, and at which line | `static_ast` |
| Relation kind (`CALLS`, `AWAIT_CALLS`, `CREATES_TASK`) | `static_ast` |
| Entry-point classification (API route, CLI command, test, framework callback) | rule-based from `lineagelens.yaml` |

### 2. Deterministic heuristics — reproducible inference (labelled as inference)

Static guesses. **Deterministic** in that the same code always produces the same
output; **approximate** in that they are not observed at runtime and are not
guaranteed correct. Whenever nothing can be inferred, the value is explicitly
`unknown` — nothing is silently guessed.

| Signal | Evidence label |
| ------ | -------------- |
| Inferred type of a return expression (`list`, `dict`, `call result`, …) | `return_expression` |
| Inferred type of an argument at a call site | `inferred_type` |
| Resolved call targets (via imports / scope walk) | `resolution: resolved` vs `external_or_dynamic` |
| Static risk signals from configurable keyword/text rules | rule match, e.g. `execute at line 12` |

> **Performance & security (static tier).** The built-in performance and
> security signals are *deterministic heuristics* — configurable keyword/pattern
> rules that fire identically on every run. They are **hints to review**, not
> measurements and not verdicts. Severity (`review`, `high`) is assigned by
> rules in `lineagelens.yaml`.

### 3. Probabilistic — LLM-generated (labelled `llm`)

Anything produced by a generative model. Output depends on the model, prompt,
and version, may differ between runs, and models can hallucinate. Probabilistic
output is stored separately and clearly tagged — it is never merged into the
deterministic graph as fact.

| Signal | Trust |
| ------ | ----- |
| Method documentation (purpose, semantics of inputs/outputs) | Probabilistic — LLM |
| LLM performance characteristics & bottleneck analysis | Probabilistic — LLM estimates, not measurements |
| LLM security & handling analysis (CWE-style review, recommendations) | Probabilistic — LLM judgement, not a security guarantee |

## Where each label lives

- `Symbol.inputs[].type`, `Symbol.outputs[].type` → value of author annotation, or `unknown`
- `Symbol.outputs[].evidence` → `annotation` | `return_expression`
- `Relation.evidence` → `static_ast`
- `Relation.resolution` → `resolved` | `external_or_dynamic`
- `Relation.arguments[].inferred_type` → `unknown` or an inferred type name
- `Symbol.risks[].evidence` → `"<raw call> at line <n>"` (rule match)
- LLM-generated documentation → `llm`

## Design rules

- Facts, heuristics, and probabilities are labelled at the point of production
  (`evidence`, `resolution`, `inferred_type`, `unknown`, `llm`).
- Uncertainty is explicit: what cannot be inferred is `unknown`, never guessed.
- Probabilistic content is opt-in (requires an LLM call) and never carries
  API credentials into artifacts.
