"""Resolution guarantees (issue #51 §4, §16).

One rule carries this layer: **never pick arbitrarily**. When several candidates
match, record the ambiguity and emit no edge.

That is the single behavioural difference from schema 3, which matched a
reference by bare trailing name, took ``possible_targets[0]``, and discarded
whatever was left over. On Apache Dubbo the result was 2,593 edges all reporting
``resolution=resolved``, every one with a dangling source, and a graph with zero
traversable paths that reported success.
"""

from __future__ import annotations

import pytest

from lineagelens.core import (
    EdgeKind,
    RefStatus,
    Resolution,
)
from lineagelens.extract import SpecExtractor
from lineagelens.resolve import Resolver, SymbolIndex


@pytest.fixture(scope="module")
def extractor():
    return SpecExtractor()


def index_sources(extractor, sources: dict[str, tuple[str, bytes]]):
    """Extract several files and resolve them together.

    ``sources`` maps a path to ``(dialect, content)``. Resolution needs the whole
    node set, because a call can target any file.
    """
    observations = []
    for path, (dialect, content) in sorted(sources.items()):
        observations.append(extractor.extract(
            path=path, content=content, dialect=dialect, content_hash=path,
            service="app", module_path=path.rsplit(".", 1)[0].replace("/", "."),
        ))
    nodes = [n for o in observations for n in o.nodes]
    index = SymbolIndex(nodes)
    return index, Resolver(index).resolve(observations)


def edges_of(result, kind: EdgeKind):
    return [e for e in result.edges if e.kind is kind]


# ---------------------------------------------------------------------------
# the core rule
# ---------------------------------------------------------------------------


class TestNeverPicksArbitrarily:
    def test_two_same_name_targets_produce_no_edge(self, extractor):
        """Two unrelated `save` methods must not yield a fabricated call edge."""
        index, result = index_sources(extractor, {
            "a.py": ("python", b"class DbRepo:\n    def save(self, x):\n        pass\n"),
            "b.py": ("python", b"class FileRepo:\n    def save(self, x):\n        pass\n"),
            "c.py": ("python", b"def go(thing):\n    thing.save(1)\n"),
        })
        saves = [e for e in edges_of(result, EdgeKind.CALLS)
                 if (n := index.get(e.dst)) and n.name == "save"]
        assert not saves, "an unresolvable receiver must not produce a call edge"

        ambiguous = [r for r in result.unresolved
                     if r.ref_text == "save" and r.status is RefStatus.AMBIGUOUS]
        assert ambiguous, "the ambiguity must be recorded, not dropped"
        assert len(ambiguous[0].candidates) == 2, "both candidates must be retained"

    def test_ambiguity_is_reported_as_a_boundary(self, extractor):
        """An enumerated ambiguity beats both a guess and silence (§9.1)."""
        _, result = index_sources(extractor, {
            "a.py": ("python", b"class A:\n    def run(self):\n        pass\n"),
            "b.py": ("python", b"class B:\n    def run(self):\n        pass\n"),
            "c.py": ("python", b"def go(t):\n    t.run()\n"),
        })
        assert any(b.candidates and len(b.candidates) == 2 for b in result.boundaries)

    def test_unique_target_does_resolve(self, extractor):
        """The rule is "no guessing", not "no resolving"."""
        index, result = index_sources(extractor, {
            "repo.py": ("python", b"def persist(x):\n    return x\n"),
            "svc.py": ("python", b"from repo import persist\n\ndef go(v):\n    persist(v)\n"),
        })
        calls = [e for e in edges_of(result, EdgeKind.CALLS)
                 if (n := index.get(e.dst)) and n.name == "persist"]
        assert len(calls) == 1
        assert calls[0].resolution in (Resolution.EXACT, Resolution.INFERRED)


class TestScopeResolution:
    def test_self_receiver_resolves_against_the_enclosing_type(self, extractor):
        index, result = index_sources(extractor, {
            "a.py": ("python", b"""
class Svc:
    def helper(self):
        pass

    def go(self):
        self.helper()
"""),
        })
        calls = [e for e in edges_of(result, EdgeKind.CALLS)
                 if (n := index.get(e.dst)) and n.name == "helper"]
        assert len(calls) == 1
        assert calls[0].resolution is Resolution.EXACT

    def test_inherited_method_resolves_through_a_resolved_base(self, extractor):
        """Member lookup follows bases, which requires INHERITS to exist first.

        This is why the resolver runs structural references in a separate,
        earlier phase -- otherwise the result would depend on file order, which
        §11 forbids.
        """
        index, result = index_sources(extractor, {
            "base.py": ("python", b"class Base:\n    def shared(self):\n        pass\n"),
            "sub.py": ("python", b"""
from base import Base


class Sub(Base):
    def go(self):
        self.shared()
"""),
        })
        assert edges_of(result, EdgeKind.INHERITS), "base must resolve first"
        calls = [e for e in edges_of(result, EdgeKind.CALLS)
                 if (n := index.get(e.dst)) and n.name == "shared"]
        assert len(calls) == 1, "inherited member should resolve via the base"

    def test_declared_parameter_type_resolves_the_receiver(self, extractor):
        """`repo.save()` where repo is declared `Repository` is deterministic."""
        index, result = index_sources(extractor, {
            "repo.py": ("python", b"class Repository:\n    def save(self, x):\n        pass\n"),
            "svc.py": ("python", b"""
from repo import Repository


def go(repo: Repository, v):
    repo.save(v)
"""),
        })
        calls = [e for e in edges_of(result, EdgeKind.CALLS)
                 if (n := index.get(e.dst)) and n.name == "save"]
        assert len(calls) == 1
        assert calls[0].resolution is Resolution.EXACT

    def test_out_of_scope_read_is_external_not_ambiguous(self, extractor):
        """A local read that is not in scope is out of scope, not a mass ambiguity.

        Falling back to a project-wide name lookup here produced 130 candidates
        for a single read of `graph`, spanning Python locals and Java parameters.
        That is noise, not information.
        """
        _, result = index_sources(extractor, {
            "a.py": ("python", b"def one():\n    total = 1\n    return total\n"),
            "b.py": ("python", b"def two():\n    total = 2\n    return total\n"),
            "c.py": ("python", b"def three():\n    total = 3\n    return total\n"),
        })
        totals = [r for r in result.unresolved if r.ref_text == "total"]
        assert not any(r.status is RefStatus.AMBIGUOUS for r in totals), (
            "a local read must never be ambiguous across files"
        )

    def test_cross_language_names_never_join(self, extractor):
        """A Python call must not target a Java method of the same name.

        Genuine cross-language links go through contract nodes (§8), never
        through a shared identifier.
        """
        index, result = index_sources(extractor, {
            "svc.py": ("python", b"def go(t):\n    process()\n"),
            "Svc.java": ("java", b"class Svc { void process() {} }"),
        })
        for edge in result.edges:
            src, dst = index.get(edge.src), index.get(edge.dst)
            if src and dst:
                assert src.lang == dst.lang, (
                    f"{edge.kind} joined {src.lang} to {dst.lang}"
                )


# ---------------------------------------------------------------------------
# derived edges
# ---------------------------------------------------------------------------


class TestStructuralEdges:
    def test_contains_is_materialised_from_lexical_nesting(self, extractor):
        """CONTAINS comes from parent_id, so nesting has one definition."""
        index, result = index_sources(extractor, {
            "a.py": ("python", b"class A:\n    def m(self, x):\n        pass\n"),
        })
        contains = edges_of(result, EdgeKind.CONTAINS)
        assert contains
        pairs = {
            (index.get(e.src).name, index.get(e.dst).name)
            for e in contains if index.get(e.src) and index.get(e.dst)
        }
        assert ("A", "m") in pairs
        assert ("m", "x") in pairs

    def test_java_interface_target_reclassifies_to_implements(self, extractor):
        """C# and Java write bases in one list; the target's kind decides.

        Reclassifying from the target is a fact. Guessing from an `I` name
        prefix would be a convention.
        """
        _, result = index_sources(extractor, {
            "P.java": ("java", b"interface Payable { void pay(); }"),
            "B.java": ("java", b"class Base {}"),
            "O.java": ("java", b"class Order extends Base implements Payable { public void pay(){} }"),
        })
        assert edges_of(result, EdgeKind.IMPLEMENTS), "interface target -> IMPLEMENTS"
        assert edges_of(result, EdgeKind.INHERITS), "class target -> INHERITS"

    def test_class_call_becomes_instantiates(self, extractor):
        index, result = index_sources(extractor, {
            "m.py": ("python", b"class Thing:\n    pass\n\n\ndef go():\n    Thing()\n"),
        })
        assert any(
            (n := index.get(e.dst)) and n.name == "Thing"
            for e in edges_of(result, EdgeKind.INSTANTIATES)
        )


class TestParamBinds:
    def test_arguments_bind_to_parameters_positionally(self, extractor):
        """The hop that carries a value across a call boundary (§9).

        Buildable only because the extractor keeps argument spans and positions.
        Schema 3 computed 3,326 argument lists and persisted none.
        """
        index, result = index_sources(extractor, {
            "r.py": ("python", b"def persist(first, second):\n    return first\n"),
            "s.py": ("python", b"""
from r import persist


def go(a, b):
    persist(a, b)
"""),
        })
        binds = edges_of(result, EdgeKind.PARAM_BINDS)
        assert len(binds) == 2, f"expected two bindings, got {len(binds)}"
        by_index = {e.metadata["arg_index"]: index.get(e.dst).name for e in binds}
        assert by_index == {0: "first", 1: "second"}

    def test_implicit_receiver_does_not_consume_argument_zero(self, extractor):
        """`self.save(x)` must bind x to the first real parameter, not to self."""
        index, result = index_sources(extractor, {
            "a.py": ("python", b"""
class A:
    def save(self, value):
        pass

    def go(self, v):
        self.save(v)
"""),
        })
        binds = edges_of(result, EdgeKind.PARAM_BINDS)
        assert binds, "no binding produced"
        assert all(index.get(e.dst).name != "self" for e in binds)
        assert index.get(binds[0].dst).name == "value"


# ---------------------------------------------------------------------------
# accounting (§16.5)
# ---------------------------------------------------------------------------


class TestAccounting:
    def test_every_reference_lands_in_exactly_one_bucket(self, extractor):
        """`refs_total == exact + inferred + unresolved`, per file.

        Enforced twice -- at construction and by a CHECK constraint -- which is
        what makes "zero silent drops" a checked property rather than a claim.
        """
        _, result = index_sources(extractor, {
            "a.py": ("python", b"""
import os


class A:
    x: int = 1

    def go(self, v):
        y = self.x + v
        return os.path.join(str(y))
"""),
        })
        assert result.coverage
        for path, coverage in result.coverage.items():
            assert coverage.refs_total == (
                coverage.refs_exact + coverage.refs_inferred + coverage.refs_unresolved
            ), f"{path} does not balance"

    def test_external_reference_is_retained(self, extractor):
        """A stdlib call is external, and must be recorded rather than dropped."""
        _, result = index_sources(extractor, {
            "a.py": ("python", b"def go(xs):\n    return len(xs)\n"),
        })
        externals = [r for r in result.unresolved if r.ref_text == "len"]
        assert externals
        assert externals[0].status is RefStatus.EXTERNAL

    def test_no_edge_has_a_dangling_endpoint(self, extractor):
        index, result = index_sources(extractor, {
            "a.py": ("python", b"class A:\n    def m(self):\n        self.m()\n"),
            "b.py": ("python", b"from a import A\n\n\ndef go():\n    A().m()\n"),
        })
        for edge in result.edges:
            assert index.get(edge.src) is not None, f"dangling src on {edge.kind}"
            assert index.get(edge.dst) is not None, f"dangling dst on {edge.kind}"


class TestDeterminism:
    def test_file_order_does_not_change_the_result(self, extractor):
        """Resolution must not depend on which file was extracted first.

        The structural-then-general phase split exists for this: member lookup
        follows resolved bases, so a single pass would resolve differently
        depending on whether the base's file came first.
        """
        sources = {
            "base.py": ("python", b"class Base:\n    def shared(self):\n        pass\n"),
            "sub.py": ("python", b"from base import Base\n\n\nclass Sub(Base):\n"
                                 b"    def go(self):\n        self.shared()\n"),
        }
        _, forward = index_sources(extractor, sources)
        _, reverse = index_sources(extractor, dict(reversed(list(sources.items()))))
        assert sorted(e.identity for e in forward.edges) == sorted(
            e.identity for e in reverse.edges
        )
