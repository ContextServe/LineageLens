"""Extraction engine behaviour, across all six languages (issue #51 §7.1).

The point of a declarative spec is that one engine serves every language, so
these tests are written against a shared corpus: the same program expressed six
ways, with the same assertions applied to each. A rule that holds only for
Python is the failure mode the rearchitecture exists to remove -- schema 3's
Python analyser grew nine relation kinds while every other language got one.

Fixtures are inline rather than on disk because each is a few lines and the
assertions read better next to the source they describe. The conformance corpus
(§12) is the on-disk, exhaustive counterpart.
"""

from __future__ import annotations

import pytest

from lineagelens.core import NodeKind, ParseStatus, RefKind, SkipReason
from lineagelens.extract.engine import SpecExtractor
from lineagelens.extract.langs import MissingGrammar, ParserRegistry

# ---------------------------------------------------------------------------
# a shared program, six ways
# ---------------------------------------------------------------------------
#
# Each declares: a type, a field on it, a method taking one typed parameter,
# a local, a two-argument call on a receiver, and a return.

PYTHON = b'''
class Order(Base):
    total: float = 0.0

    def submit(self, repo: Repository) -> bool:
        """Submit it."""
        amount = self.total
        ok = repo.save(amount, self.total)
        return ok
'''

JAVA = b'''
class Order extends Base implements Payable {
    private double total = 0.0;

    public boolean submit(Repository repo) {
        double amount = this.total;
        boolean ok = repo.save(amount, this.total);
        return ok;
    }
}
'''

TYPESCRIPT = b'''
class Order extends Base implements Payable {
  total: number = 0.0;

  submit(repo: Repository): boolean {
    const amount = this.total;
    const ok = repo.save(amount, this.total);
    return ok;
  }
}
'''

JAVASCRIPT = b'''
class Order extends Base {
  total = 0.0;

  submit(repo) {
    const amount = this.total;
    const ok = repo.save(amount, this.total);
    return ok;
  }
}
'''

GO = b'''
package app

type Order struct {
	total float64
}

func (o *Order) Submit(repo Repository) bool {
	amount := o.total
	ok := repo.Save(amount, o.total)
	return ok
}
'''

RUST = b'''
pub struct Order {
    pub total: f64,
}

impl Payable for Order {
    fn submit(&self, repo: Repository) -> bool {
        let amount = self.total;
        let ok = repo.save(amount, self.total);
        ok
    }
}
'''

CSHARP = b'''
class Order : Base, IPayable {
    private double total = 0.0;

    public bool Submit(Repository repo) {
        var amount = this.total;
        var ok = repo.Save(amount, this.total);
        return ok;
    }
}
'''

CORPUS = {
    "python": (PYTHON, "python", "submit", "save"),
    "java": (JAVA, "java", "submit", "save"),
    "typescript": (TYPESCRIPT, "typescript", "submit", "save"),
    "javascript": (JAVASCRIPT, "javascript", "submit", "save"),
    "go": (GO, "go", "Submit", "Save"),
    "rust": (RUST, "rust", "submit", "save"),
    "csharp": (CSHARP, "csharp", "Submit", "Save"),
}


@pytest.fixture(scope="module")
def extractor():
    return SpecExtractor()


def run(extractor, dialect, source):
    return extractor.extract(
        path=f"app/order.{dialect}",
        content=source,
        dialect=dialect,
        content_hash="h",
        service="api",
        module_path="app.order",
    )


@pytest.fixture(params=sorted(CORPUS), ids=sorted(CORPUS))
def sample(request, extractor):
    source, dialect, method_name, call_name = CORPUS[request.param]
    obs = run(extractor, dialect, source)
    return request.param, obs, method_name, call_name


# ---------------------------------------------------------------------------
# cross-language invariants
# ---------------------------------------------------------------------------


class TestAllLanguages:
    def test_parses_without_error(self, sample):
        lang, obs, _, _ = sample
        assert obs.file.parse_status is ParseStatus.OK, (
            f"{lang}: {[e.message for e in obs.file.parse_errors]}"
        )

    def test_node_ids_are_unique(self, sample):
        """A duplicate id means two constructs collapsed onto one node."""
        lang, obs, _, _ = sample
        ids = [n.id for n in obs.nodes]
        assert len(ids) == len(set(ids)), f"{lang}: duplicate node ids"

    def test_containment_is_a_tree(self, sample):
        """Every parent_id must resolve, and nothing may be its own ancestor.

        Guards the two bugs found while building this: equal spans made matches
        parent and child of each other, and a parameter list anchored as one
        node made sibling parameters into ancestors.
        """
        lang, obs, _, _ = sample
        by_id = {n.id: n for n in obs.nodes}
        for node in obs.nodes:
            if node.parent_id is None:
                continue
            assert node.parent_id in by_id, f"{lang}: {node.name} has a dangling parent"
            seen, cur = {node.id}, by_id.get(node.parent_id)
            while cur is not None:
                assert cur.id not in seen, f"{lang}: containment cycle at {cur.name}"
                seen.add(cur.id)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None

    def test_declares_a_type(self, sample):
        lang, obs, _, _ = sample
        types = [n for n in obs.nodes if n.is_type]
        assert types, f"{lang}: no type node"
        assert any(n.name == "Order" for n in types), f"{lang}: Order not found"

    def test_declares_the_method_with_its_parameter(self, sample):
        lang, obs, method_name, _ = sample
        methods = [n for n in obs.nodes if n.name == method_name]
        assert methods, f"{lang}: {method_name} not found"
        params = [n for n in obs.nodes if n.kind is NodeKind.PARAMETER]
        assert any(p.name == "repo" for p in params), f"{lang}: parameter repo missing"

    def test_field_is_owned_by_the_type_not_the_method(self, sample):
        """`total` must be a member of Order however the language declares it.

        Python, TS, JS and C# write instance fields inside a method body, so
        plain lexical containment would parent them to the method and a query
        for the type's fields would come up empty.
        """
        lang, obs, _, _ = sample
        by_id = {n.id: n for n in obs.nodes}
        fields = [n for n in obs.nodes if n.kind is NodeKind.FIELD and n.name == "total"]
        assert fields, f"{lang}: field total not found"
        owner = by_id.get(fields[0].parent_id)
        assert owner is not None and owner.name == "Order", (
            f"{lang}: total is owned by {owner.name if owner else None}, not Order"
        )

    def test_records_the_call_with_a_receiver_hint(self, sample):
        """The receiver is what lets Tier B resolve `repo.save` rather than guess.

        Schema 3 discarded it, matched on the bare trailing name, and picked an
        arbitrary candidate -- which is how it reported 2,593 resolved edges on
        Dubbo with zero traversable paths.
        """
        lang, obs, _, call_name = sample
        calls = [r for r in obs.refs if r.ref_kind is RefKind.CALL and r.ref_text == call_name]
        assert calls, f"{lang}: call to {call_name} not observed"
        assert any(c.receiver_hint for c in calls), f"{lang}: no receiver hint captured"

    def test_call_arguments_are_indexed_in_source_order(self, sample):
        """PARAM_BINDS needs argument *positions*, not just a count (§9).

        tree-sitter emits one match per repeated capture, so a two-argument call
        arrives as two matches each holding one argument. Indexing within a match
        would label both 0, and binding would then hit position 0 twice and
        position 1 never.
        """
        lang, obs, _, call_name = sample
        calls = [r for r in obs.refs if r.ref_kind is RefKind.CALL and r.ref_text == call_name]
        with_args = [c for c in calls if c.metadata.get("args")]
        assert with_args, f"{lang}: no arguments captured for {call_name}"
        args = with_args[0].metadata["args"]
        assert len(args) == 2, f"{lang}: expected 2 arguments, got {[a['text'] for a in args]}"
        assert [a["index"] for a in args] == [0, 1], f"{lang}: indices {args}"
        assert args[0]["start_byte"] < args[1]["start_byte"], f"{lang}: args out of order"

    def test_every_ref_attaches_to_a_real_node(self, sample):
        """A reference's from_node must exist, or its edge would dangle."""
        lang, obs, _, _ = sample
        ids = {n.id for n in obs.nodes}
        orphans = [r.ref_text for r in obs.refs if r.from_node not in ids]
        assert not orphans, f"{lang}: refs attached to unknown nodes: {orphans[:5]}"

    def test_emits_no_edges(self, sample):
        """The §4 invariant: an extractor cannot express a resolved edge."""
        _, obs, _, _ = sample
        assert not hasattr(obs, "edges")

    def test_dataflow_observed(self, sample):
        """Reads and writes are emitted as references, so one resolver handles both."""
        lang, obs, _, _ = sample
        kinds = {r.ref_kind for r in obs.refs}
        assert RefKind.WRITE in kinds, f"{lang}: no writes observed"
        assert RefKind.READ in kinds, f"{lang}: no reads observed"


# ---------------------------------------------------------------------------
# language-specific expectations
# ---------------------------------------------------------------------------


class TestLanguageSpecifics:
    def test_python_async_and_docstring(self, extractor):
        obs = run(extractor, "python", b'async def go(x: int) -> str:\n    """Doc."""\n    return ""\n')
        fn = next(n for n in obs.nodes if n.name == "go")
        assert fn.has(__import__("lineagelens.core", fromlist=["NodeFlags"]).NodeFlags.ASYNC)
        assert fn.docstring == "Doc."
        assert fn.return_type == "str"

    def test_python_constructor_is_promoted_by_name(self, extractor):
        obs = run(extractor, "python", b"class A:\n    def __init__(self):\n        pass\n")
        assert any(n.kind is NodeKind.CONSTRUCTOR for n in obs.nodes)

    def test_python_decorated_function_is_one_node(self, extractor):
        """A decorated def must not become a second node (see _merge_by_span)."""
        obs = run(extractor, "python", b"@memoize\ndef helper(x: int) -> int:\n    return x\n")
        helpers = [n for n in obs.nodes if n.name == "helper"]
        assert len(helpers) == 1
        assert helpers[0].decorators, "decorator was not captured onto the merged node"

    def test_java_overloads_are_distinct_nodes(self, extractor):
        """The case Tier A cannot resolve but *must* keep separate (§6).

        Schema 3 keyed on name alone, so all three of these collapsed into one
        node and their callers merged.
        """
        obs = run(extractor, "java", b"""
class A {
    void save(int x) {}
    void save(String x) {}
    void save(int x, int y) {}
}
""")
        saves = [n for n in obs.nodes if n.name == "save"]
        assert len(saves) == 3
        assert len({n.id for n in saves}) == 3, "overloads collapsed onto one id"
        assert len({n.signature_hash for n in saves}) == 3

    def test_java_separates_extends_from_implements(self, extractor):
        """Schema 3 had one INHERITS kind and emitted 2 edges over 88 classes."""
        obs = run(extractor, "java", b"class A extends B implements C, D {}")
        inherits = {r.ref_text for r in obs.refs if r.ref_kind is RefKind.INHERIT}
        implements = {r.ref_text for r in obs.refs if r.ref_kind is RefKind.IMPLEMENT}
        assert "B" in inherits
        assert {"C", "D"} <= implements

    def test_java_captures_annotations(self, extractor):
        """Dubbo and Spring wiring lives in annotations; contracts read these (§8.2)."""
        obs = run(extractor, "java", b"""
class Svc {
    @DubboReference
    private Greeting greeting;

    @RequestMapping("/api/users")
    public void list() {}
}
""")
        decorated = {r.ref_text for r in obs.refs if r.ref_kind is RefKind.DECORATE}
        assert {"DubboReference", "RequestMapping"} <= decorated

    def test_typescript_interface_and_type_alias(self, extractor):
        """Node kinds schema 3 had no representation for at all."""
        obs = run(extractor, "typescript", b"interface Payable { pay(): void }\ntype Id = string;\n")
        kinds = {n.kind for n in obs.nodes}
        assert NodeKind.INTERFACE in kinds
        assert NodeKind.TYPE_ALIAS in kinds

    def test_typescript_arrow_function_is_named(self, extractor):
        """The dominant declaration form in modern TS; anonymous would lose it."""
        obs = run(extractor, "typescript", b"const handler = (req: Request): void => {};\n")
        assert any(n.name == "handler" and n.is_callable for n in obs.nodes)

    def test_tsx_component_call_needs_the_overlay(self, extractor):
        """JSX only compiles against the TSX grammar, hence the dialect overlay."""
        obs = extractor.extract(
            path="web/App.tsx",
            content=b"export function App() { return <UserCard id={1} />; }\n",
            dialect="tsx", content_hash="h", service="web", module_path="web.App",
        )
        calls = {r.ref_text for r in obs.refs if r.ref_kind is RefKind.CALL}
        assert "UserCard" in calls

    def test_tsx_ignores_lowercase_html_tags(self, extractor):
        """A `<div>` is not a symbol. The old regex scanner matched inside strings."""
        obs = extractor.extract(
            path="web/App.tsx",
            content=b"export function App() { return <div className='x' />; }\n",
            dialect="tsx", content_hash="h", service="web", module_path="web.App",
        )
        assert "div" not in {r.ref_text for r in obs.refs if r.ref_kind is RefKind.CALL}

    def test_go_struct_and_interface(self, extractor):
        obs = run(extractor, "go", b"package m\ntype S struct{ a int }\ntype I interface{ M() }\n")
        kinds = {n.kind for n in obs.nodes}
        assert NodeKind.STRUCT in kinds
        assert NodeKind.INTERFACE in kinds

    def test_rust_trait_impl_is_an_implement_ref(self, extractor):
        obs = run(extractor, "rust", b"impl Handler for Server { fn f(&self) {} }\n")
        assert "Handler" in {r.ref_text for r in obs.refs if r.ref_kind is RefKind.IMPLEMENT}

    def test_csharp_property_and_enum(self, extractor):
        obs = run(extractor, "csharp", b"""
class A { public string Name { get; set; } }
enum E { X, Y }
""")
        kinds = {n.kind for n in obs.nodes}
        assert NodeKind.PROPERTY in kinds
        assert NodeKind.ENUM in kinds
        assert NodeKind.ENUM_MEMBER in kinds


# ---------------------------------------------------------------------------
# honesty about gaps (§7.1)
# ---------------------------------------------------------------------------


class TestFailureIsRecordedNotHidden:
    def test_syntax_error_is_partial_with_a_boundary(self, extractor):
        """A broken file must report the gap, not silently under-extract.

        Every negative answer over an unparsed region is unreliable, so the
        region is recorded as a boundary and surfaced by any query crossing it.
        """
        obs = run(extractor, "python", b"def ok():\n    pass\n\ndef broken(:\n    pass\n")
        assert obs.file.parse_status is ParseStatus.PARTIAL
        assert obs.file.parse_errors
        assert obs.boundaries
        assert any("syntax" in b.detail or "missing" in b.detail for b in obs.boundaries)

    def test_missing_grammar_skips_rather_than_degrades(self, extractor, monkeypatch):
        """The single most consequential behaviour change in the extractor.

        Schema 3 fell through to a regex line scanner whenever a grammar was
        absent -- which was always, since no grammar package was ever declared.
        On Dubbo that produced 4,297 "classes" matched from `"class " in line`
        and five "methods" that were string literals from a switch statement,
        all reported as a successful extraction.
        """
        def refuse(self, dialect):
            raise MissingGrammar(f"no grammar for {dialect}")

        monkeypatch.setattr(ParserRegistry, "parser_for", refuse)
        obs = run(SpecExtractor(), "python", PYTHON)

        assert obs.file.parse_status is ParseStatus.SKIPPED
        assert obs.file.skip_reason is SkipReason.MISSING_GRAMMAR
        assert obs.nodes == [], "a skipped file must yield no nodes, not guessed ones"
        assert obs.refs == []


class TestDeterminism:
    @pytest.mark.parametrize("lang", sorted(CORPUS), ids=sorted(CORPUS))
    def test_extraction_is_reproducible(self, extractor, lang):
        """Same bytes must give the same ids and order, or §11 cannot hold.

        tree-sitter returns matches in an unspecified order, so the engine sorts
        by position; without that, anonymous-member ordinals and the containment
        walk would both vary between runs.
        """
        source, dialect, _, _ = CORPUS[lang]
        first = run(extractor, dialect, source)
        second = run(SpecExtractor(), dialect, source)
        assert [n.id for n in first.nodes] == [n.id for n in second.nodes]
        assert [(r.ref_text, r.span.start_byte) for r in first.refs] == [
            (r.ref_text, r.span.start_byte) for r in second.refs
        ]
