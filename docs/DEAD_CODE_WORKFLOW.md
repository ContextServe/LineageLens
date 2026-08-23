# Using LineageLens to keep dead code out

This is the workflow the tool exists for: analyse, read the verdicts, act on the
ones worth acting on, and stop new dead code arriving.

## Why "does anything call it?" is the wrong question

The obvious way to find dead code is to look for symbols with no incoming call.
It does not work, and it fails in a specific and dangerous direction.

Measured on a real 1,620-symbol project, 71% of the symbols that approach called
"confirmed dead" were live. They were reached by mechanisms a graph of call edges
does not describe:

| how it was actually reached | symbols |
|---|---|
| passed as a value — `Depends(get_db)`, `lifespan=shutdown`, registry dicts | 158 |
| lives in a test file | 147 |
| used in a type annotation — `def login(r: LoginRequest)` | 57 |
| implicit dunder — `__init__`, `__enter__`, `__exit__` | 37 |
| listed in `__all__` | 36 |
| used as a base class | 17 |

A Pydantic request model is *never called*. Neither is a FastAPI dependency, an
abstract base method, or a `__enter__`. An agent told those were "confirmed dead"
and asked to clean up would delete a working application.

So LineageLens does not ask whether anything calls a symbol. It walks outward
from entry points over every edge kind it models — calls, references,
annotations, inheritance, overrides, decorators, exports, imports, fixtures —
and reports what reached each symbol.

## The verdicts

| verdict | meaning | act on it? |
|---|---|---|
| `alive` | reached from an entry point through a syntactic fact | no |
| `dynamic_only` | reached, but every path crosses a name match — type inference, a polymorphic override, a fixture name | no, but read the mechanism if you are refactoring |
| `public_api` | unreferenced in-repo, but in a package `__all__` | no — consumers are outside this repo |
| `test_only` | reachable only from tests | yes — deleting it breaks the suite but nothing shipped uses it, which usually means the test is testing nothing that matters |
| `probably_dead` | unreachable, but an unresolved call shares its name | investigate; do not bulk-delete |
| `dead` | unreachable by any modelled mechanism, and no name collision | yes, after the check below |

`scope` is reported separately from the verdict, because dead code in a test file
is a different triage decision from dead code that ships.

## What a `dead` verdict does and does not mean

It means: **no static reference to this symbol exists anywhere in the project.**

It does not mean the symbol is unused at runtime. Static analysis cannot see:

- `getattr(module, name_from_config)`
- plugin discovery via entry points or directory scanning
- a handler named in an environment variable, YAML file, or database row
- `eval` / `exec`
- a symbol referenced only from a template or a non-Python file

This is why there is no `--fix` mode and never will be. The tool produces a
shortlist for a human or an agent to check; it does not delete code.

## Auditing a verdict

Every reachable symbol names the mechanism that saved it, so you can check the
reasoning rather than trusting the conclusion.

```bash
curl localhost:8717/api/v1/reachability/myapp.api.routes.create_item
```

```json
{
  "verdict": "alive",
  "scope": "source",
  "rescue": {
    "mechanism": "entry_point:api_route",
    "tier": "deterministic_fact",
    "detail": "invoked from outside the project as api_route",
    "via_symbol": null
  }
}
```

`tier` is the part to read. `deterministic_fact` means the reason was read
directly off literal syntax. `deterministic_heuristic` means it rests on a name
match — worth a second look before you rely on it.

The MCP equivalent is the `get_reachability` tool. **An agent should call it
before deleting anything.** A verdict you cannot audit is one you should not act
on.

## Rescue mechanisms

Fact-tier — read off literal syntax:

| mechanism | what it means |
|---|---|
| `entry_point:<kind>` | invoked from outside: a route, CLI command, test, task, console script, `__main__` guard |
| `static_call` | called by name from reachable code |
| `implicit_dunder` | the language invokes it on a reachable class |
| `base_class` | a reachable class inherits from it |
| `type_annotation` | named in a signature or variable annotation |
| `decorator` | applied with `@` to something reachable |
| `dunder_all_export` | listed in a package `__all__` |
| `passed_as_value` | handed to something as an argument or stored in a structure |
| `module_scope` | a module body, which runs on import |
| `abstract_declaration` | declared `@abstractmethod` |
| `pragma_keep` | marked `# lineagelens: keep` |

Heuristic-tier — a name match, not a proof:

| mechanism | why it is only a heuristic |
|---|---|
| `jedi_inference` | the call target came from type inference |
| `local_type_inference_*` | the receiver's type was inferred from a constructor or factory |
| `polymorphic_override` | it overrides a reachable base method by name; Python has no `override` keyword |
| `pytest_fixture_name` | a test parameter matches a fixture name |
| `string_reference` | a dotted string literal matches its name |
| `name_collision_unresolved_call` | an unresolved call site shares its name |

## Triaging a `dead` verdict

1. **Read the symbol.** Is it obviously superseded, a leftover, half a refactor?
   Delete it.
2. **Search for its name as a string.** `grep -r "'the_name'"`. If it is named in
   config, a template, or a registry file, it is reachable dynamically — go to 4.
3. **Check whether an entry-point rule is missing.** If it is a route, task or
   command for a framework not yet covered, add the decorator suffix to
   `analysis.entry_points` in `lineagelens.yaml`. That fixes the whole class of
   symbol, not just this one, and is always better than annotating individuals.
4. **Record that it is reached dynamically.** In preference order:
   - add a rule to `analysis.entry_point_rules` if a whole category is affected
   - add the decorator suffix to `analysis.entry_points`
   - enable `REFERENCES_STRING` in `analysis.relation_kinds` if your project
     wires things up with dotted strings (see the caveat below)
   - as a last resort, `# lineagelens: keep` on the definition

```python
def rebuild_search_index():  # lineagelens: keep
    """Invoked by name from the ops runbook, not from Python."""
```

The pragma is a comment in the source, which means it is reviewable, it travels
with the code, and it explains itself to the next reader. That is the point.

## Two worked examples

**`PhaseEngine.compute_single` → `dead`, and that is correct.** Its only other
mentions in the project are docstrings. Docstring and comment references are
deliberately not modelled: prose about a symbol is not a use of it, and treating
it as one would make the tool useless on any well-documented codebase. If prose
is the only thing referring to a symbol, the symbol is dead and the prose is
stale.

**`MassiveProvider.fetch_daily_prices` → `dynamic_only` via
`polymorphic_override`.** Nothing calls it by name. It is reached because
`GlobalIngestionEngine.__init__` takes `provider: BaseDataProvider` and stores it
on `self`, so `self.provider.fetch_daily_prices(...)` resolves to the abstract
base method — and this class overrides it. Two heuristics in a chain, so the
verdict is `dynamic_only` rather than `alive`. That is the honest answer: almost
certainly live, and you should confirm the subclass is actually instantiated
somewhere before touching it.

## Stopping new dead code arriving

Detection on its own does not change anything. Two commands close the loop.

**Keep the graph current.**

```bash
lineagelens hook install .
```

A post-commit hook, so it never slows a commit down. Without it the graph goes
stale and an agent makes decisions from a snapshot of an hour ago — which is the
drift problem, not a fix for it.

Add `.lineagelens/` to `.gitignore` but keep the baseline:

```gitignore
.lineagelens/*
!.lineagelens/dead-code-baseline.json
```

**Ratchet in CI.**

```bash
lineagelens check . --update-baseline   # once, to accept what exists today
git add .lineagelens/dead-code-baseline.json
```

From then on `lineagelens check .` exits 1 only on dead code that is *new*. This
matters for adoption: an absolute gate fails every existing codebase on day one,
gets switched off, and nothing improves. A ratchet works from the moment you
install it, and the number only moves one way.

Tighten later:

```bash
lineagelens check . --fail-on probably_dead   # include the uncertain ones
lineagelens check . --max-new 3               # allow a small budget
```

## Using this from an agent

The MCP tools are the intended path. A reasonable sequence before writing new
code:

1. `search_symbols` — does something like this already exist?
2. `get_reachability` on anything you are about to delete or replace.
3. `list_dead_code` — do not regenerate anything already flagged.

The reason `get_reachability` exists as a separate tool is that the verdict alone
is not enough for an agent to act on. `dead` plus "no static reference exists,
and static analysis cannot see reflection" is actionable. `dead` on its own
invites exactly the confident, wrong deletion this document is about.

## Known limitations

These are the cases LineageLens gets wrong today, stated plainly so you can
recognise them rather than trusting a verdict through them.

**Attribute access on a receiver whose type cannot be inferred.** A `@property`
or `@cached_property` read as `obj.thing` only resolves when `obj`'s class is
known — from an annotation, a constructor call, or an annotated factory. It is not
known when `obj` comes from an untyped parameter, an untyped loop variable, or a
ternary (`x if cond else y`). Reference edges deliberately do not consult type
inference, because doing so at every attribute access would dominate analysis
time. Consequence: a property reached only through such a receiver may be
reported `dead`.

The practical mitigation is one you want anyway — annotate the parameter:

```python
def get_callers(graph: CodeGraph, symbol_id: str, index: GraphIndex | None = None):
```

Running LineageLens on itself, that single annotation removed 26 false positives,
because one unresolvable receiver had made an entire chain of `cached_property`
methods unreachable.

**Reflective dispatch beyond the modelled patterns.** `ast.NodeVisitor` subclasses
are handled, as are Celery tasks, pytest fixtures, Django management commands and
`[project.scripts]`. Anything else that resolves a name at runtime — a plugin
registry read from a config file, `getattr(module, name)`, an entry-point group —
is invisible. Use `analysis.entry_points` for a whole family, or
`# lineagelens: keep` for one symbol.

**A custom `entry_points` family replaces that family's defaults.** Listing
`framework_callback` in your `lineagelens.yaml` replaces the default suffix list
for `framework_callback` only; other families keep their defaults. If you customise
a family, copy the defaults you still want. `lineagelens init` writes the full set
so this is visible.

**Docstring and comment references are not modelled, deliberately.** Prose about a
symbol is not a use of it. If prose is the only thing referring to a symbol, the
symbol is dead and the prose is stale.
