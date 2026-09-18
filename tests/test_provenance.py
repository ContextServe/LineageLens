"""Index provenance and the digests that gate staleness (#66).

Three `graph_meta` columns were declared and never written: `commit_sha`,
`ontology_digest` and `deterministic_ok`. The middle one was the dangerous
case. `verify` reads it, and an empty value compared against an empty value
passes, so a change to the node or edge taxonomy left every existing index
looking current. A column that is present and blank is worse than one that is
absent, because the check that depends on it appears to run.
"""

from __future__ import annotations

import subprocess

import pytest

from lineagelens import provenance
from lineagelens.core import SCHEMA_VERSION
from lineagelens.indexer import Indexer
from lineagelens.ontology import ontology_digest

PROJECT = {
    "pyproject.toml": '[project]\nname = "prov"\n',
    "app/thing.py": "def handle(payload):\n    return payload\n",
}


def write_project(root):
    for rel, text in PROJECT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    """A real git repository with one commit."""
    root = write_project(tmp_path / "repo")
    try:
        git(root, "init", "-q")
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("git unavailable")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


# ---------------------------------------------------------------------------
# git identity
# ---------------------------------------------------------------------------


class TestGitProvenance:
    def test_commit_branch_and_clean_tree(self, repo):
        store, _ = Indexer(repo).run()
        got = store.provenance()
        expected = git(repo, "rev-parse", "HEAD").stdout.strip()
        assert got["commit_sha"] == expected
        assert got["branch"] == git(
            repo, "rev-parse", "--abbrev-ref", "HEAD"
        ).stdout.strip()
        assert got["dirty"] is False

    def test_dirty_is_reported(self, repo):
        """`dirty` is what makes `commit_sha` honest.

        A graph built from a modified tree is not the graph of that commit, and
        a consumer told the commit but not the modification will trust it.
        """
        (repo / "app" / "thing.py").write_text("def handle(p):\n    return None\n")
        store, _ = Indexer(repo).run()
        assert store.provenance()["dirty"] is True

    def test_detached_head_has_a_commit_but_no_branch(self, repo):
        """Reporting the literal string "HEAD" as a branch would be worse."""
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "-q", "--detach", sha)
        store, _ = Indexer(repo).run()
        got = store.provenance()
        assert got["commit_sha"] == sha
        assert got["branch"] is None


class TestGitIsNotADependency:
    def test_indexing_a_non_repository_succeeds(self, tmp_path):
        """Indexing an unpacked tarball is a normal thing to do."""
        root = write_project(tmp_path / "plain")
        store, report = Indexer(root).run()
        assert report.nodes > 0
        assert store.provenance() == {
            "commit_sha": None, "branch": None, "dirty": None,
        }

    def test_a_missing_git_binary_is_not_an_error(self, repo, monkeypatch):
        """Emptying PATH is the closest honest simulation of no git."""
        monkeypatch.setenv("PATH", "")
        assert provenance.resolve(repo) == provenance.Provenance()

    def test_resolve_never_raises(self, tmp_path):
        for candidate in (tmp_path, tmp_path / "does-not-exist"):
            assert isinstance(provenance.resolve(candidate), provenance.Provenance)

    def test_dirty_stays_none_when_undeterminable(self):
        """Tri-state on purpose.

        `None` means "could not determine". Collapsing it to `False` would
        report an unknown tree as clean, which is the misplaced trust this
        field exists to prevent.
        """
        assert provenance.Provenance().dirty is None
        assert provenance.Provenance(commit_sha="abc").dirty is None


# ---------------------------------------------------------------------------
# provenance must not reach build_digest
# ---------------------------------------------------------------------------


class TestDeterminismSurvives:
    def test_a_new_commit_does_not_change_build_digest(self, repo):
        """The property that forced provenance outside the digest.

        `build_digest` asserts "same source, same graph". Folding the commit in
        would make `verify --determinism` fail between any two commits with
        identical content, which destroys the guarantee it exists to provide.
        """
        _, first = Indexer(repo).run()

        git(repo, "commit", "-q", "--allow-empty", "-m", "no source change")
        store, second = Indexer(repo).run()

        assert second.build_digest == first.build_digest
        # ...while the recorded commit genuinely moved.
        assert store.provenance()["commit_sha"] == git(
            repo, "rev-parse", "HEAD"
        ).stdout.strip()

    def test_dirty_state_does_not_change_build_digest_by_itself(self, repo):
        """Only source content may move the digest."""
        _, clean = Indexer(repo).run()
        (repo / "notes.txt").write_text("not source\n")
        _, dirty = Indexer(repo).run()
        assert dirty.build_digest == clean.build_digest


# ---------------------------------------------------------------------------
# ontology digest
# ---------------------------------------------------------------------------


class TestOntologyDigest:
    def test_written_and_non_empty(self, tmp_path):
        store, _ = Indexer(write_project(tmp_path / "p")).run()
        stored = store.conn.execute(
            "SELECT ontology_digest FROM graph_meta WHERE id = 1"
        ).fetchone()[0]
        assert stored, "ontology_digest is still empty"
        assert stored == ontology_digest()

    def test_stable_across_calls(self):
        assert ontology_digest() == ontology_digest()

    def test_moves_when_the_taxonomy_changes(self, monkeypatch):
        """A digest nothing can be shown to change is a constant.

        Asserted by adding an edge kind to the set the digest is built from,
        which is the change it is meant to detect.
        """
        from lineagelens import ontology

        before = ontology_digest()
        original = ontology.relation_kinds()
        monkeypatch.setattr(
            ontology, "relation_kinds",
            lambda: [*original, "INVENTED_FOR_THIS_TEST"],
        )
        assert ontology.ontology_digest() != before

    def test_tracks_schema_version(self, monkeypatch):
        from lineagelens import ontology

        before = ontology_digest()
        monkeypatch.setattr(ontology, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
        assert ontology.ontology_digest() != before


# ---------------------------------------------------------------------------
# deterministic_ok
# ---------------------------------------------------------------------------


class TestDeterministicOk:
    def test_unverified_is_null_not_zero(self, tmp_path):
        """Three states, not two.

        "Not checked" and "checked and failed" are different facts and only one
        of them is a defect.
        """
        store, _ = Indexer(write_project(tmp_path / "p")).run()
        value = store.conn.execute(
            "SELECT deterministic_ok FROM graph_meta WHERE id = 1"
        ).fetchone()[0]
        assert value is None
        assert value is not False and value != 0

    @pytest.mark.parametrize("verdict", [True, False])
    def test_recorded_verdict(self, tmp_path, verdict):
        store, _ = Indexer(write_project(tmp_path / "p")).run()
        store.record_determinism(verdict)
        assert store.conn.execute(
            "SELECT deterministic_ok FROM graph_meta WHERE id = 1"
        ).fetchone()[0] == int(verdict)


# ---------------------------------------------------------------------------
# unclaimed frameworks (#69)
# ---------------------------------------------------------------------------


UNMODELLED = {
    "pyproject.toml": '[project]\nname = "exotic"\n',
    # A decorator carrying a route-shaped literal, from a framework no adapter
    # claims. This is the §8.2 case: a contract exists in the code and
    # LineageLens cannot model it.
    "srv.py": (
        "from unknownframework import handler\n\n"
        "@handler('/v9/widgets')\n"
        "def widgets():\n"
        "    return []\n"
    ),
}

NO_FRAMEWORK = {
    "pyproject.toml": '[project]\nname = "plain"\n',
    "a.py": "def add(x, y):\n    return x + y\n",
}


def index_of(tmp_path, name, files):
    from lineagelens.query import QueryEngine

    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    store, report = Indexer(root).run()
    return QueryEngine(store, str(root)), report


class TestUnclaimedFrameworks:
    """§8.2: an empty contract map has two causes and they must be told apart.

    `_note_unclaimed` read `graph_meta.ontology_digest` expecting JSON with an
    `"unclaimed"` key. Nothing ever wrote that, and the column was empty, so
    the `if` was false and the branch never ran. The feature was inert from the
    day it was written, and the blank column made the reader a no-op rather
    than an error -- so there was no failure to investigate.
    """

    def test_persisted_and_matches_the_index_report(self, tmp_path):
        engine, report = index_of(tmp_path, "exotic", UNMODELLED)
        assert report.unclaimed_frameworks, "fixture produced no unclaimed frameworks"
        assert engine.store.unclaimed_frameworks() == report.unclaimed_frameworks

    def test_reaches_the_coverage_envelope(self, tmp_path):
        """The assertion the old code could not satisfy at any input."""
        engine, _ = index_of(tmp_path, "exotic", UNMODELLED)
        envelope = engine.contract_map().envelope.as_dict()
        assert envelope["unclaimed_frameworks"]
        assert envelope["unclaimed_frameworks"][0]["name"] == "handler"

    def test_the_two_empty_answers_are_distinguishable(self, tmp_path):
        """The whole point of §8.2.

        Both projects return no contracts. One is genuinely unconnected; the
        other is connected through a framework we cannot model. If those two
        responses were equal, an agent could not tell "nothing downstream" from
        "we cannot see downstream" -- and it would believe the first.
        """
        unmodelled, _ = index_of(tmp_path, "exotic2", UNMODELLED)
        plain, _ = index_of(tmp_path, "plain2", NO_FRAMEWORK)

        a, b = unmodelled.contract_map(), plain.contract_map()
        assert not a.results and not b.results, "fixtures should map no contracts"
        assert a.envelope.as_dict() != b.envelope.as_dict()
        assert a.envelope.unclaimed_frameworks
        assert not b.envelope.unclaimed_frameworks

    def test_ontology_digest_is_only_ever_a_digest(self, tmp_path):
        """It was doubling as a JSON payload column. Never again."""
        engine, _ = index_of(tmp_path, "digest", UNMODELLED)
        stored = engine.store.conn.execute(
            "SELECT ontology_digest FROM graph_meta WHERE id = 1"
        ).fetchone()[0]
        int(stored, 16)  # raises unless it is a plain hex digest
        assert not stored.startswith(("{", "["))

    def test_malformed_content_does_not_raise(self, tmp_path):
        """A query must not fail because a summary field is unreadable."""
        engine, _ = index_of(tmp_path, "broken", UNMODELLED)
        engine.store.conn.execute(
            "UPDATE graph_meta SET unclaimed_frameworks = 'not json' WHERE id = 1"
        )
        engine.store.conn.commit()
        assert engine.store.unclaimed_frameworks() == []
        assert engine.contract_map().envelope.unclaimed_frameworks == []
