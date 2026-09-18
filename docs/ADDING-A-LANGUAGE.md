# Adding a language

A language enters at a **level**, not as a binary. Breadth and depth are
independent axes, so you can add inventory support for Kotlin without claiming
you can trace its data flow — and an agent querying it will be told which it is
reading before it trusts a negative answer.

| Level | Required specs | Delivers | Roughly |
| --- | --- | --- | --- |
| **L0** — inventory | `nodes.scm` | symbols, `search`, `get_symbol`, module/class/function map | ~80 lines |
| **L1** — graph | `+ refs.scm` | `CALLS` / `IMPORTS`, `callers_of`, `callees_of`, `impact_of` | +~70 lines |
| **L2** — flow | `+ dataflow.scm` | `READS` / `WRITES` / `PARAM_BINDS` / `RETURNS`, `dataflow_of`, contracts | +~80 lines |

**The level is derived, never declared.** It comes from which spec files loaded
and carry content — there is no `level = "L2"` field to set, and adding one
would defeat the point. A level you can write down is a level that drifts from
what the queries actually do.

## The one rule

**A grammar without an extraction spec is worse than no support at all.**

`pyproject.toml` records why, and it is worth restating because this is the
process that could repeat it:

> Previously `tree-sitter` sat behind an extra and *no grammar package was
> declared at all*, so `_get_tree_sitter_parser` returned None for all 12
> advertised languages and every non-Python file silently fell through to a
> regex line scanner. On Apache Dubbo that produced 4,297 "classes" matched
> from `"class " in line` and five "methods" that were string literals from a
> switch statement.

So: **no language enters `matrix.json` without a corpus file demonstrating what
it extracts.** A grammar pinned without a spec fails the conformance run.

## What you deliver

```
pyproject.toml                                  exact grammar pin (== not >=)
src/lineagelens/spec/<lang>/lang.toml           lang, dialects, doc_prefixes
src/lineagelens/spec/<lang>/nodes.scm           mandatory — L0
src/lineagelens/spec/<lang>/refs.scm            optional — L1
src/lineagelens/spec/<lang>/dataflow.scm        optional — L2
src/lineagelens/extract/langs.py                GRAMMARS + EXTENSIONS entries
src/lineagelens/conformance/corpus/<lang>/…     one file per construct set
```

### 1. Pin the grammar exactly

```toml
"tree-sitter-kotlin==1.1.0",
```

`==`, never `>=`. A grammar upgrade changes parse output, which must invalidate
the index rather than silently alter results — `graph_meta.grammar_digest`
depends on the pinned set, and a floor would make `verify --determinism`
unenforceable.

### 2. `lang.toml`

```toml
lang = "kotlin"
dialects = ["kotlin"]
doc_prefixes = ["/**", "*", "//"]
implicit_receivers = []
constructor_names = ["<init>"]
```

`implicit_receivers` names parameters that are declared but are not part of an
overload signature — Python's `self`, a Go method receiver. They are dropped
before signature hashing. `constructor_names` exists because the spelling is
arbitrary (`__init__`, `constructor`, `new`) and in most languages a
constructor is grammatically an ordinary method.

### 3. `nodes.scm` — this is L0

Capture, where the language has the construct: module or namespace, class /
struct / interface / trait / enum, function, method, constructor, field,
constant, and the doc comment attached to each.

Constructs the language lacks are `n/a` in the matrix — **not `false`**. `false`
means "we should find these and did not"; `n/a` means "there is nothing to
find". Conflating them is how a capability matrix stops being read.

### 4. A corpus file

`src/lineagelens/conformance/corpus/<lang>/` — real code exercising every
construct your spec claims. This is the evidence for the level, and the
conformance run measures it rather than trusting the spec.

### 5. Run the conformance suite

```
python -m lineagelens.conformance.runner
```

It regenerates `matrix.json` and **fails** if a claimed level is not
corroborated by what your corpus produced:

- L1 requires ≥1 `CALLS` or `IMPORTS` edge;
- L2 additionally requires ≥1 `READS` / `WRITES` / `PARAM_BINDS` / `RETURNS`.

Cumulatively — an L2 claim must satisfy L1 too. A `dataflow.scm` that works
over a `refs.scm` that does not is two levels of spec with one broken, not one
level up.

`matrix.json` is committed, so regenerating must produce no diff when nothing
changed. CI asserts this.

## Promoting a level later

Add the next spec file and re-run the suite. Nothing else changes — no manifest
edit, no code change, no claim to update, because every surface derives the
level. `graph_meta.ontology_digest` moves, which invalidates existing indexes:
correct, since the graph now contains edge kinds it did not before.

## Where the level shows up

Four places, all generated:

| Surface | Field |
| --- | --- |
| `lineagelens ontology` | `lvl` column |
| `lineagelens coverage` | `levels:` section, naming which languages give unavailable rather than absent answers |
| `lineagelens index` | `levels` line |
| every query response | `coverage.levels` in the envelope |

The last is the one that matters most. `Envelope` exists so a negative answer
can be told apart from an uncovered one, and a level is exactly that
distinction at language granularity. An agent calling `callers_of` on an L0
symbol needs to know a call answer is *unavailable*, not *empty*.

An envelope touching any language below L2 reports `complete: false`.

## CI floors

```
lineagelens index --require-level=L2                  every language must reach L2
lineagelens index --require-level=kotlin:L0,java:L2   per-language floors
```

Files below the floor are skipped with
`files.skip_reason = 'below_required_level'` and the command exits non-zero, so
refusing to answer cannot look like a successful index of nothing. A floor is
always a minimum to meet, never a maximum to allow.
