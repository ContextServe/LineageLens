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

from typing import ClassVar

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


# ---------------------------------------------------------------------------
# tier B capability versus discovery (#65)
# ---------------------------------------------------------------------------


class TestTierBIsGatedOnCapability:
    """`--require-tier-b` refused nothing for a detected-but-unwired toolchain.

    `ToolchainOracle.resolve` inherits the base's `None` -- deliberately, so
    the matrix can say "javac detected, resolution not wired" rather than
    claiming the language is unsupported. But `languages_without_tier_b` tested
    `available() == MISSING`, which answers "did we find a toolchain". A JDK on
    PATH therefore counted Java as having Tier B, so a user asking to be given
    only compiler-grade answers was silently served Tier A: the run succeeded,
    the envelope looked clean, and nothing indicated it.

    It was install-dependent, which is why no test caught it. Without a JDK,
    `javac` is `missing`, Java lands in `languages_without_tier_b`, and
    everything behaves. Every test here therefore fakes the toolchain rather
    than depending on the machine.
    """

    @staticmethod
    def registry(tmp_path):
        from lineagelens.resolve.oracles import OracleRegistry

        return OracleRegistry(project_root=tmp_path)

    def test_base_oracles_cannot_resolve_by_default(self):
        """False by default, so a new oracle is correct without doing anything.

        Defaulting to True would make every future oracle wrong until someone
        remembered to exclude it -- the wrong direction for a trust switch.
        """
        from lineagelens.resolve.oracles import _Base

        assert _Base().can_resolve() is False

    def test_every_oracle_claiming_tier_b_overrides_resolve(self, tmp_path):
        """A `can_resolve()` of True must be backed by a real implementation."""
        from lineagelens.resolve.oracles import _Base

        for oracles in self.registry(tmp_path)._by_lang.values():
            for oracle in oracles:
                if oracle.can_resolve():
                    assert type(oracle).resolve is not _Base.resolve, (
                        f"{oracle.name} claims Tier B but inherits the base resolve"
                    )

    def test_a_detected_but_unwired_toolchain_is_not_tier_b(self, tmp_path, monkeypatch):
        """The bug, asserted independently of whether this machine has a JDK."""
        from lineagelens.resolve.oracles import (
            OracleAvailability,
            ToolchainOracle,
        )

        # Pretend every toolchain was found on PATH.
        monkeypatch.setattr(
            ToolchainOracle, "_locate", lambda self: tmp_path / "fake-tool"
        )
        registry = self.registry(tmp_path)

        assert registry.availability()["java"]["javac"] == (
            OracleAvailability.DETECTED_UNWIRED
        )
        assert "java" in registry.languages_without_tier_b(), (
            "a found-but-unwired toolchain is being counted as Tier B"
        )

    def test_detected_unwired_is_distinct_from_detected(self):
        """Two states, because they mean different things to a caller."""
        from lineagelens.resolve.oracles import OracleAvailability

        assert OracleAvailability.DETECTED != OracleAvailability.DETECTED_UNWIRED

    def test_python_still_has_tier_b(self, tmp_path):
        """jedi is the one oracle that actually resolves; do not regress it."""
        registry = self.registry(tmp_path)
        assert "python" not in registry.languages_without_tier_b()
        assert registry.availability()["python"]["jedi"] == "vendored"

    def test_ontology_does_not_render_unwired_as_a_resolver(self, monkeypatch, tmp_path):
        """"javac (detected)" read as a capability. It was an inventory note."""
        from lineagelens.ontology import installed_tiers
        from lineagelens.resolve.oracles import ToolchainOracle

        monkeypatch.setattr(
            ToolchainOracle, "_locate", lambda self: tmp_path / "fake-tool"
        )
        java = installed_tiers()["java"]["tier_b"]
        assert "unwired" in java
        assert java != "javac (detected)"


class TestRequireTierBRefuses:
    """The refusal has to be observable: skipped files and a non-zero exit.

    Refusing to answer must not look like a successful index of nothing.
    """

    JAVA_PROJECT: ClassVar[dict[str, str]] = {
        "pom.xml": "<project><artifactId>svc</artifactId></project>",
        "src/main/java/Svc.java": (
            "public class Svc {\n"
            "    public String handle(String in) { return in; }\n"
            "}\n"
        ),
    }

    @staticmethod
    def build(tmp_path, files):
        root = tmp_path / "svc"
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return root

    def test_files_are_skipped_with_the_right_reason(self, tmp_path, monkeypatch):
        from lineagelens.core import SkipReason
        from lineagelens.indexer import Indexer
        from lineagelens.resolve.oracles import ToolchainOracle

        monkeypatch.setattr(
            ToolchainOracle, "_locate", lambda self: tmp_path / "fake-tool"
        )
        root = self.build(tmp_path, self.JAVA_PROJECT)
        store, report = Indexer(root, require_tier_b=frozenset({"java"})).run()

        assert report.tier_b_refused == ["java"]
        skipped = [
            f for f in store.files()
            if f.skip_reason == SkipReason.MISSING_TIER_B
        ]
        assert skipped, "no file was skipped despite the refusal"
        assert all(f.lang == "java" for f in skipped)

    def test_the_cli_exits_non_zero(self, tmp_path, monkeypatch, capsys):
        """Otherwise a CI job that asked for Tier B passes on zero answers."""
        from lineagelens.cli import main
        from lineagelens.resolve.oracles import ToolchainOracle

        monkeypatch.setattr(
            ToolchainOracle, "_locate", lambda self: tmp_path / "fake-tool"
        )
        root = self.build(tmp_path, self.JAVA_PROJECT)
        code = main(["index", str(root), "--require-tier-b=java"])
        assert code == 1
        assert "refused" in capsys.readouterr().err

    def test_a_language_with_tier_b_is_not_refused(self, tmp_path):
        from lineagelens.cli import main

        root = self.build(tmp_path, {
            "pyproject.toml": '[project]\nname = "p"\n',
            "app.py": "def f(x):\n    return x\n",
        })
        assert main(["index", str(root), "--require-tier-b=python"]) == 0
