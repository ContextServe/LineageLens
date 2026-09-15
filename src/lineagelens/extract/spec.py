"""Declarative extraction specs (issue #51 §7.1).

Hand-written extractors do not scale to six languages. ``analyzer.py`` was 1,563
lines of bespoke Python ``ast`` visitor; six of those guarantees exactly the
per-language divergence the rearchitecture exists to remove -- one language would
grow ``IMPLEMENTS`` support and the others would silently lack it, which is how
the schema-3 ontology came to declare four relation kinds while nine were
emitted and only one reached non-Python code.

So a language is *data*: three tree-sitter query files and a manifest. One
engine (``extract.engine``) consumes them all. Adding Go means authoring queries
and fixtures, not writing an analyser.

Capture convention
------------------

The engine keys off capture names, so the convention is the interface between a
spec and the engine.

``nodes.scm`` -- each pattern declares exactly one node::

    @node.<kind>     the node's full span; <kind> is a NodeKind value
    @name            its identifier
    @params          the parameter list (span only; parameters are their own nodes)
    @return_type     declared return type
    @type            declared type, for field/parameter/variable/constant
    @docstring       doc comment or docstring literal
    @visibility      an access modifier token
    @decorator       a decorator or annotation (may repeat)
    @type_param      a generic type parameter (may repeat)
    @modifier        static/abstract/final/async/etc. (may repeat)

``refs.scm`` -- each pattern declares one reference *observation*. It may not
declare a target, because resolving is not an extractor's job (§4)::

    @ref.<kind>      the reference's span; <kind> is a RefKind value
    @ref.name        the referenced name as written
    @receiver        receiver expression, kept as a resolution hint
    @ref.arg         one argument at a call site (may repeat), for PARAM_BINDS

``dataflow.scm`` -- def-use facts within one body (§9)::

    @flow.<read|write|return>
    @flow.name       the value being read or written
    @flow.value      the expression supplying it
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

logger = logging.getLogger(__name__)

#: Directory holding one subdirectory per language.
SPEC_ROOT = Path(__file__).resolve().parent.parent / "spec"

#: Query files a language may provide. Only ``nodes`` is mandatory -- a language
#: with no ``dataflow.scm`` is reported as ``dataflow: unsupported`` in the
#: capability matrix rather than silently answering data-flow queries with
#: nothing (§12).
QUERY_FILES = ("nodes", "refs", "dataflow")

#: Optional per-dialect overlay, appended to the shared query when compiling for
#: that dialect: ``refs.tsx.scm`` extends ``refs.scm`` for the ``tsx`` grammar
#: only.
#:
#: This exists because dialects of one language can have genuinely different
#: grammars. TypeScript and TSX share every declaration and call node type, so
#: one spec serves both -- but ``jsx_element`` and friends exist *only* in the
#: TSX grammar, and a query naming them fails to compile against plain
#: TypeScript. Splitting the language in two would duplicate ~60 patterns to
#: accommodate four; an overlay keeps the shared part shared.
OVERLAY_TEMPLATE = "{name}.{dialect}.scm"


class SpecError(RuntimeError):
    """A spec is missing, malformed, or violates the capture convention."""


@dataclass(slots=True)
class LanguageSpec:
    """The extraction rules for one logical language."""

    lang: str
    root: Path
    dialects: tuple[str, ...]
    sources: dict[str, str] = field(default_factory=dict)
    #: ``{(query_name, dialect): source}`` overlays, see :data:`OVERLAY_TEMPLATE`.
    overlays: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Names whose declaration order defines a callable's signature. Languages
    #: differ on whether a receiver counts: Python's ``self`` and Go's method
    #: receiver are declared parameters but are not part of the overload
    #: signature, so they are dropped before hashing.
    implicit_receivers: tuple[str, ...] = ()
    #: Text that introduces a doc comment, used to strip markers before storing.
    doc_prefixes: tuple[str, ...] = ()
    #: Callable names that are really constructors. Declared per language
    #: because the spelling is arbitrary (`__init__`, `constructor`, `new`) and
    #: because in most languages a constructor is grammatically an ordinary
    #: method -- so the engine promotes the kind rather than each spec needing a
    #: second pattern that would collide with the first (see engine._merge_by_span).
    constructor_names: tuple[str, ...] = ()

    @property
    def has_dataflow(self) -> bool:
        """Whether this language has data-flow queries at all.

        Drives ``DataflowStatus.UNSUPPORTED`` in the capability matrix, so a
        language without them reports the gap rather than answering data-flow
        queries with a confident empty result.
        """
        return bool(self.sources.get("dataflow", "").strip())

    def query_source(self, name: str, dialect: str | None = None) -> str:
        """Query text for ``name``, with ``dialect``'s overlay appended if any."""
        base = self.sources.get(name, "")
        if dialect is None:
            return base
        overlay = self.overlays.get((name, dialect), "")
        if not overlay:
            return base
        return f"{base}\n{overlay}"

    def digest(self) -> str:
        """Hash of this spec's query text, for ``graph_meta.spec_digest`` (§11).

        Editing a query changes extraction output, so it must invalidate the
        index the same way a grammar upgrade does. Overlays are included, in
        sorted order so the digest does not depend on directory listing order.
        """
        h = hashlib.blake2b(digest_size=16)
        h.update(self.lang.encode())
        for name in QUERY_FILES:
            h.update(b"\x00")
            h.update(name.encode())
            h.update(b"\x00")
            h.update(self.sources.get(name, "").encode())
        for key in sorted(self.overlays):
            h.update(b"\x00")
            h.update(f"{key[0]}.{key[1]}".encode())
            h.update(b"\x00")
            h.update(self.overlays[key].encode())
        return h.hexdigest()


class SpecRegistry:
    """Loads every language spec found under :data:`SPEC_ROOT`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SPEC_ROOT
        self._specs: dict[str, LanguageSpec] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self.root.is_dir():
            raise SpecError(
                f"no extraction specs at {self.root}. Tier A extraction is not "
                f"optional (§7.1), so this is a broken install rather than a "
                f"degraded one."
            )
        # Sorted so registry construction is order-independent (§11).
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            self._specs[entry.name] = self._load_one(entry)
        self._loaded = True

    def _load_one(self, path: Path) -> LanguageSpec:
        manifest_path = path / "lang.toml"
        if not manifest_path.is_file():
            raise SpecError(f"{path.name}: missing lang.toml")
        try:
            manifest: dict[str, Any] = tomllib.loads(manifest_path.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise SpecError(f"{manifest_path}: {exc}") from exc

        lang = manifest.get("lang", path.name)
        dialects = tuple(manifest.get("dialects", [lang]))

        sources: dict[str, str] = {}
        overlays: dict[tuple[str, str], str] = {}
        for name in QUERY_FILES:
            query_path = path / f"{name}.scm"
            if query_path.is_file():
                sources[name] = query_path.read_text("utf-8")
            elif name == "nodes":
                raise SpecError(f"{path.name}: nodes.scm is mandatory")

            for dialect in dialects:
                overlay_path = path / OVERLAY_TEMPLATE.format(name=name, dialect=dialect)
                if overlay_path.is_file():
                    overlays[(name, dialect)] = overlay_path.read_text("utf-8")

        return LanguageSpec(
            lang=lang,
            root=path,
            dialects=dialects,
            sources=sources,
            overlays=overlays,
            implicit_receivers=tuple(manifest.get("implicit_receivers", [])),
            doc_prefixes=tuple(manifest.get("doc_prefixes", [])),
            constructor_names=tuple(manifest.get("constructor_names", [])),
        )

    def spec_for(self, lang: str) -> LanguageSpec:
        self._load()
        spec = self._specs.get(lang)
        if spec is None:
            raise SpecError(
                f"no extraction spec for {lang!r}. Available: {sorted(self._specs)}"
            )
        return spec

    def has(self, lang: str) -> bool:
        self._load()
        return lang in self._specs

    def languages(self) -> tuple[str, ...]:
        self._load()
        return tuple(sorted(self._specs))

    def digest(self) -> str:
        """Combined digest across all specs, for ``graph_meta.spec_digest``."""
        self._load()
        h = hashlib.blake2b(digest_size=16)
        for lang in sorted(self._specs):
            h.update(self._specs[lang].digest().encode())
        return h.hexdigest()
