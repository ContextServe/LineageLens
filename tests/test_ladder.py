"""The capability ladder (#56).

Language support used to be a binary, which is the wrong shape in both
directions. It could not express "we can inventory Kotlin symbols but not trace
its data flow" -- a genuinely useful state, and the one most breadth-oriented
tooling is actually in -- so the only options were to claim a language fully or
not at all.

Two properties carry the whole design, and both are asserted here:

* **the level is derived, never declared.** A declared level drifts the moment
  someone adds a grammar without a spec, which is the exact failure the ladder
  exists to surface;
* **a claimed level is falsifiable.** The conformance run cross-checks it
  against the edge kinds a corpus actually produced, so a `refs.scm` that does
  not match its grammar cannot advertise L1.
"""

from __future__ import annotations

import pytest

from lineagelens.conformance.runner import _level_shortfall
from lineagelens.extract.spec import (
    LEVEL_EVIDENCE,
    LEVELS,
    SpecRegistry,
)

NODES = '(function_definition name: (identifier) @name) @function'
REFS = '(call function: (identifier) @callee) @call'
DATAFLOW = '(assignment left: (identifier) @target) @write'


def spec_dir(root, lang, files):
    """A synthetic spec directory containing exactly ``files``."""
    d = root / lang
    d.mkdir(parents=True)
    (d / "lang.toml").write_text(f'lang = "{lang}"\ndialects = ["{lang}"]\n')
    for name, text in files.items():
        (d / f"{name}.scm").write_text(text)
    return d


# ---------------------------------------------------------------------------
# derivation
# ---------------------------------------------------------------------------


class TestLevelIsDerived:
    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"nodes": NODES}, "L0"),
            ({"nodes": NODES, "refs": REFS}, "L1"),
            ({"nodes": NODES, "refs": REFS, "dataflow": DATAFLOW}, "L2"),
        ],
    )
    def test_all_three_levels(self, tmp_path, files, expected):
        spec_dir(tmp_path, "synth", files)
        assert SpecRegistry(tmp_path).spec_for("synth").level == expected

    def test_a_placeholder_spec_does_not_promote(self, tmp_path):
        """A comment-only `refs.scm` is not a capability.

        Checking for the key alone would let an empty placeholder promote a
        language to L1 while extracting nothing -- a declared level wearing a
        measured level's clothes.
        """
        spec_dir(tmp_path, "synth", {
            "nodes": NODES,
            "refs": "; TODO: write these queries\n;\n",
        })
        assert SpecRegistry(tmp_path).spec_for("synth").level == "L0"

    def test_dataflow_without_refs_still_reports_l2(self, tmp_path):
        """Derivation reports what loaded; the conformance run judges it.

        Deliberately split: `level` is a fact about files on disk, and whether
        those files work is measured against a corpus. Conflating them would
        put a corpus run inside spec loading.
        """
        spec_dir(tmp_path, "synth", {"nodes": NODES, "dataflow": DATAFLOW})
        assert SpecRegistry(tmp_path).spec_for("synth").level == "L2"
        # ...and the cross-check is what refuses it.
        assert _level_shortfall("L2", {"READS"})

    def test_there_is_no_level_field_to_declare(self, tmp_path):
        """Setting one in the manifest must not change anything."""
        d = spec_dir(tmp_path, "synth", {"nodes": NODES})
        (d / "lang.toml").write_text(
            'lang = "synth"\ndialects = ["synth"]\nlevel = "L2"\n'
        )
        assert SpecRegistry(tmp_path).spec_for("synth").level == "L0"

    def test_levels_are_ordered_floor_first(self):
        assert LEVELS == ("L0", "L1", "L2")

    def test_every_shipped_language_is_l2(self):
        """All seven stay at L2; the ladder adds no regression."""
        levels = SpecRegistry().levels()
        assert set(levels) == {
            "csharp", "go", "java", "javascript", "python", "rust", "typescript",
        }
        assert set(levels.values()) == {"L2"}


# ---------------------------------------------------------------------------
# the cross-check that makes a level falsifiable
# ---------------------------------------------------------------------------


class TestLevelIsFalsifiable:
    def test_l0_needs_no_edges(self):
        assert _level_shortfall("L0", set()) == ""

    def test_l1_needs_a_call_or_import(self):
        assert _level_shortfall("L1", {"CALLS"}) == ""
        assert _level_shortfall("L1", {"IMPORTS"}) == ""
        assert "required for L1" in _level_shortfall("L1", {"CONTAINS"})

    def test_l2_needs_a_dataflow_edge(self):
        assert _level_shortfall("L2", {"CALLS", "READS"}) == ""
        assert "required for L2" in _level_shortfall("L2", {"CALLS"})

    def test_the_check_is_cumulative(self):
        """L2 must satisfy L1 too.

        Data-flow queries over broken refs queries are two levels of spec with
        one of them broken, not one level up.
        """
        message = _level_shortfall("L2", {"READS"})
        assert "required for L1" in message

    def test_evidence_kinds_are_real_edge_kinds(self):
        """A typo here would silently make a level unfalsifiable."""
        from lineagelens.core import EdgeKind

        known = {k.value for k in EdgeKind}
        for kinds in LEVEL_EVIDENCE.values():
            assert set(kinds) <= known

    def test_the_shipped_matrix_corroborates_every_claim(self):
        """The committed artifact must not contain an unsupported claim."""
        import json
        from pathlib import Path

        import lineagelens.conformance as conformance

        matrix = json.loads(
            (Path(conformance.__file__).parent / "matrix.json").read_text()
        )
        offenders = {
            lang: entry["level_unsupported"]
            for lang, entry in matrix["languages"].items()
            if entry.get("level_unsupported")
        }
        assert not offenders, offenders


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    from lineagelens.indexer import Indexer

    root = tmp_path_factory.mktemp("lad")
    (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
    (root / "app.py").write_text(
        "def inner(x):\n    return x\n\ndef outer():\n    return inner(1)\n"
    )
    store, report = Indexer(root).run()
    return store, report, root


class TestLevelIsReported:
    def test_index_report(self, indexed):
        _, report, _ = indexed
        assert report.levels == {"python": "L2"}
        assert report.as_dict()["levels"] == {"python": "L2"}

    def test_query_envelope(self, indexed):
        """The most important consumer.

        `Envelope` exists so a negative answer can be told apart from an
        uncovered one, and a level is that distinction at language granularity.
        """
        from lineagelens.query import QueryEngine

        store, _, root = indexed
        engine = QueryEngine(store, str(root))
        envelope = engine.search("inner").envelope
        assert envelope.levels == {"python": "L2"}
        assert envelope.as_dict()["levels"] == {"python": "L2"}

    def test_a_sub_l2_language_makes_the_envelope_incomplete(self):
        """Reporting `complete` over an inventory-only language would assert
        exactly the thing the ladder exists to deny."""
        from lineagelens.query import Envelope

        assert Envelope(levels={"python": "L2"}).complete is True
        assert Envelope(levels={"kotlin": "L0"}).complete is False
        assert Envelope(levels={"python": "L2", "kotlin": "L1"}).complete is False

    def test_capability_matrix(self):
        from lineagelens.ontology import capability_matrix

        languages = capability_matrix()["languages"]
        assert languages["python"]["level"] == "L2"
        assert "flow" in languages["python"]["level_means"]

    def test_ontology_digest_tracks_levels(self, monkeypatch):
        """A level change must invalidate an index (#56 criterion 5)."""
        from lineagelens import ontology

        before = ontology.ontology_digest()
        monkeypatch.setattr(
            ontology.SpecRegistry if hasattr(ontology, "SpecRegistry") else SpecRegistry,
            "levels",
            lambda self: {"python": "L1"},
        )
        assert ontology.ontology_digest() != before


# ---------------------------------------------------------------------------
# --require-level
# ---------------------------------------------------------------------------


class TestRequireLevel:
    def test_parses_a_bare_level(self):
        from lineagelens.cli import _parse_level_floors

        assert _parse_level_floors("L2") == {"*": "L2"}

    def test_parses_per_language_floors(self):
        from lineagelens.cli import _parse_level_floors

        assert _parse_level_floors("kotlin:L0,java:L2") == {
            "kotlin": "L0", "java": "L2",
        }

    def test_rejects_a_level_that_does_not_exist(self):
        from lineagelens.cli import _parse_level_floors

        with pytest.raises(SystemExit, match="not a level"):
            _parse_level_floors("L9")

    def test_a_satisfied_floor_exits_zero(self, tmp_path):
        from lineagelens.cli import main

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        assert main(["index", str(root), "--require-level=L2"]) == 0

    def test_a_language_below_the_floor_is_skipped(self, tmp_path, monkeypatch):
        """Recorded as a data fact on the file row, not a log line.

        Patched rather than fixtured: every shipped language is L2, so there is
        no natural case, and a test that cannot run is worse than none.
        """
        from lineagelens.core import SkipReason
        from lineagelens.indexer import Indexer

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")

        monkeypatch.setattr(SpecRegistry, "levels", lambda self: {"python": "L0"})
        monkeypatch.setattr(
            SpecRegistry, "spec_for",
            lambda self, lang: type("S", (), {"level": "L0"})(),
        )
        store, report = Indexer(root, require_level={"*": "L2"}).run()
        try:
            assert report.level_refused == {"python": "L0"}
            skipped = [
                f for f in store.files()
                if f.skip_reason == SkipReason.BELOW_REQUIRED_LEVEL
            ]
            assert skipped, "no file skipped despite the floor"
        finally:
            store.close()

    def test_no_floor_skips_nothing(self, tmp_path):
        from lineagelens.indexer import Indexer

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        store, report = Indexer(root).run()
        try:
            assert report.level_refused == {}
            assert report.files_parsed >= 1
        finally:
            store.close()


class TestDocumentation:
    def test_adding_a_language_exists_and_names_every_artifact(self):
        """The prerequisite for the grammar-wave work being contributable."""
        from pathlib import Path

        text = Path("docs/ADDING-A-LANGUAGE.md").read_text()
        for artifact in ("lang.toml", "nodes.scm", "refs.scm", "dataflow.scm",
                         "corpus", "pyproject.toml", "--require-level"):
            assert artifact in text, f"{artifact} undocumented"
        # The rule the whole process exists to enforce.
        assert "worse than no support at all" in text
