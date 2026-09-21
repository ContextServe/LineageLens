"""The conformance suite and the generated capability matrix (issue #51 §12).

Schema 3's ontology was hand-written prose: it declared four relation kinds
while nine were emitted, omitted ``REFERENCES`` (657 edges) and ``ANNOTATES``
(235), and advertised tools that returned nothing. Agents were told to trust a
contract that misdescribed a fifth of the graph.

The fix is not "write better prose" -- it is to make the description a *test
artifact*. Generating it means running the extractor and resolver over a corpus,
so it cannot drift. These tests enforce that:

* the committed matrix matches what regenerating it produces (§16.26)
* every language's corpus actually yields what the matrix claims
* the ontology reports "untested" rather than inventing a value when a
  measurement is absent
"""

from __future__ import annotations

import json

import pytest

from lineagelens.conformance import (
    EXPECTATIONS,
    MATRIX_PATH,
    run_all,
    run_language,
)
from lineagelens.core import PERSISTED_EDGES, EdgeKind, NodeKind
from lineagelens.ontology import (
    capability_matrix,
    node_kinds,
    ontology_instructions,
    relation_kinds,
)

LANGUAGES = sorted(EXPECTATIONS)


@pytest.fixture(scope="module")
def committed() -> dict:
    assert MATRIX_PATH.is_file(), (
        "matrix.json is missing. Generate it with: "
        "python -m lineagelens.conformance.runner"
    )
    return json.loads(MATRIX_PATH.read_text("utf-8"))


class TestMatrixIsCurrent:
    def test_regenerating_produces_no_change(self, committed):
        """Ontology drift is a build failure (§16.26).

        The committed matrix is what ``get_ontology`` serves. If the code has
        moved on and the matrix has not, agents are reading a stale contract --
        which is the schema-3 failure, just with a JSON file instead of a dict.
        """
        fresh = run_all()
        assert fresh["languages"] == committed["languages"], (
            "matrix.json is stale. Regenerate with: "
            "python -m lineagelens.conformance.runner"
        )

    def test_every_language_with_a_spec_is_measured(self, committed):
        missing = sorted(set(LANGUAGES) - set(committed["languages"]))
        assert not missing, f"languages absent from the matrix: {missing}"


@pytest.mark.parametrize("lang", LANGUAGES)
class TestPerLanguage:
    def test_corpus_parses_and_yields_nodes(self, lang):
        result = run_language(lang)
        assert result.files > 0, f"{lang}: corpus produced no parsed files"
        assert result.node_kinds, f"{lang}: no nodes extracted"

    def test_declared_node_kinds_are_all_produced(self, lang):
        """A node kind the ontology lists must actually be extractable.

        This is the check that catches a spec typo: a query naming a node type
        that does not exist compiles fine and silently matches nothing.
        """
        result = run_language(lang)
        assert not result.missing_nodes, (
            f"{lang}: expected but never produced: {result.missing_nodes}"
        )

    def test_an_l2_language_reaches_calls_and_dataflow(self, lang):
        """The §15 floor, now scoped to the languages that claim it.

        Tier A brings an **L2** language to nodes, calls and data flow, and
        anything below that is a broken spec rather than a language
        limitation. An L0 language has no refs.scm and no dataflow.scm, so
        requiring those edges would report a failure for a capability nobody
        claimed -- which is exactly the conflation the ladder exists to end
        (#56, #61).

        What stops an L0 language hiding a broken spec is the level
        cross-check in the runner: a language claiming L1 or L2 whose corpus
        produced no corroborating edges fails the run outright.
        """
        from lineagelens.extract.spec import SpecRegistry

        level = SpecRegistry().levels().get(lang, "L0")
        result = run_language(lang)
        if level != "L2":
            # Must be `n/a`, not False: "no such capability claimed" and
            # "claimed and absent" are different states.
            assert result.capabilities["calls"] == "n/a"
            assert result.capabilities["dataflow"] == "n/a"
            return
        assert result.capabilities["calls"] is True, f"{lang}: no CALLS edges"
        assert result.capabilities["dataflow"] is True, f"{lang}: no data-flow edges"

    def test_partial_capabilities_carry_a_reason(self, lang):
        """A "partial" claim must say why, or it is just a shrug.

        Go's structural interface satisfaction and Rust's blanket impls are
        genuinely irreducible (§15); saying so is different from failing
        quietly.
        """
        result = run_language(lang)
        for capability, value in result.capabilities.items():
            if value == "partial":
                assert result.capabilities.get(f"{capability}_note"), (
                    f"{lang}: {capability} is partial with no reason recorded"
                )

    def test_no_dangling_edges_in_the_corpus(self, lang):
        """The §16.1 invariant, checked per language rather than once."""
        result = run_language(lang)
        assert result.edge_kinds, f"{lang}: no edges at all"


class TestOntologyIsDerived:
    def test_relation_kinds_come_from_the_enum(self):
        """Not from a hand-maintained list that can omit what is emitted."""
        assert set(relation_kinds()) == {str(k) for k in PERSISTED_EDGES}

    def test_flows_to_is_excluded_because_it_is_never_persisted(self):
        """FLOWS_TO is a query-time closure, so claiming it would be wrong."""
        assert EdgeKind.FLOWS_TO.value not in relation_kinds()

    def test_node_kinds_come_from_the_enum(self):
        assert set(node_kinds()) == {k.value for k in NodeKind}

    def test_matrix_reports_measured_state(self):
        matrix = capability_matrix()
        assert matrix["generated"] is True, (
            "matrix.json absent; capability would be reported as untested"
        )
        for lang in LANGUAGES:
            entry = matrix["languages"][lang]
            assert entry["tier_a"] != "missing", f"{lang}: no grammar available"
            assert "capabilities" in entry

    def test_untested_is_reported_as_untested(self, tmp_path, monkeypatch):
        """Absence of evidence must be reported as absence.

        Fabricating a plausible matrix when the file is missing would reproduce
        precisely the failure this module exists to correct.
        """
        import lineagelens.ontology as ontology

        monkeypatch.setattr(ontology, "MATRIX_PATH", tmp_path / "absent.json")
        matrix = ontology.capability_matrix()
        assert matrix["generated"] is False
        for entry in matrix["languages"].values():
            assert set(entry["capabilities"].values()) == {"untested"}

    def test_guarantees_and_limitations_are_both_stated(self):
        matrix = capability_matrix()
        assert matrix["guarantees"], "a contract with no guarantees is not a contract"
        assert matrix["limitations"], (
            "a limitation the tool knows about is a boundary record; "
            "one it does not is a wrong answer"
        )


class TestInstructions:
    def test_instructions_name_the_available_languages(self):
        text = ontology_instructions()
        assert "python" in text
        assert "coverage" in text, "the envelope habit must be stated"
        assert "intent=" in text, "intent scoping must be stated"

    def test_instructions_say_when_there_is_no_index(self, tmp_path):
        text = ontology_instructions(tmp_path)
        assert "lineagelens index" in text
