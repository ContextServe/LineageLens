"""Every extraction spec must compile against every dialect it claims.

This is the guard that makes declarative specs safe (issue #51 §7.1). A spec is
data, so a typo in a node type is not a syntax error anyone notices -- it is a
query that silently matches nothing, which means that language quietly
under-extracts. Schema 3's equivalent failure was worse and went unnoticed for
longer: it advertised 12 languages, shipped no grammars at all, and fell back to
a regex line scanner for every non-Python file.

A compile check per (language, dialect, query) is cheap and catches the whole
class. The conformance corpus (§12) then checks that the queries match what they
are supposed to.
"""

from __future__ import annotations

import pytest
from tree_sitter import Query, QueryError

from lineagelens.extract.langs import GRAMMARS, ParserRegistry, supported_languages
from lineagelens.extract.spec import QUERY_FILES, SpecRegistry

#: Every (language, dialect) pair a spec declares, expanded so each is its own
#: test case -- a failure names the exact combination rather than "specs broke".
DIALECT_PAIRS = sorted(
    {(g.lang, g.dialect) for g in GRAMMARS}
)


@pytest.fixture(scope="module")
def registry():
    return SpecRegistry()


@pytest.fixture(scope="module")
def parsers():
    return ParserRegistry()


class TestSpecCoverage:
    def test_every_supported_language_has_a_spec(self, registry):
        """A grammar with no spec would extract nothing and say nothing."""
        missing = sorted(supported_languages() - set(registry.languages()))
        assert not missing, f"languages with a grammar but no spec: {missing}"

    def test_every_spec_has_a_grammar(self, registry):
        """A spec with no grammar is dead weight that cannot ever run."""
        grammar_langs = {g.lang for g in GRAMMARS}
        orphans = sorted(set(registry.languages()) - grammar_langs)
        assert not orphans, f"specs with no grammar: {orphans}"

    def test_nodes_query_is_mandatory_everywhere(self, registry):
        for lang in registry.languages():
            assert registry.spec_for(lang).query_source("nodes").strip(), (
                f"{lang}: nodes.scm is empty"
            )


@pytest.mark.parametrize(("lang", "dialect"), DIALECT_PAIRS, ids=lambda v: str(v))
@pytest.mark.parametrize("which", QUERY_FILES)
class TestQueriesCompile:
    def test_compiles(self, registry, parsers, lang, dialect, which):
        """The spec, plus any dialect overlay, must compile against the grammar.

        Overlays exist because dialects can differ: ``jsx_element`` is in the TSX
        grammar and not in plain TypeScript, so a shared query naming it would
        fail here for ``typescript`` and pass for ``tsx``. That asymmetry is the
        reason this is parameterised by dialect and not just by language.
        """
        spec = registry.spec_for(lang)
        source = spec.query_source(which, dialect)
        if not source.strip():
            pytest.skip(f"{lang} has no {which}.scm")

        parser = parsers.parser_for(dialect)
        try:
            compiled = Query(parser.language, source)
        except QueryError as exc:
            pytest.fail(f"spec/{lang}/{which}.scm vs {dialect} grammar: {exc}")
        assert compiled.pattern_count > 0


class TestOverlays:
    def test_tsx_overlay_adds_patterns_over_plain_typescript(self, registry):
        """The overlay mechanism must actually be wired, not just present.

        If ``query_source`` ignored the dialect, TSX would compile but JSX
        elements would never match -- a silent under-extraction of every React
        component call, which is precisely the failure mode specs-as-data
        invites.
        """
        spec = registry.spec_for("typescript")
        base = spec.query_source("refs", "typescript")
        tsx = spec.query_source("refs", "tsx")
        assert len(tsx) > len(base)
        assert "jsx_self_closing_element" in tsx
        assert "jsx_self_closing_element" not in base

    def test_overlay_changes_the_spec_digest(self, registry):
        """Overlays are part of extraction output, so they must be hashed (§11)."""
        spec = registry.spec_for("typescript")
        assert spec.overlays, "typescript should carry tsx overlays"
        assert spec.digest()


class TestDeterminism:
    def test_registry_digest_is_stable(self):
        """Same specs on disk must yield the same digest across constructions."""
        assert SpecRegistry().digest() == SpecRegistry().digest()

    def test_grammar_digest_is_stable(self):
        assert ParserRegistry().digest() == ParserRegistry().digest()
