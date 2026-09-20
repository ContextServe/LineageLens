"""Language detection and grammar loading.

Two behaviours here are deliberate reversals of what schema 3 did.

**Detection is per file, not per repository.** ``is_js_project`` returned true
for any repo containing a ``package.json`` or one ``.ts`` file under ``src/``,
and the caller then early-returned after analysing only that language. Running
it on this repository produced 39 TypeScript symbols and silently discarded
8,532 lines of Python. Language is a property of a file; a repo has as many as
it has.

**A missing grammar is a hard failure.** Schema 3 declared 12 languages in a
map, shipped zero grammar packages, and silently fell through to a regex line
scanner for every non-Python file -- every time. On Apache Dubbo that yielded
4,297 "classes" matched from ``"class " in line`` and five "methods" that were
string literals lifted out of a switch statement. Confident garbage is worse
than a recorded gap, so an unavailable grammar produces a ``skipped`` file
record and a capability-matrix hole instead of a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MissingGrammar(RuntimeError):
    """No tree-sitter grammar is available for a language.

    Raised rather than degraded. The indexer catches this per file and records
    ``skip_reason='missing_grammar'`` so the gap is queryable (§10.6).
    """


@dataclass(frozen=True, slots=True)
class Grammar:
    """A tree-sitter grammar and how to load it.

    ``entry_point`` exists because the grammar packages are not uniform:
    ``tree_sitter_typescript`` exposes ``language_typescript`` and
    ``language_tsx`` rather than a single ``language``, since TSX is a genuinely
    different grammar and not a dialect flag.
    """

    lang: str            # logical language, shared by dialects ("typescript")
    dialect: str         # grammar identity ("typescript", "tsx")
    module: str
    entry_point: str
    package: str         # distribution name, for the pinned-version digest


#: Every grammar this build knows how to load.
#:
#: Dialects that share a logical language (``typescript``/``tsx``) map to the
#: same ``lang`` so one extraction spec serves both, while still loading the
#: correct parser.
GRAMMARS: tuple[Grammar, ...] = (
    Grammar("python", "python", "tree_sitter_python", "language", "tree-sitter-python"),
    Grammar("java", "java", "tree_sitter_java", "language", "tree-sitter-java"),
    Grammar("javascript", "javascript", "tree_sitter_javascript", "language",
            "tree-sitter-javascript"),
    Grammar("typescript", "typescript", "tree_sitter_typescript", "language_typescript",
            "tree-sitter-typescript"),
    Grammar("typescript", "tsx", "tree_sitter_typescript", "language_tsx",
            "tree-sitter-typescript"),
    Grammar("go", "go", "tree_sitter_go", "language", "tree-sitter-go"),
    Grammar("rust", "rust", "tree_sitter_rust", "language", "tree-sitter-rust"),
    Grammar("csharp", "csharp", "tree_sitter_c_sharp", "language", "tree-sitter-c-sharp"),
    # Wave 1 (#61), all at L0: nodes.scm only, no refs or dataflow. The level
    # is derived from which specs loaded, so nothing here claims more than it
    # delivers -- and the conformance run fails if it did.
    Grammar("c", "c", "tree_sitter_c", "language", "tree-sitter-c"),
    Grammar("ruby", "ruby", "tree_sitter_ruby", "language", "tree-sitter-ruby"),
    Grammar("bash", "bash", "tree_sitter_bash", "language", "tree-sitter-bash"),
    Grammar("kotlin", "kotlin", "tree_sitter_kotlin", "language", "tree-sitter-kotlin"),
)

#: File extension to grammar dialect. ``.tsx`` and ``.jsx`` need their own
#: grammars, not just their own spec.
EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "tsx",         # tree-sitter-javascript has no JSX; the TSX grammar covers it
    ".ts": "typescript",
    # Wave 1 (#61).
    ".c": "c",
    ".h": "c",           # a C header is an inventory of what a unit exposes
    ".rb": "ruby",
    ".rake": "ruby",
    ".gemspec": "ruby",
    ".sh": "bash",
    ".bash": "bash",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
}

#: Interpreter names seen in a shebang, for files with no useful extension.
SHEBANGS: dict[str, str] = {
    "python": "python", "python2": "python", "python3": "python",
    "node": "javascript", "nodejs": "javascript", "bun": "typescript",
    "deno": "typescript",
}

_BY_DIALECT = {g.dialect: g for g in GRAMMARS}
_LANGS = {g.lang for g in GRAMMARS}


def supported_languages() -> frozenset[str]:
    """Logical languages this build has specs and grammars for."""
    return frozenset(_LANGS)


def detect_dialect(path: Path, *, head: bytes | None = None) -> str | None:
    """Which grammar dialect applies to ``path``, or ``None`` if unknown.

    Extension first, then a shebang. ``head`` lets the caller pass bytes it has
    already read rather than re-opening the file during a walk of a large repo.
    """
    dialect = EXTENSIONS.get(path.suffix.lower())
    if dialect is not None:
        return dialect

    # No recognised extension: fall back to a shebang. Extensionless executable
    # scripts are common as CLI entry points, and missing them means missing an
    # entry point the whole reachability walk is seeded from.
    if head is None:
        try:
            with path.open("rb") as fh:
                head = fh.read(128)
        except OSError:
            return None
    if not head.startswith(b"#!"):
        return None

    first = head.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    # Handles both `#!/usr/bin/python3` and `#!/usr/bin/env python3`.
    for token in reversed(first.replace("#!", "").split()):
        name = token.rsplit("/", 1)[-1]
        if name in SHEBANGS:
            return SHEBANGS[name]
    return None


def language_of(dialect: str) -> str:
    """Logical language for a grammar dialect (``tsx`` -> ``typescript``)."""
    grammar = _BY_DIALECT.get(dialect)
    if grammar is None:
        raise MissingGrammar(f"unknown grammar dialect: {dialect!r}")
    return grammar.lang


def grammar_version(dialect: str) -> str | None:
    """Installed version of a dialect's grammar package, or ``None`` if absent."""
    grammar = _BY_DIALECT.get(dialect)
    if grammar is None:
        return None
    try:
        return version(grammar.package)
    except PackageNotFoundError:
        return None


class ParserRegistry:
    """Loads and caches tree-sitter parsers.

    Parsers are cached because constructing one is not free and a repository
    walk hits the same handful of dialects thousands of times. Failures are
    cached too -- a missing grammar will not appear mid-run, and retrying the
    import per file on a large repo is measurable.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._failures: dict[str, str] = {}

    def parser_for(self, dialect: str) -> Any:
        """Return a parser for ``dialect``, or raise :class:`MissingGrammar`."""
        if dialect in self._parsers:
            return self._parsers[dialect]
        if dialect in self._failures:
            raise MissingGrammar(self._failures[dialect])

        grammar = _BY_DIALECT.get(dialect)
        if grammar is None:
            reason = f"no grammar registered for dialect {dialect!r}"
            self._failures[dialect] = reason
            raise MissingGrammar(reason)

        try:
            from tree_sitter import Language, Parser
        except ImportError as exc:  # pragma: no cover - dependency is declared
            reason = f"tree-sitter is not installed: {exc}"
            self._failures[dialect] = reason
            raise MissingGrammar(reason) from exc

        try:
            module = import_module(grammar.module)
            factory = getattr(module, grammar.entry_point)
            parser = Parser(Language(factory()))
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            reason = (
                f"grammar for {grammar.lang} ({grammar.dialect}) is unavailable: {exc}. "
                f"Install with: pip install {grammar.package}"
            )
            self._failures[dialect] = reason
            raise MissingGrammar(reason) from exc

        self._parsers[dialect] = parser
        return parser

    def can_parse(self, dialect: str) -> bool:
        """Whether ``dialect`` has a loadable grammar, without raising.

        Exists because a missing grammar became a routine state when wave-1
        grammars moved behind extras (#61): the indexer needs to decide
        whether to skip a file, and an exception is the wrong shape for a
        decision made once per file.
        """
        try:
            self.parser_for(dialect)
        except MissingGrammar:
            return False
        return True

    def available(self) -> dict[str, str]:
        """``{dialect: version}`` for every grammar that loads.

        Feeds the capability matrix (§12), which reports measured availability
        per install rather than a hand-maintained claim.
        """
        found: dict[str, str] = {}
        for grammar in GRAMMARS:
            try:
                self.parser_for(grammar.dialect)
            except MissingGrammar:
                continue
            found[grammar.dialect] = grammar_version(grammar.dialect) or "unknown"
        return found

    def digest(self) -> str:
        """Hash of the loadable grammar set and versions, for ``graph_meta`` (§11).

        A grammar upgrade changes parse output, so it must invalidate the index
        rather than silently altering results. Recording the digest is what makes
        that detectable.
        """
        import hashlib

        available = self.available()
        payload = "\x00".join(f"{d}={available[d]}" for d in sorted(available))
        return hashlib.blake2b(payload.encode(), digest_size=16).hexdigest()
