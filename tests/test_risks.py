"""Resiliency risk signals (#60).

The capability the product advertises most prominently:

    [RESILIENCY RISK DETECTED]
    Thread.sleep(1000) in async
    blocking_in_async (High)

It existed in `queries.py` and went with the schema-3 core in `59eb115`.

The negative cases in this file are load-bearing. A detector that flags correct
code is worse than none, because it trains people to ignore it -- so
`await asyncio.sleep` and a synchronous `Thread.sleep` are asserted *not* to be
findings, as explicitly as the positives are asserted to be.
"""

from __future__ import annotations

import pytest

from lineagelens.indexer import Indexer
from lineagelens.query import QueryEngine
from lineagelens.query.risks import SEVERITIES, RiskDetector, list_risks


def build(tmp_path, name, files):
    root = tmp_path / name
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    store, report = Indexer(root).run()
    return QueryEngine(store, str(root)), report


PYTHON = {
    "pyproject.toml": '[project]\nname = "svc"\n',
    "svc.py": (
        "import asyncio\n"
        "import time\n\n"
        "async def correct():\n"
        "    await asyncio.sleep(1)\n"
        "    return 1\n\n"
        "async def blocking():\n"
        "    time.sleep(1)\n"
        "    return 2\n\n"
        "def synchronous():\n"
        "    time.sleep(1)\n"
        "    return 3\n\n"
        "def helper():\n"
        "    time.sleep(1)\n\n"
        "async def indirect():\n"
        "    return helper()\n"
    ),
}

JAVA = {
    "pom.xml": "<project><artifactId>svc</artifactId></project>",
    "src/Orders.java": (
        "public class Orders {\n"
        "    @Async\n"
        "    public void annotated() throws Exception {\n"
        "        Thread.sleep(1000);\n"
        "    }\n\n"
        "    public void plainSync() throws Exception {\n"
        "        Thread.sleep(1000);\n"
        "    }\n"
        "}\n"
    ),
}

# Go has no risks.toml. Used to assert `unsupported` is distinguishable from
# an empty result -- the failure the coverage envelope exists to prevent.
GO = {
    "go.mod": "module svc\n\ngo 1.22\n",
    "main.go": (
        "package main\n\n"
        'import "time"\n\n'
        "func main() {\n"
        "\ttime.Sleep(time.Second)\n"
        "}\n"
    ),
}


def calls(result):
    return {f["call"] for f in result.results}


def frames(result):
    return {f["frame"].split("#")[-1] for f in result.results}


# ---------------------------------------------------------------------------
# python
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def python_result(tmp_path_factory):
    engine, _ = build(tmp_path_factory.mktemp("py"), "svc", PYTHON)
    return list_risks(engine)


@pytest.fixture(scope="module")
def java_result(tmp_path_factory):
    engine, _ = build(tmp_path_factory.mktemp("java"), "svc", JAVA)
    return list_risks(engine)


class TestPython:
    def test_a_blocking_call_in_an_async_frame_is_found(self, python_result):
        assert "time.sleep" in calls(python_result)
        assert any("blocking" in f for f in frames(python_result))

    def test_the_correct_async_form_is_not_flagged(self, python_result):
        """`await asyncio.sleep` is the right way to do this.

        Flagging it would be worse than finding nothing, because a detector
        that cries wolf gets muted.
        """
        assert not any("correct" in f for f in frames(python_result))

    def test_a_synchronous_frame_is_not_flagged(self, python_result):
        """`time.sleep` in a plain function blocks nothing that matters."""
        assert not any("synchronous" in f for f in frames(python_result))

    def test_a_transitive_blocking_call_is_found(self, python_result):
        """Three frames down still stalls the loop."""
        indirect = [f for f in python_result.results if "indirect" in f["frame"]]
        assert indirect, "transitive finding missing"
        assert indirect[0]["distance"] >= 1
        assert "helper" in indirect[0]["chain"]

    def test_findings_are_external_references(self, python_result):
        """The most important implementation note in the issue.

        `time.sleep` is stdlib: it is never a node and never the destination
        of an edge. A detector reading only resolved edges would miss every
        finding in this file.
        """
        assert all(f["external"] for f in python_result.results)

    def test_every_finding_carries_its_chain_and_evidence(self, python_result):
        for finding in python_result.results:
            assert finding["chain"]
            assert finding["evidence"] in ("fact", "heuristic", "probabilistic")
            assert finding["at"] and ":" in finding["at"]
            assert finding["frame_at"] and ":" in finding["frame_at"]


# ---------------------------------------------------------------------------
# java -- no async keyword
# ---------------------------------------------------------------------------


class TestJava:
    def test_thread_sleep_in_an_async_annotated_method(self, java_result):
        """The claim, verbatim: Thread.sleep(1000) in async, high."""
        assert "Thread.sleep" in calls(java_result)
        finding = next(f for f in java_result.results if f["call"] == "Thread.sleep")
        assert finding["rule"] == "blocking_in_async"
        assert finding["severity"] == "high"

    def test_a_synchronous_method_is_not_flagged(self, java_result):
        """Java has no keyword, so the context comes from @Async.

        `plainSync` has the same body and no annotation, which is the only
        thing distinguishing them -- so this is the test that proves the
        marker logic works rather than everything being flagged.
        """
        assert not any("plainSync" in f for f in frames(java_result))

    def test_the_annotation_is_found_via_unresolved_refs(self, tmp_path):
        """`@Async` is external, so `node.decorators` is empty.

        Reading only that field found nothing -- the same "resolved only"
        mistake that would have missed `Thread.sleep`.
        """
        engine, _ = build(tmp_path, "svc", JAVA)
        detector = RiskDetector(engine)
        annotated = next(
            n for n in engine.store.conn.execute(
                "SELECT * FROM nodes WHERE name = 'annotated'"
            )
        )
        from lineagelens.store.db import _node_from_row

        node = _node_from_row(annotated)
        assert not node.decorators, "fixture assumption changed"
        assert "Async" in detector._annotations_of(node)


# ---------------------------------------------------------------------------
# unsupported is not empty
# ---------------------------------------------------------------------------


class TestUnsupportedIsNotEmpty:
    def test_a_language_without_a_rule_pack_says_unsupported(self, tmp_path):
        """The distinction the envelope exists for.

        Go has a real `time.Sleep` in this fixture and no rule pack. Reporting
        an empty list would say "analysed, nothing found" about code nobody
        analysed -- and "no risks" is the answer a reader most wants to
        believe.
        """
        engine, _ = build(tmp_path, "gosvc", GO)
        result = list_risks(engine)
        assert result.extra["risks"]["languages"]["go"] == "unsupported"
        assert "go" in result.extra["risks"]["unsupported"]

    def test_analysed_and_unsupported_are_different_states(self, tmp_path):
        py, _ = build(tmp_path, "py", PYTHON)
        go, _ = build(tmp_path, "go", GO)
        assert (
            list_risks(py).extra["risks"]["languages"]
            != list_risks(go).extra["risks"]["languages"]
        )

    def test_a_clean_analysed_project_is_distinguishable_from_unsupported(
        self, tmp_path
    ):
        """An empty result from an analysed language is a real answer."""
        clean, _ = build(tmp_path, "clean", {
            "pyproject.toml": '[project]\nname = "c"\n',
            "c.py": "import asyncio\n\nasync def f():\n    await asyncio.sleep(1)\n",
        })
        result = list_risks(clean)
        assert result.returned == 0
        assert result.extra["risks"]["languages"]["python"] == "analysed"

    def test_an_empty_rule_pack_is_refused_at_load(self, tmp_path):
        """A risks.toml with no rules would report "analysed, none found"."""
        from lineagelens.extract.spec import SpecError, SpecRegistry

        spec = tmp_path / "synth"
        spec.mkdir()
        (spec / "lang.toml").write_text('lang = "synth"\ndialects = ["synth"]\n')
        (spec / "nodes.scm").write_text("(function_definition) @function")
        (spec / "risks.toml").write_text("# nothing here\n")
        with pytest.raises(SpecError, match="no \\[\\[rule\\]\\]"):
            SpecRegistry(tmp_path).spec_for("synth")


# ---------------------------------------------------------------------------
# severity is derived
# ---------------------------------------------------------------------------


class TestSeverityIsDerived:
    def test_distance_lowers_severity(self, tmp_path):
        """A direct call is worse than one five hops down."""
        engine, _ = build(tmp_path, "deep", {
            "pyproject.toml": '[project]\nname = "d"\n',
            "d.py": (
                "import time\n\n"
                "def h4():\n    time.sleep(1)\n\n"
                "def h3():\n    return h4()\n\n"
                "def h2():\n    return h3()\n\n"
                "def h1():\n    return h2()\n\n"
                "async def entry():\n    return h1()\n"
            ),
        })
        result = list_risks(engine)
        assert result.results, "no finding at depth"
        finding = result.results[0]
        assert finding["distance"] > 2
        assert finding["severity"] == "medium"
        assert any("hops" in r for r in finding["severity_reasons"])

    def test_a_weak_evidence_chain_lowers_confidence_not_visibility(self):
        """Never suppressed.

        A suppressed finding makes the absence of a finding mean two different
        things, which is the one outcome worth avoiding more than a false
        positive.
        """
        from lineagelens.query.risks import _bump

        assert _bump("high", -1) == "medium"
        assert _bump("low", -1) == "low", "clamped, not dropped"

    def test_severity_never_leaves_the_ladder(self):
        for value in SEVERITIES:
            from lineagelens.query.risks import _bump

            assert _bump(value, 3) in SEVERITIES
            assert _bump(value, -3) in SEVERITIES

    def test_an_entry_point_frame_raises_severity(self):
        """A stalled entry point is a user-visible outage."""
        from lineagelens.query.risks import _bump

        assert _bump("high", 1) == "critical"

    def test_min_severity_filters(self, tmp_path):
        engine, _ = build(tmp_path, "py2", PYTHON)
        assert list_risks(engine, min_severity="critical").returned == 0
        assert list_risks(engine, min_severity="high").returned > 0


# ---------------------------------------------------------------------------
# rules are data
# ---------------------------------------------------------------------------


class TestRulesAreData:
    def test_shipped_packs_load(self):
        from lineagelens.extract.spec import SpecRegistry

        registry = SpecRegistry()
        assert set(registry.languages_with_risks()) == {
            "java", "javascript", "python", "typescript",
        }
        for lang in registry.languages_with_risks():
            for rule in registry.risk_rules(lang):
                assert rule["id"] and rule["severity"] in SEVERITIES
                assert rule["targets"], f"{lang}: rule with no targets"

    def test_java_async_markers_are_top_level(self):
        """A TOML key after [[rule]] belongs to that table.

        Placing `async_markers` below the first rule silently made it a rule
        field and left the marker list empty, so nothing was ever recognised
        as an async frame in Java.
        """
        from lineagelens.extract.spec import SpecRegistry

        assert SpecRegistry().spec_for("java").async_markers

    def test_changing_a_rule_changes_the_spec_digest(self, tmp_path):
        """A rule change must invalidate the index, like a query change."""
        from lineagelens.extract.spec import SpecRegistry

        spec = tmp_path / "synth"
        spec.mkdir()
        (spec / "lang.toml").write_text('lang = "synth"\ndialects = ["synth"]\n')
        (spec / "nodes.scm").write_text("(function_definition) @function")
        (spec / "risks.toml").write_text(
            '[[rule]]\nid = "r"\nseverity = "high"\ntargets = ["a.b"]\n'
        )
        before = SpecRegistry(tmp_path).spec_for("synth").digest()

        (spec / "risks.toml").write_text(
            '[[rule]]\nid = "r"\nseverity = "high"\ntargets = ["a.b", "c.d"]\n'
        )
        assert SpecRegistry(tmp_path).spec_for("synth").digest() != before

    def test_target_matching_is_on_trailing_segments(self):
        """So a rule author need not write every import spelling."""
        from lineagelens.query.risks import _matches

        assert _matches("java.lang.Thread.sleep", ["Thread.sleep"])
        assert _matches("Thread.sleep", ["java.lang.Thread.sleep"]) is None
        assert _matches("svc/java/Orders#run", ["Orders.run"])
        assert _matches("time.sleep", ["asyncio.sleep"]) is None


# ---------------------------------------------------------------------------
# the async flag gap this work uncovered
# ---------------------------------------------------------------------------


TYPESCRIPT = {
    "package.json": '{"name": "web"}',
    "handler.ts": (
        "import * as fs from 'fs';\n\n"
        "export async function blocking() {\n"
        "  return fs.readFileSync('/tmp/x');\n"
        "}\n\n"
        "export async function correct() {\n"
        "  return await fs.promises.readFile('/tmp/x');\n"
        "}\n\n"
        "export function synchronous() {\n"
        "  return fs.readFileSync('/tmp/x');\n"
        "}\n"
    ),
}


class TestAsyncFlagForTypeScript:
    """#60 step 3 asked whether `NodeFlags.ASYNC` is set for TypeScript.

    It was not. `python/nodes.scm` captured `"async" @modifier` and the
    TypeScript and JavaScript specs did not, so the flag was populated only for
    Python. Every TypeScript async frame was invisible to this detector, which
    would have reported "no risks" over code whose async-ness it could not see
    -- a confident empty answer, which is the failure mode this whole subsystem
    exists to avoid.
    """

    def test_async_functions_carry_the_flag(self, tmp_path):
        from lineagelens.core import NodeFlags

        engine, _ = build(tmp_path, "web", TYPESCRIPT)
        flagged = {
            n.name for n in engine.store.nodes_with_flag(NodeFlags.ASYNC)
        }
        assert flagged == {"blocking", "correct"}
        assert "synchronous" not in flagged

    def test_a_blocking_sync_api_in_an_async_frame_is_found(self, tmp_path):
        engine, _ = build(tmp_path, "web", TYPESCRIPT)
        result = list_risks(engine)
        assert "fs.readFileSync" in calls(result)
        assert frames(result) == {"blocking"}

    def test_the_promise_form_is_not_flagged(self, tmp_path):
        engine, _ = build(tmp_path, "web", TYPESCRIPT)
        assert not any("correct" in f for f in frames(list_risks(engine)))

    def test_every_language_with_a_rule_pack_can_see_its_async_frames(self):
        """The invariant the gap violated.

        A rule pack without a way to recognise an async frame in its language
        produces a permanently empty result that reads as "analysed, clean".
        Either the language sets ASYNC or it declares markers.
        """
        from lineagelens.extract.spec import SpecRegistry

        registry = SpecRegistry()
        for lang in registry.languages_with_risks():
            spec = registry.spec_for(lang)
            has_keyword = '"async" @modifier' in spec.sources.get("nodes", "")
            assert has_keyword or spec.async_markers, (
                f"{lang} has rules requiring an async context but no way to "
                f"detect one: no async modifier capture and no async_markers"
            )
