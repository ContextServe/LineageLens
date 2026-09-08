# Developing LineageLens

Architecture, the invariants that hold it together, and how to extend it.

Design rationale and the measured baseline that motivated the current shape are
in issue #51; section references below (§7.1, §10.4, …) point there.

## Install and build from source

```bash
git clone https://github.com/pranayVyas/LineageLens
cd LineageLens
python -m venv .venv && source .venv/bin/activate
pip install -e '.[mcp,watch,dev]'

pytest                    # 323 tests, ~3s
lineagelens index .       # index this repository
```

Build a wheel:

```bash
pip install build
python -m build --wheel        # -> dist/lineagelens-*.whl
```

### What installs automatically, and what does not

**Automatic.** All eight tree-sitter grammars are pinned *core* dependencies,
not extras, because Tier A extraction is not optional (§7.1). So a plain
`pip install lineagelens` gives you working extraction for all six languages.
jedi comes with it too, which is Python's Tier B resolver — vendored as a
wheel, so Python needs nothing external.

Grammar versions are pinned **exactly** (`==`, never `>=`). A grammar upgrade
changes parse output, which must invalidate the index rather than silently
change results; the pinned set's digest is recorded in
`graph_meta.grammar_digest`. A floor here would make `lineagelens verify`
unenforceable.

**Extras:**

| Extra | For |
|---|---|
| `[mcp]` | the MCP server (`lineagelens mcp`, `lineagelens-mcp`) |
| `[watch]` | `IndexWatcher` (needs `watchdog`) |
| `[scip]` | SCIP as a Tier B alias feed |
| `[dev]` | pytest and ruff |

**Not automatic — system toolchains.** Java, Go, Rust and C# Tier B resolvers
are external programs (`javac`, `gopls`, `rust-analyzer`, `dotnet`) and cannot
be pip-installed. Check what this machine has:

```bash
lineagelens ontology
```

A language with no available resolver still indexes, at Tier A. What you lose
is resolution quality: more references land in `unresolved_refs` as *ambiguous*
rather than becoming edges. None are guessed — the resolver's never-pick rule
holds regardless of tier — and the tier used is reported per language on every
answer's coverage envelope.

Make it strict where a partial graph should be an error:

```bash
lineagelens index . --require-tier-b          # every language
lineagelens index . --require-tier-b=java,csharp
```

This is a deliberate reversal of §7.2, which made Tier B a hard requirement.
That reasoning does not survive the resolver as built: §7.2 argued a
Tier-A-only Java graph is the fabricated-edge failure of §1.2, but fabrication
came from *picking among candidates*, which this resolver never does. Refusing
the language costs everything — nodes, structure, contracts, data flow — to
avoid a risk one layer down already prevents. The Dubbo benchmark settles it:
100,122 nodes and 425,029 edges across 15 kinds, produced with `javac` merely
*detected* and never actually answering a query.

### No configuration, no init step

There is no `init` command and no config file. `lineagelens.yaml` used to
configure source roots, an engine choice and a relation-kind allowlist; all
three are gone:

- **source roots** — every file is walked and language is detected per file, so
  there is nothing to declare. Services are discovered from build manifests
  (`pom.xml`, `package.json`, `go.mod`, `Cargo.toml`, `pyproject.toml`,
  `*.csproj`, `Dockerfile`).
- **engine** — there is no user-selectable engine. Extraction is per file and
  additive.
- **relation kinds** — all of them are always emitted. The allowlist existed to
  bisect regressions in a design where each kind was a separate risky pass.

What is left is two flags on `index`: `--dataflow` and `--allow-tier-a-only`.

The one optional directory is `.lineagelens/adapters/`, for contract adapters
describing an in-house framework. Nothing is required to be there.

`index` writes `.lineagelens/graph.sqlite`. Add `.lineagelens/` to
`.gitignore`.

## The one idea

**Extractors emit observations. Only the resolver creates edges.**

An extractor reports *"at `file:line:col` there is a call-kind reference with
text `save` and receiver type hint `OrderRepo`"*. It has no way to express an
edge, because `Observation` has no `edges` field. The resolver decides what
that binds to and attaches evidence and provenance.

Everything else follows from that split:

- an extractor cannot fabricate an edge by name matching — it cannot write
  edges at all
- an extractor cannot silently drop a failed reference — unresolved references
  are its *output format*, not a leftover
- extractors are per file, so merging languages is the default rather than a
  special mode
- one component owns persistence, so lossiness is a schema property rather than
  an emergent accident

The previous architecture had each engine parse *and* resolve *and* name symbols
*and* write a complete graph. Quality, ID scheme, ontology coverage and
persistence fidelity all forked per engine, and each fork drifted.

## Layers

```
extract/     per-language declarative specs -> observations
  spec/<lang>/{nodes,refs,dataflow}.scm     tree-sitter queries (data, not code)
  langs.py                                  per-FILE detection, pinned grammars
  engine.py                                 the single extractor for all six

resolve/     observations -> edges, ambiguities, boundaries
  index.py                                  scope/member/import lookup
  resolver.py                               the only component that makes edges
  oracles.py                                Tier B: two methods, six toolchains

contracts/   cross-framework and cross-service joins
  normalise.py                              the key normaliser (the crux)
  adapters.py                               30 frameworks as YAML
  detect.py                                 applies them; reads SPI registries

store/       schema 4 — one node table, one edge table, spans on both
query/       twelve primitives; intent, budget, envelope
mcp/         thin shell over query (four lines per tool)
conformance/ corpora + the generated capability matrix
```

`core/` holds the vocabulary — spans, kinds, record types, identity — and
depends on nothing else, so there is exactly one definition of what a node, an
edge and a span are.

## Invariants, and where they are enforced

| Invariant | Enforced by |
|---|---|
| Every edge has both endpoints | FK constraints + `dangling_edge_count()` assertion after every build |
| Every reference is accounted for | `Coverage.__post_init__` + a `CHECK` constraint |
| No arbitrary pick among candidates | `Resolver._resolve_ref`; `test_resolve.py::TestNeverPicksArbitrarily` |
| Missing grammar skips, never degrades | `SpecExtractor.extract`; `test_extract_engine.py::TestFailureIsRecordedNotHidden` |
| Same commit, same graph | `GraphStore.compute_build_digest`; `lineagelens verify` |
| No cross-language edge except via a contract | `Resolver._candidates`; `test_resolve.py::test_cross_language_names_never_join` |
| Ontology matches behaviour | `test_conformance.py::TestMatrixIsCurrent` |

If you change resolution, run `lineagelens verify` on a real repository. It is
the check that catches order dependence, and order dependence is the failure
mode that hides best.

## Identity (§6)

```
qualified_name = <service>/<lang>/<module-path>#<member>[/<member>...]
signature_hash = h(normalised parameter types, arity)     # "" for non-callables
id             = h(service, lang, qualified_name, signature_hash)
```

Four constraints, each ruling something out:

- **stable across extractors** — Tier A and Tier B observing one symbol must
  converge, so IDs derive only from declared structure
- **stable across edits** — nothing may derive from a line number, or editing
  line 10 renames the symbol on line 400 and invalidates every edge touching it
- **collision-free for overloads** — Java, C#, C++ and Go all permit same-name
  callables in one scope, so the signature is part of the key
- **deterministic** — never `hash()`; Python randomises it per process

Anonymous constructs use an ordinal among same-kind siblings, never a position.

## Adding a language

Four files and a fixture. No Python.

1. **Pin the grammar** in `pyproject.toml` — exactly, not with `>=`. A grammar
   upgrade changes parse output and must invalidate the index; a floor makes
   `verify` unenforceable.
2. **Register it** in `extract/langs.py`: a `Grammar` entry and an extension
   mapping. A dialect with a different grammar (TSX vs TypeScript) gets its own
   entry with the same logical `lang`.
3. **Write `spec/<lang>/`**:

   ```
   lang.toml       dialects, implicit_receivers, constructor_names
   nodes.scm       @node.<kind> + @name @params @return_type @type
                   @docstring @visibility @decorator @type_param @modifier
   refs.scm        @ref.<kind> + @ref.name @receiver @ref.arg
   dataflow.scm    @flow.<read|write> + @flow.name @flow.value
   ```

   A dialect-only pattern goes in an overlay: `refs.tsx.scm` is appended when
   compiling for `tsx` only.

4. **Add a corpus** at `conformance/corpus/<lang>/` and an `Expectation` in
   `conformance/runner.py`, then regenerate:

   ```bash
   python -m lineagelens.conformance.runner
   ```

### Spec conventions worth knowing

These were each learned by getting them wrong:

- **Anchor on the node you mean.** `(parameters (identifier) @name) @node.parameter`
  anchors on the whole parameter *list*, so every plain parameter gets the same
  span and siblings become each other's ancestors. Anchor on the identifier.
- **Several patterns matching one construct is normal**, and they merge by
  span with the most specific kind winning. That is how optional captures (a
  docstring, a type annotation) layer. But a decorated declaration must anchor
  on the *inner* declaration, or it gets a different span and becomes a second
  node.
- **Emit `@node.function` for every callable.** The engine promotes it to
  `method` or `constructor` from position and `constructor_names`, so the rule
  lives in one place for all six languages.
- **A `.` anchor before a child means first; after means last.** A docstring is
  the first statement.
- **Declare binding forms as nodes**, not only as data-flow writes. `except ... as e`
  without a declaration node leaves a later read of `e` out of scope, which
  turns into a project-wide ambiguity.
- **Field names differ between grammars.** C# uses `returns:` for a method's
  return type but `type:` for an operator's; JavaScript labels a class field's
  name `property:` where TypeScript uses `name:`. Probe rather than assume:

  ```python
  cursor = node.walk(); cursor.goto_first_child()
  print(cursor.node.type, cursor.field_name)
  ```

## Writing a contract adapter

Frameworks are data. Drop a YAML file in `.lineagelens/adapters/` (project) or
`contracts/definitions/` (shipped):

```yaml
- id: myframework.route
  lang: python
  match: decorator          # decorator | annotation | call | type | import
  names: [myrouter.get, myrouter.post]
  methods: [get, post]
  contract: {kind: http_route, role: exposes, key_arg: 0}
```

`role` is `exposes` or `consumes`; the two sides join when their normalised keys
match. `key_from_type: true` keys on the annotated declaration's type instead of
an argument — that is how Dubbo's `@DubboReference private Greeting greeting;`
names its contract.

Two traps:

- **Quote `on`, `off`, `yes`, `no`, `y`, `n`.** YAML 1.1 reads them as
  booleans. The loader rejects a non-string name with a message saying so,
  because an unquoted `on` once crashed a whole index.
- **Do not write generic adapters.** `emit`, `on`, `publish`,
  `addEventListener` match every DOM handler in a frontend and would
  manufacture a topic contract per listener. A fabricated contract is worse than
  a missing one — and a missing one is reported as an unclaimed declaration
  anyway.

If you add a contract kind, add a normaliser in `contracts/normalise.py`.
Without one it falls back to exact text matching, which joins far less than it
should. The HTTP normaliser is the reference: seven parameter syntaxes collapse
to one key, and getting that wrong splits the contract in two so the join
silently disappears.

## Data flow (§9)

Four rules, deterministic:

1. `WRITES` — assignment targets
2. `READS` — value-position references
3. `PARAM_BINDS` — argument *N* at a resolved call site → parameter *N*
4. `RETURNS` — callee → the call site's assignment target

`FLOWS_TO` is the closure over those, computed at query time rather than
materialised.

Two subtleties that matter:

- `PARAM_BINDS` binds from the **argument's own node** when the argument is a
  plain name. Binding from the enclosing body — all the reference itself
  identifies — leaves the value disconnected and a walk stops dead at the call.
- An implicit receiver (`self`, `this`, a Go method receiver) occupies
  parameter 0 in the declaration but not at the call site, so it is dropped
  before binding.

Where a chain needs alias analysis, container contents or closure capture, emit
a `Boundary` with candidates. Never an edge.

## Adding a query primitive

Put the traversal in `query/traverse.py` or `query/analysis.py`, then expose it
on `QueryEngine` and add a four-line MCP tool. Requirements:

- accept `intent` and `budget`, with the cheapest correct default
- filter edge kinds **server-side**
- return `QueryResult`, so truncation and the envelope are never ambiguous
- add to the envelope any boundary the walk crossed

Two traversal shapes, and choosing wrong is a real bug:

- `find_paths` scopes `visited` **per path**, because every route is wanted.
- `walk` uses a **global** `visited`, because each node is reported once at its
  true minimum distance.

Using a global set for the first is what made the old `get_lineage` report a
one-hop target at depth 3.

## Tests

```bash
pytest                                      # 323 tests, ~3s
pytest tests/test_conformance.py            # regenerate-and-compare the matrix
lineagelens verify . --incremental          # determinism on a real tree
ruff check src tests
```

Conventions:

- **Cross-language invariants run against the same program written seven ways**
  (`test_extract_engine.py`). A rule that holds only for Python fails.
- **Assert on presence, not counts.** Exact counts break on any improvement and
  get "fixed" by pasting whatever the code produced, which tests nothing.
- **Name the defect a test guards.** Most tests here exist because something
  specific went wrong; the docstring says what, so a future reader can tell a
  real regression from a stale expectation.

## Performance

Dubbo (4,046 files, 120 modules) indexes in ~31s eager. Three things dominated
before they were fixed, and all three were invisible at small scale:

- a per-file scan of the global edge list — quadratic, 2 billion iterations
- `shutil.which` per *reference* for oracle availability — 29,818 PATH scans
- per-file writes — 1,435 `executemany` calls for 288 files

The lesson: profile on the large corpus. A 141-file repository will not show
any of these.

## Known limits

Stated so they are not rediscovered as bugs. Each is in `ontology.py`'s
`limitations` and reported through the coverage envelope.

- **Go** interface satisfaction is structural; `IMPLEMENTS` needs
  whole-program method-set matching and is currently partial.
- **Rust** blanket impls (`impl<T: Display> Foo for T`) have no single
  resolvable target.
- **C#** partial classes span files and extension methods dispatch on static
  imports; neither is modelled.
- **Tier B answering is unimplemented for javac/gopls/rust-analyzer/roslyn.**
  Availability is detected and reported, so the matrix shows `detected` while
  resolution is Tier A quality. This is the largest open item.
- **Removed with the schema-3 core**: the GraphQL API, the web UI, and the
  dead-code ratchet. The ratchet needs redesign rather than a port — entry
  points now derive from contracts, so reachability is a different computation.
