"""SCIP ingestion as a Tier B oracle (#47).

The Tier 2 half of the two-tier claim. `ToolchainOracle` detects `javac` and
answers nothing; this is the first oracle other than jedi that actually
resolves, and the first that is compiler-verified.

An *oracle*, not a merger. The original design built a parallel graph from SCIP
and reconciled two graphs by (file, line, col); implementing the existing
protocol instead inherits evidence tiers, the availability matrix,
`--require-tier-b` semantics and `unresolved_refs` as a ready-made work list.

Two properties carry the design and are tested hardest:

* **the index conversion is right.** SCIP is 0-indexed on lines, `Span` is
  1-indexed. An off-by-one here resolves nothing rather than failing, which is
  why it is asserted directly rather than inferred from a passing lookup;
* **staleness is refused, per file.** An index built against different source
  must contribute nothing, or SCIP reintroduces exactly what #51 eliminated:
  confident edges that do not match the code on disk.
"""

from __future__ import annotations

import hashlib

import pytest
from scip_fixtures import document, index, occurrence

from lineagelens.core import RefKind, Span, UnresolvedRef
from lineagelens.resolve.oracles import ScipOracle, default_oracles
from lineagelens.resolve.scip import DEFINITION_ROLE, ScipParseError, read_index

JAVA = (
    "public class Orders {\n"
    "    public String handle(String in) {\n"
    "        return format(in);\n"
    "    }\n"
    "    public String format(String s) {\n"
    "        return s;\n"
    "    }\n"
    "}\n"
)


def write_index(tmp_path, documents, **kwargs):
    path = tmp_path / "index.scip"
    path.write_bytes(index(documents, **kwargs))
    return path


def hash_of(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def a_ref(file_path: str, line: int, col: int) -> UnresolvedRef:
    return UnresolvedRef(
        from_node="n", ref_text="x", ref_kind=RefKind.CALL,
        span=Span(start_byte=0, end_byte=1, start_line=line, start_col=col,
                  end_line=line, end_col=col + 1),
        file_path=file_path,
    )


# ---------------------------------------------------------------------------
# the reader
# ---------------------------------------------------------------------------


class TestReader:
    def test_parses_metadata_documents_and_occurrences(self, tmp_path):
        path = write_index(tmp_path, [document("src/A.java", [
            occurrence("sym/one.", 0, 4, 8, roles=DEFINITION_ROLE),
            occurrence("sym/two.", 2, 1, 5),
        ])])
        parsed = read_index(path)
        assert parsed.tool == "scip-java"
        assert parsed.project_root == "file:///repo"
        assert len(parsed.documents) == 1
        assert parsed.occurrence_count() == 2
        assert parsed.documents[0].language == "java"

    def test_line_conversion_is_zero_to_one_indexed(self, tmp_path):
        """Asserted directly, not inferred from a lookup that happened to work.

        SCIP lines are 0-indexed; `Span.start_line` is 1-indexed. An
        off-by-one resolves nothing rather than failing loudly, so it would be
        indistinguishable from an index that simply covers nothing.
        """
        path = write_index(tmp_path, [document("a.py", [
            occurrence("sym.", 0, 3, 7),
        ])])
        occ = read_index(path).documents[0].occurrences[0]
        assert occ.line == 1, "SCIP line 0 must become Span line 1"
        # Columns are 0-indexed in both and must NOT be shifted.
        assert occ.column == 3
        assert occ.end_column == 7

    def test_definition_role_is_read(self, tmp_path):
        path = write_index(tmp_path, [document("a.py", [
            occurrence("def.", 0, 0, 1, roles=DEFINITION_ROLE),
            occurrence("ref.", 1, 0, 1),
        ])])
        definition, reference = read_index(path).documents[0].occurrences
        assert definition.is_definition is True
        assert reference.is_definition is False

    def test_a_four_element_range_spans_lines(self, tmp_path):
        """[startLine, startChar, endLine, endChar]."""
        from scip_fixtures import lenf, varint

        body = lenf(1, varint(0) + varint(2) + varint(4) + varint(9))
        body += lenf(2, b"multi.")
        path = write_index(tmp_path, [document("a.py", [body])])
        occ = read_index(path).documents[0].occurrences[0]
        assert (occ.line, occ.end_line) == (1, 5)
        assert (occ.column, occ.end_column) == (2, 9)

    def test_an_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "index.scip"
        path.write_bytes(b"")
        with pytest.raises(ScipParseError, match="empty"):
            read_index(path)

    def test_an_index_with_no_documents_is_refused(self, tmp_path):
        """A successful parse of nothing looks identical to a failed parse.

        Refusing it is what stops "SCIP resolved nothing" being reported as
        "SCIP found nothing to resolve".
        """
        path = write_index(tmp_path, [])
        with pytest.raises(ScipParseError, match="no documents"):
            read_index(path)

    def test_a_truncated_buffer_raises(self, tmp_path):
        path = tmp_path / "index.scip"
        path.write_bytes(bytes([0x0A, 0x7F]))  # claims 127 bytes, supplies none
        with pytest.raises(ScipParseError):
            read_index(path)

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(ScipParseError, match="cannot read"):
            read_index(tmp_path / "absent.scip")


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


class TestResolution:
    @pytest.fixture
    def oracle(self, tmp_path):
        path = write_index(tmp_path, [document("src/Orders.java", [
            # `format` defined on SCIP line 4 == Span line 5
            occurrence("sem . . Orders#format().", 4, 18, 24,
                       roles=DEFINITION_ROLE),
            # ...referenced on SCIP line 2 == Span line 3
            occurrence("sem . . Orders#format().", 2, 15, 21),
            # A reference SCIP saw but never defined: an external dependency.
            occurrence("sem jdk . java/lang/Thread#sleep().", 5, 8, 13),
        ], text=JAVA)])
        return ScipOracle(project_root=tmp_path, index_path=path,
                          languages=frozenset({"java"}))

    def test_a_reference_resolves_to_its_definition_site(self, oracle):
        target = oracle.resolve(a_ref("src/Orders.java", 3, 15))
        assert target is not None
        assert target.qualified_name == "src/Orders.java:5"
        assert target.evidence.label == "scip_resolve"
        assert target.resolution.value == "exact"
        assert target.is_external is False

    def test_evidence_is_a_fact_not_a_heuristic(self, oracle):
        """The whole point of Tier 2.

        A compiler-verified answer is a different kind of claim from a
        name-match inference, and the tier is what a reader acts on.
        """
        target = oracle.resolve(a_ref("src/Orders.java", 3, 15))
        assert target.evidence.tier.value == "fact"

    def test_a_reference_with_no_definition_is_external_not_unknown(self, oracle):
        """"Definitely external" and "unknown" are different answers.

        Reporting nothing would lose information SCIP actually has.
        """
        target = oracle.resolve(a_ref("src/Orders.java", 6, 8))
        assert target is not None
        assert target.is_external is True
        assert target.evidence.label == "scip_external"
        assert "Thread#sleep" in target.qualified_name

    def test_an_unknown_position_resolves_to_nothing(self, oracle):
        assert oracle.resolve(a_ref("src/Orders.java", 99, 0)) is None

    def test_an_unknown_file_resolves_to_nothing(self, oracle):
        assert oracle.resolve(a_ref("src/Other.java", 3, 15)) is None

    def test_type_of_returns_the_scip_symbol(self, oracle):
        """Covers the structural-type and overload cases Tier A cannot."""
        span = Span(start_byte=0, end_byte=1, start_line=5, start_col=18,
                    end_line=5, end_col=24)
        assert oracle.type_of(span, "src/Orders.java") == "sem . . Orders#format()."

    def test_status_reports_what_the_index_covers(self, oracle):
        status = oracle.status()
        assert status["tool"] == "scip-java"
        assert status["occurrences"] == 3
        assert status["definitions"] == 1
        assert status["error"] == ""


# ---------------------------------------------------------------------------
# staleness
# ---------------------------------------------------------------------------


class TestStalenessIsRefused:
    def _oracle(self, tmp_path, *, indexed_text, disk_hash):
        path = write_index(tmp_path, [document("A.java", [
            occurrence("sem . . A#g().", 0, 22, 23, roles=DEFINITION_ROLE),
        ], text=indexed_text)])
        return ScipOracle(
            project_root=tmp_path, index_path=path,
            languages=frozenset({"java"}),
            content_hashes={"A.java": disk_hash},
        )

    def test_a_matching_hash_is_used(self, tmp_path):
        text = "public class A { void g() {} }\n"
        oracle = self._oracle(tmp_path, indexed_text=text,
                              disk_hash=hash_of(text))
        assert oracle.status()["stale_files"] == []
        assert oracle.resolve(a_ref("A.java", 1, 22)) is not None

    def test_a_differing_hash_is_refused(self, tmp_path):
        """The gate that stops SCIP reintroducing what #51 eliminated.

        An index built against different source would otherwise produce
        confident edges that do not correspond to the code on disk.
        """
        oracle = self._oracle(
            tmp_path,
            indexed_text="public class A { void g() {} }\n",
            disk_hash=hash_of("public class A { void f() {} }\n"),
        )
        assert oracle.status()["stale_files"] == ["A.java"]
        assert oracle.resolve(a_ref("A.java", 1, 22)) is None
        assert oracle.type_of(
            Span(start_byte=0, end_byte=1, start_line=1, start_col=22,
                 end_line=1, end_col=23), "A.java") is None

    def test_an_entirely_stale_index_reports_unavailable(self, tmp_path):
        """So `--require-tier-b` refuses rather than silently degrading (#65)."""
        oracle = self._oracle(
            tmp_path,
            indexed_text="old\n",
            disk_hash=hash_of("new\n"),
        )
        assert oracle.available() == "missing"
        assert oracle.can_resolve() is False

    def test_an_index_without_text_is_trusted(self, tmp_path):
        """Unverifiable is not the same as stale.

        Some indexers omit document text. Refusing everything unverifiable
        would make SCIP useless against them, and an absent hash is not
        evidence of a mismatch.
        """
        path = write_index(tmp_path, [document("A.java", [
            occurrence("sem . . A#g().", 0, 0, 1, roles=DEFINITION_ROLE),
        ])])
        oracle = ScipOracle(project_root=tmp_path, index_path=path,
                            languages=frozenset({"java"}),
                            content_hashes={"A.java": hash_of("anything\n")})
        assert oracle.status()["stale_files"] == []


# ---------------------------------------------------------------------------
# degradation and ordering
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_an_unusable_index_degrades_to_tier_a_rather_than_failing(self, tmp_path):
        """A bad SCIP index must not fail the build -- but must say so."""
        path = tmp_path / "index.scip"
        path.write_bytes(b"not protobuf at all, really")
        oracle = ScipOracle(project_root=tmp_path, index_path=path,
                            languages=frozenset({"java"}))
        assert oracle.available() == "missing"
        assert oracle.status()["error"]
        assert oracle.resolve(a_ref("A.java", 1, 0)) is None

    def test_no_index_path_is_simply_unavailable(self, tmp_path):
        oracle = ScipOracle(project_root=tmp_path, languages=frozenset({"java"}))
        assert oracle.available() == "missing"
        assert oracle.can_resolve() is False


class TestOracleOrder:
    def test_scip_is_absent_without_an_index(self, tmp_path):
        assert [o.name for o in default_oracles(tmp_path)][0] == "jedi"

    def test_scip_outranks_jedi_when_present(self, tmp_path):
        """A compiler-verified fact outranks a static-analysis inference.

        `OracleRegistry.resolve` takes the first answer, so order *is* the
        precedence rule. This is the one place #47 changes existing behaviour.
        """
        names = [o.name for o in default_oracles(tmp_path, tmp_path / "i.scip")]
        assert names[0] == "scip"
        assert names.index("scip") < names.index("jedi")


# ---------------------------------------------------------------------------
# the indexer and the CLI
# ---------------------------------------------------------------------------


class TestIndexerIntegration:
    def _project(self, tmp_path):
        root = tmp_path / "svc"
        (root / "src").mkdir(parents=True)
        (root / "pom.xml").write_text("<project><artifactId>svc</artifactId></project>")
        (root / "src" / "Orders.java").write_text(JAVA)
        return root

    def test_the_report_records_what_scip_contributed(self, tmp_path):
        from lineagelens.indexer import Indexer

        root = self._project(tmp_path)
        path = write_index(root, [document("src/Orders.java", [
            occurrence("sem . . Orders#format().", 4, 18, 24,
                       roles=DEFINITION_ROLE),
            occurrence("sem . . Orders#format().", 2, 15, 21),
        ], text=JAVA)])

        store, report = Indexer(root, scip_index=path).run()
        try:
            assert report.scip["tool"] == "scip-java"
            assert report.scip["occurrences"] == 2
            assert report.scip["stale_files"] == []
            assert "scip" in report.as_dict()
        finally:
            store.close()

    def test_indexing_without_scip_is_unchanged(self, tmp_path):
        """SCIP is opt-in; its absence must change nothing."""
        from lineagelens.indexer import Indexer

        root = self._project(tmp_path)
        store, report = Indexer(root).run()
        try:
            assert report.scip == {}
            assert report.nodes > 0
        finally:
            store.close()

    def test_auto_discovery_requires_the_flag_and_a_file(self, tmp_path):
        """Never implicit.

        Picking up a stray index.scip would change resolution silently, and
        resolution is the thing a user most needs to reason about.
        """
        from lineagelens.cli import _resolve_scip_path

        root = self._project(tmp_path)

        class Args:
            path = root
            scip = None

        assert _resolve_scip_path(Args()) is None

        Args.scip = "auto"
        with pytest.raises(SystemExit, match="scip-java index"):
            _resolve_scip_path(Args())

        write_index(root, [document("src/Orders.java", [
            occurrence("s.", 0, 0, 1, roles=DEFINITION_ROLE),
        ])])
        assert _resolve_scip_path(Args()).name == "index.scip"

    def test_an_explicit_missing_path_is_an_error(self, tmp_path):
        from lineagelens.cli import _resolve_scip_path

        class Args:
            path = tmp_path
            scip = str(tmp_path / "nope.scip")

        with pytest.raises(SystemExit, match="is not a file"):
            _resolve_scip_path(Args())

    def test_nothing_is_subprocessed(self):
        """LineageLens never runs scip-java or scip-typescript.

        Running a build tool inside `index` would make indexing
        network-dependent, slow and non-deterministic, and
        `verify --determinism` could not hold.

        Checked against the **AST**, not the text. A text scan matched the
        docstrings explaining this very decision -- the same false positive
        that would have made `upload.assert_no_source` and the telemetry guard
        unusable, and for the same reason: a check that cannot tell code from
        prose gets switched off.
        """
        import ast
        from pathlib import Path

        import lineagelens.resolve.scip.reader as reader
        from lineagelens.resolve import oracles

        forbidden = {"subprocess", "os.system", "popen", "pty", "commands"}
        for module in (reader, oracles):
            tree = ast.parse(Path(module.__file__).read_text())
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            assert not (imported & forbidden), (
                f"{module.__name__} imports {imported & forbidden}"
            )
