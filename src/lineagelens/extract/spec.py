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

#: Resiliency rule pack, loaded alongside the queries (#60). Optional: a
#: language without one reports ``risks: unsupported`` rather than an empty
#: list, because "no risks found" is the answer a reader most wants to believe
#: and must not be produced by an absence of analysis.
RISKS_FILE = "risks.toml"

#: The capability ladder (#56). A level is *derived* from which spec files
#: loaded, never declared in a manifest: #51 §11 requires capability to be
#: measured rather than asserted, and a declared level would drift the moment
#: someone added a grammar without a spec -- which is the exact failure the
#: ladder exists to make visible.
#:
#: Ordered floor-first. ``nodes.scm`` is mandatory, so L0 is the floor and
#: there is no level below it.
LEVELS = ("L0", "L1", "L2")

#: What each level adds, for the one place a human reads it.
LEVEL_MEANING = {
    "L0": "inventory: symbols, search, module/class/function map",
    "L1": "graph: CALLS/IMPORTS, callers_of, callees_of, impact_of",
    "L2": "flow: READS/WRITES/PARAM_BINDS/RETURNS, dataflow_of, contracts",
}

#: Edge kinds whose presence in a corpus run corroborates a claimed level.
#: A language claiming L1 whose corpus produced no CALLS or IMPORTS edge is not
#: an L1 language -- it has a `refs.scm` that does not match its grammar, which
#: is worse than having none, because the level would advertise a capability
#: the queries do not deliver.
LEVEL_EVIDENCE = {
    "L1": ("CALLS", "IMPORTS"),
    "L2": ("READS", "WRITES", "PARAM_BINDS", "RETURNS"),
}

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


def _dumps_stable(value: Any) -> str:
    """Canonical JSON, so a digest does not depend on key order."""
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _strip_comments(source: str) -> str:
    """Query text with tree-sitter ``;`` comments removed.

    Used only to decide whether a spec file is substantive. A file containing
    nothing but a header comment is a placeholder, and treating it as a
    capability is how a measured level silently becomes a declared one.
    """
    return "\n".join(
        line.strip() for line in source.splitlines()
        if line.strip() and not line.strip().startswith(";")
    )


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
    #: Resiliency rules from ``risks.toml``, or ``None`` when the language has
    #: none (#60). ``None`` rather than an empty list on purpose: a language
    #: with no rule pack reports ``unsupported``, and an empty risk list must
    #: not look the same as an unanalysed one.
    risks: tuple[dict[str, Any], ...] | None = None
    #: Constructs that mark an async frame in a language with no async keyword.
    async_markers: tuple[str, ...] = ()

    @property
    def level(self) -> str:
        """Capability level, derived from which spec files actually loaded.

        Not read from ``lang.toml``. See :data:`LEVELS` for why.
        """
        if self._has("dataflow"):
            return "L2"
        if self._has("refs"):
            return "L1"
        # `nodes.scm` is mandatory (`_load_one` raises without it), so a spec
        # that exists at all is at least L0.
        return "L0"

    def _has(self, name: str) -> bool:
        """Whether a query file loaded *and* carries content.

        An empty or comment-only file is not a capability. Checking presence of
        the key alone would let a placeholder `refs.scm` promote a language to
        L1 while extracting nothing -- a declared level wearing a measured
        level's clothes.
        """
        return bool(_strip_comments(self.sources.get(name, "")).strip())

    @property
    def has_dataflow(self) -> bool:
        """Whether this language has data-flow queries at all.

        Drives ``DataflowStatus.UNSUPPORTED`` in the capability matrix, so a
        language without them reports the gap rather than answering data-flow
        queries with a confident empty result.
        """
        return self._has("dataflow")

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
        # Rules participate: changing a rule changes findings, so it must
        # invalidate the index the same way editing a query does (#60 step 1).
        if self.risks is not None:
            h.update(b"\x00risks\x00")
            h.update(_dumps_stable(self.risks).encode())
        if self.async_markers:
            h.update(b"\x00async\x00")
            h.update(",".join(sorted(self.async_markers)).encode())
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

        # Rules live beside the extraction specs because a rule is
        # language-specific and reviewable as a diff, same as a query.
        risks: tuple[dict[str, Any], ...] | None = None
        async_markers: tuple[str, ...] = ()
        risks_path = path / RISKS_FILE
        if risks_path.is_file():
            try:
                pack = tomllib.loads(risks_path.read_text("utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise SpecError(f"{risks_path}: {exc}") from exc
            risks = tuple(pack.get("rule", ()))
            async_markers = tuple(pack.get("async_markers", ()))
            if not risks:
                raise SpecError(
                    f"{risks_path}: contains no [[rule]]. An empty rule pack "
                    f"would report 'analysed, no risks' over nothing analysed."
                )

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
            risks=risks,
            async_markers=async_markers,
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

    def risk_rules(self, lang: str) -> tuple[dict[str, Any], ...] | None:
        """Rule pack for ``lang``, or ``None`` when it has none."""
        self._load()
        spec = self._specs.get(lang)
        return spec.risks if spec else None

    def languages_with_risks(self) -> tuple[str, ...]:
        self._load()
        return tuple(
            lang for lang, spec in sorted(self._specs.items())
            if spec.risks
        )

    def levels(self) -> dict[str, str]:
        """``{lang: level}`` for every loaded spec."""
        self._load()
        return {lang: spec.level for lang, spec in sorted(self._specs.items())}

    def digest(self) -> str:
        """Combined digest across all specs, for ``graph_meta.spec_digest``."""
        self._load()
        h = hashlib.blake2b(digest_size=16)
        for lang in sorted(self._specs):
            h.update(self._specs[lang].digest().encode())
        return h.hexdigest()
