"""The one extractor. Consumes a declarative spec, emits observations.

There is exactly one of these for all six languages. Everything
language-specific lives in ``spec/<lang>/*.scm``; everything here is shared.
That is the whole point -- schema 3 had a separate analyser per language, and
they diverged immediately: Python grew nine relation kinds while every other
language got one, and the tree-sitter path resolved references by bare trailing
name and deleted whatever failed to match.

What this module may and may not do
-----------------------------------

It emits :class:`~lineagelens.core.types.Observation` -- nodes with spans, and
references with spans. It cannot emit an :class:`~lineagelens.core.types.Edge`,
because ``Observation`` has no field for one. Resolving is the resolver's job.

The one structural relationship it *does* record is ``parent_id``: lexical
containment read straight off the parse tree. That is not a resolved reference
-- no lookup, no candidate set, no possibility of being wrong about it -- so the
resolver materialises ``CONTAINS`` edges from it rather than re-deriving nesting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from ..core import (
    Boundary,
    BoundaryKind,
    ExtractorRun,
    FileRecord,
    Node,
    NodeFlags,
    NodeKind,
    Observation,
    ParseError,
    ParseStatus,
    RefKind,
    SkipReason,
    Span,
    Tier,
    UnresolvedRef,
    Visibility,
    anonymous_member,
    node_id,
    qualified_name,
    signature_hash,
)
from .langs import MissingGrammar, ParserRegistry, language_of
from .spec import LanguageSpec, SpecRegistry

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "tier-a-spec"
EXTRACTOR_VERSION = "1"

#: Node kinds that can lexically contain other code, and are therefore
#: candidates for "which node is this reference written inside". Excludes value
#: kinds: a reference is never *inside* a parameter, it is inside the callable
#: that declares it.
CONTAINER_KINDS = frozenset({
    NodeKind.MODULE, NodeKind.FILE, NodeKind.CLASS, NodeKind.INTERFACE,
    NodeKind.ENUM, NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ANNOTATION,
    NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CONSTRUCTOR, NodeKind.PROPERTY,
})

#: Modifier tokens mapped onto node flags, shared across languages because the
#: keywords happen to coincide. A language whose spelling differs captures the
#: equivalent token and it lands here.
_MODIFIER_FLAGS: dict[str, NodeFlags] = {
    "async": NodeFlags.ASYNC,
    "static": NodeFlags.STATIC,
    "abstract": NodeFlags.ABSTRACT,
    "final": NodeFlags.FINAL,
    "const": NodeFlags.FINAL,
    "readonly": NodeFlags.FINAL,
    "sealed": NodeFlags.FINAL,
    "export": NodeFlags.EXPORTED,
    "default": NodeFlags.EXPORTED,
    "pub": NodeFlags.EXPORTED,
    "deprecated": NodeFlags.DEPRECATED,
    "override": NodeFlags.OVERRIDE,
}

_VISIBILITY_TOKENS: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "private": Visibility.PRIVATE,
    "protected": Visibility.PROTECTED,
    "internal": Visibility.INTERNAL,
    "package": Visibility.PACKAGE,
    "pub": Visibility.PUBLIC,
}

#: Kinds that can own methods, used to promote a function to a method (§7.1).
_TYPE_KINDS = frozenset({
    NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM,
    NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ANNOTATION,
})

#: Specificity ranking, for when several patterns match one span with different
#: kinds. Writing a spec so that a richer pattern and a plainer one both match
#: the same construct is intentional -- it is how optional captures like a
#: docstring or a type annotation are layered -- so the merge has to pick, and
#: it picks the most specific claim. Unranked kinds tie at 0 and fall back to a
#: stable alphabetical order.
_KIND_RANK: dict[NodeKind, int] = {
    NodeKind.CONSTRUCTOR: 40,
    NodeKind.PROPERTY: 35,
    NodeKind.METHOD: 30,
    NodeKind.FUNCTION: 20,
    NodeKind.ENUM_MEMBER: 35,
    NodeKind.CONSTANT: 30,
    NodeKind.FIELD: 25,
    NodeKind.PARAMETER: 22,
    NodeKind.VARIABLE: 20,
}


def _strictly_contains(outer: Span, inner: Span) -> bool:
    """Containment excluding equality, so equal spans are siblings not kin."""
    return (
        outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
        and (outer.start_byte, outer.end_byte) != (inner.start_byte, inner.end_byte)
    )


def _merge_by_span(raws: list[_Raw]) -> list[_Raw]:
    """Collapse matches that describe the same construct into one node.

    Several patterns matching one construct is the normal case, not an error: a
    Python ``def`` matches the bare pattern, the docstring-bearing pattern, and
    the ``async`` pattern, each contributing different captures. Without this
    merge each becomes its own node, so ``submit`` appears twice -- once with
    its parameters and once without -- and the two then nest inside each other
    because a span contains itself.

    Captures are unioned so the merged node keeps the docstring from one match
    and the modifiers from another. The kind is the most specific claim, by
    :data:`_KIND_RANK`.
    """
    by_span: dict[tuple[int, int], _Raw] = {}
    for raw in raws:
        key = (raw.span.start_byte, raw.span.end_byte)
        existing = by_span.get(key)
        if existing is None:
            by_span[key] = raw
            continue

        for capture, nodes in raw.captures.items():
            existing.captures.setdefault(capture, nodes)
        if not existing.name and raw.name:
            existing.name = raw.name
        if _rank(raw.kind) > _rank(existing.kind):
            existing.kind = raw.kind

    return list(by_span.values())


def _rank(kind: NodeKind) -> tuple[int, str]:
    return (_KIND_RANK.get(kind, 0), kind.value)


def _dedupe_refs(refs: list[UnresolvedRef]) -> list[UnresolvedRef]:
    """Collapse repeated observations of one reference, unioning arguments.

    tree-sitter emits one match per binding of a repeated capture, so
    ``arguments: (argument_list (_) @ref.arg)?`` over a two-argument call yields
    two matches for a single call site. Left alone, that both inflates the
    reference count -- corrupting the coverage accounting in §16.5 -- and gives
    each copy a partial argument list, which would make PARAM_BINDS bind
    argument 0 twice and never argument 1.
    """
    merged: dict[tuple[str, str, int, int, str], UnresolvedRef] = {}
    for ref in refs:
        key = (
            ref.from_node, ref.ref_kind.value,
            ref.span.start_byte, ref.span.end_byte, ref.ref_text,
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = ref
            continue

        args = {a["start_byte"]: a for a in existing.metadata.get("args", ())}
        args.update({a["start_byte"]: a for a in ref.metadata.get("args", ())})
        if args:
            existing.metadata["args"] = [args[k] for k in sorted(args)]
        if not existing.receiver_hint and ref.receiver_hint:
            merged[key] = replace(existing, receiver_hint=ref.receiver_hint,
                                  metadata=existing.metadata)

    # Argument position is source order, assigned only now that every match for
    # a call site has been merged.
    for ref in merged.values():
        for position, arg in enumerate(ref.metadata.get("args", ())):
            arg["index"] = position
    return list(merged.values())


@dataclass(slots=True)
class _Raw:
    """A node match before identity is assigned.

    Ids cannot be minted during the query walk: a callable's id includes its
    signature hash, and the signature comes from parameter nodes that are its
    children. So matches are collected, the tree is built, signatures are
    computed bottom-up, and only then are ids assigned.
    """

    span: Span
    kind: NodeKind
    name: str
    captures: dict[str, list[Any]]
    members: tuple[str, ...] = ()
    parent: int | None = None
    children: list[int] = field(default_factory=list)
    node_id: str = ""
    qname: str = ""
    sig_hash: str = ""


class SpecExtractor:
    """Runs a language spec over a file and returns what it observed."""

    def __init__(
        self,
        *,
        specs: SpecRegistry | None = None,
        parsers: ParserRegistry | None = None,
    ) -> None:
        self.specs = specs or SpecRegistry()
        self.parsers = parsers or ParserRegistry()
        self._queries: dict[tuple[str, str], Any] = {}

    # ---- public API -------------------------------------------------------

    def extract(
        self,
        *,
        path: str,
        content: bytes,
        dialect: str,
        content_hash: str,
        service: str = "",
        module_path: str = "",
        service_id: str | None = None,
    ) -> Observation:
        """Extract one file.

        ``dialect`` selects the grammar; ``service``/``module_path`` feed the
        qualified-name scheme (§6). A parse failure yields an observation with a
        ``failed`` file record rather than an exception, so one unparseable file
        cannot abort an index -- but it is *recorded*, not skipped silently.
        """
        lang = language_of(dialect)
        spec = self.specs.spec_for(lang)

        try:
            parser = self.parsers.parser_for(dialect)
        except MissingGrammar as exc:
            # Refusal, not degradation. Falling back to a regex scanner here is
            # what produced 4,297 bogus classes and five string-literal
            # "methods" on Dubbo, all reported as successful extraction.
            logger.warning("skipping %s: %s", path, exc)
            return Observation(file=FileRecord(
                path=path, lang=lang, content_hash=content_hash,
                size_bytes=len(content), parse_status=ParseStatus.SKIPPED,
                service_id=service_id, skip_reason=SkipReason.MISSING_GRAMMAR,
            ))

        tree = parser.parse(content)
        root = tree.root_node

        errors = tuple(_collect_parse_errors(root))
        status = ParseStatus.PARTIAL if errors else ParseStatus.OK

        record = FileRecord(
            path=path, lang=lang, content_hash=content_hash, size_bytes=len(content),
            parse_status=status, service_id=service_id, parse_errors=errors,
            extractors=(ExtractorRun(name=EXTRACTOR_NAME, version=EXTRACTOR_VERSION,
                                     tier=Tier.A),),
        )
        observation = Observation(file=record)

        # A module node per file: the root container, so every symbol has a
        # parent and CONTAINS forms a single tree rather than a forest.
        module_qname = qualified_name(service, lang, module_path)
        module = Node(
            id=node_id(service, lang, module_qname),
            kind=NodeKind.MODULE,
            name=module_path.rsplit(".", 1)[-1] or path,
            qualified_name=module_qname,
            lang=lang,
            span=Span.whole_file(content),
            file_path=path,
            service_id=service_id,
        )
        observation.nodes.append(module)

        raws = self._collect_nodes(spec, dialect, root)
        self._build_tree(raws)
        self._reclassify(raws, spec)
        self._assign_identity(raws, spec, service, lang, module_path)
        observation.nodes.extend(
            self._to_node(r, path, service_id, lang, module.id, raws) for r in raws
        )

        # Reference and data-flow observations attach to the innermost container
        # they are written inside.
        lookup = _ContainerLookup(
            [(r.span, r.node_id) for r in raws if r.kind in CONTAINER_KINDS],
            fallback=module.id,
        )
        observation.refs.extend(self._collect_refs(spec, dialect, root, path, lookup))
        if spec.has_dataflow:
            observation.refs.extend(
                self._collect_dataflow(spec, dialect, root, path, lookup)
            )

        if errors:
            # A syntax error means part of the file was not walked, so any
            # "nothing references this" answer over it is unreliable. Attached to
            # the module rather than to a guessed enclosing node: the point is
            # that this region was *not* understood, so claiming to know which
            # symbol it belongs to would overstate what was parsed.
            #
            # Capped, because one misplaced brace can yield hundreds of ERROR
            # nodes and the file-level record already carries the full list.
            observation.boundaries.extend(
                Boundary(
                    node_id=module.id,
                    kind=BoundaryKind.PARSE_ERROR,
                    detail=f"{path}:{e.line}:{e.col} {e.message}",
                )
                for e in errors[:20]
            )

        return observation

    # ---- node collection --------------------------------------------------

    def _query(self, spec: LanguageSpec, dialect: str, which: str) -> Any | None:
        """Compile and cache a query. Returns ``None`` if the spec omits it."""
        key = (dialect, which)
        if key in self._queries:
            return self._queries[key]

        source = spec.query_source(which, dialect)
        if not source.strip():
            self._queries[key] = None
            return None

        from tree_sitter import Query, QueryError

        parser = self.parsers.parser_for(dialect)
        try:
            compiled = Query(parser.language, source)
        except QueryError as exc:
            # A malformed spec is a build defect, not a per-file problem: it
            # would silently under-extract every file of that language.
            raise RuntimeError(
                f"spec/{spec.lang}/{which}.scm failed to compile against the "
                f"{dialect} grammar: {exc}"
            ) from exc
        self._queries[key] = compiled
        return compiled

    def _matches(self, query: Any, root: Any) -> list[tuple[int, dict[str, list[Any]]]]:
        """Run a query, in a stable order.

        tree-sitter returns matches in an unspecified order -- observed to be
        pattern-major rather than positional. Sorting by position makes both the
        containment walk and anonymous-member ordinals deterministic (§11).
        """
        from tree_sitter import QueryCursor

        matches = QueryCursor(query).matches(root)
        return sorted(matches, key=lambda m: _match_sort_key(m[1]))

    def _collect_nodes(
        self, spec: LanguageSpec, dialect: str, root: Any
    ) -> list[_Raw]:
        query = self._query(spec, dialect, "nodes")
        if query is None:
            return []

        raws: list[_Raw] = []
        for _, captures in self._matches(query, root):
            anchor_name = next(
                (c for c in captures if c.startswith("node.")), None
            )
            if anchor_name is None:
                continue
            try:
                kind = NodeKind(anchor_name.removeprefix("node."))
            except ValueError:
                logger.warning(
                    "spec/%s/nodes.scm declares unknown node kind %r; ignoring",
                    spec.lang, anchor_name,
                )
                continue

            anchors = captures[anchor_name]
            if not anchors:
                continue
            span = Span.from_tree_sitter(anchors[0])
            name = _text(captures.get("name"))
            raws.append(_Raw(span=span, kind=kind, name=name, captures=captures))

        merged = _merge_by_span(raws)
        # Outermost first, so a stack walk sees parents before children. On a
        # shared start offset the wider span is the parent.
        merged.sort(key=lambda r: (r.span.start_byte, -r.span.end_byte, r.kind.value))
        return merged

    def _build_tree(self, raws: list[_Raw]) -> None:
        """Link each match to its innermost enclosing match."""
        stack: list[int] = []
        for idx, raw in enumerate(raws):
            # Strict containment: a span contains itself, so an inclusive test
            # would make two matches at the same span parent and child of each
            # other. `_merge_by_span` makes spans unique, and this keeps the
            # invariant local rather than depending on that.
            while stack and not _strictly_contains(raws[stack[-1]].span, raw.span):
                stack.pop()
            if stack:
                raw.parent = stack[-1]
                raws[stack[-1]].children.append(idx)
            stack.append(idx)

    def _reclassify(self, raws: list[_Raw], spec: LanguageSpec) -> None:
        """Promote callables to method/constructor from their position.

        A spec declares ``@node.function`` for every callable, because the
        distinction is structural rather than syntactic: in Python, Go and Rust
        the same grammar node is a free function or a method depending only on
        what encloses it. Deciding it here rather than in each spec keeps the
        rule in one place for all six languages, and avoids the alternative --
        two patterns matching one construct with different kinds, which would
        reintroduce the duplicate nodes this pass exists to prevent.
        """
        for raw in raws:
            if raw.kind is not NodeKind.FUNCTION:
                continue
            if raw.name and raw.name in spec.constructor_names:
                raw.kind = NodeKind.CONSTRUCTOR
            elif raw.parent is not None and raws[raw.parent].kind in _TYPE_KINDS:
                raw.kind = NodeKind.METHOD

        self._reparent_fields(raws)

    def _reparent_fields(self, raws: list[_Raw]) -> None:
        """Attach an instance field to its type, not to the method that writes it.

        ``self.items = items`` is lexically inside the constructor, so plain
        containment makes the field a child of ``__init__``. Semantically it is a
        member of ``Order``, and the qualified name has to say so or a query for
        the type's fields cannot find it.

        Two consequences worth noting. Java and C# fields are already declared
        directly in the class body, so this is a no-op for them -- the rule is
        shared but only bites where the language allows it. And a field assigned
        in two different methods converges on one id, which is correct: it is one
        field written twice, and the store merges the writes into a single node.
        """
        for idx, raw in enumerate(raws):
            if raw.kind is not NodeKind.FIELD or raw.parent is None:
                continue
            owner = raw.parent
            while owner is not None and raws[owner].kind not in _TYPE_KINDS:
                owner = raws[owner].parent
            if owner is None or owner == raw.parent:
                continue
            # `idx`, not `raws.index(raw)`: _Raw is a plain dataclass, so
            # identity lookup by value could match a different node with equal
            # fields, and it would be O(n) per field besides.
            raws[raw.parent].children.remove(idx)
            raw.parent = owner
            raws[owner].children.append(idx)

    def _assign_identity(
        self,
        raws: list[_Raw],
        spec: LanguageSpec,
        service: str,
        lang: str,
        module_path: str,
    ) -> None:
        """Compute member paths and mint ids.

        Order matters. A callable's id depends on its signature hash, which
        depends on its parameter nodes -- its children. So member names are
        resolved top-down (a child needs its parent's path) while signatures are
        read from already-collected children.
        """
        anon_counters: dict[tuple[int | None, str], int] = {}

        for raw in raws:
            if raw.name:
                member = raw.name
            else:
                # Ordinal among same-kind anonymous siblings, never a line
                # number: a position-derived name would rename the lambda on
                # line 400 whenever line 10 changed (§6).
                key = (raw.parent, raw.kind.value)
                ordinal = anon_counters.get(key, 0)
                anon_counters[key] = ordinal + 1
                member = anonymous_member(raw.kind.value, ordinal)

            parent_members = raws[raw.parent].members if raw.parent is not None else ()
            raw.members = (*parent_members, member)

        for raw in raws:
            raw.qname = qualified_name(service, lang, module_path, list(raw.members))
            raw.sig_hash = self._signature_hash_for(raw, raws, spec)
            raw.node_id = node_id(service, lang, raw.qname, raw.sig_hash)

    def _signature_hash_for(
        self, raw: _Raw, raws: list[_Raw], spec: LanguageSpec
    ) -> str:
        """Signature hash for a callable, or ``""`` for anything else.

        Built from the callable's own parameter nodes, in declaration order.
        Implicit receivers (Python ``self``, a Go method receiver) are declared
        parameters but play no part in overload identity, so the spec names them
        and they are dropped here.
        """
        if raw.kind not in (
            NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CONSTRUCTOR, NodeKind.PROPERTY
        ):
            return ""

        params = [
            raws[i] for i in raw.children if raws[i].kind is NodeKind.PARAMETER
        ]
        params.sort(key=lambda p: p.span.start_byte)
        kept = [p for p in params if p.name not in spec.implicit_receivers]
        types = [_capture_text(p.captures, "type") or "" for p in kept]
        return signature_hash(types, arity=len(kept))

    def _to_node(
        self,
        raw: _Raw,
        path: str,
        service_id: str | None,
        lang: str,
        module_id: str,
        raws: list[_Raw],
    ) -> Node:
        caps = raw.captures

        flags = NodeFlags.NONE
        visibility: Visibility | None = None
        for token in _texts(caps.get("modifier")):
            flags |= _MODIFIER_FLAGS.get(token, NodeFlags.NONE)
        for token in _texts(caps.get("visibility")):
            visibility = _VISIBILITY_TOKENS.get(token, visibility)
            flags |= _MODIFIER_FLAGS.get(token, NodeFlags.NONE)

        signature = None
        if raw.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CONSTRUCTOR,
                        NodeKind.PROPERTY):
            params = sorted(
                (raws[i] for i in raw.children if raws[i].kind is NodeKind.PARAMETER),
                key=lambda p: p.span.start_byte,
            )
            rendered = ", ".join(
                f"{p.name}: {t}" if (t := _capture_text(p.captures, "type")) else p.name
                for p in params
            )
            ret = _capture_text(caps, "return_type")
            signature = f"({rendered})" + (f" -> {ret}" if ret else "")

        return Node(
            id=raw.node_id,
            kind=raw.kind,
            name=raw.name or raw.members[-1],
            qualified_name=raw.qname,
            lang=lang,
            span=raw.span,
            file_path=path,
            service_id=service_id,
            signature=signature,
            signature_hash=raw.sig_hash,
            docstring=_clean_docstring(_capture_text(caps, "docstring")),
            return_type=_capture_text(caps, "return_type"),
            type_ref=_capture_text(caps, "type"),
            visibility=visibility,
            flags=flags,
            type_params=tuple(_texts(caps.get("type_param"))),
            decorators=tuple(_texts(caps.get("decorator"))),
            parent_id=raws[raw.parent].node_id if raw.parent is not None else module_id,
        )

    # ---- reference collection ---------------------------------------------

    def _collect_refs(
        self,
        spec: LanguageSpec,
        dialect: str,
        root: Any,
        path: str,
        lookup: _ContainerLookup,
    ) -> list[UnresolvedRef]:
        query = self._query(spec, dialect, "refs")
        if query is None:
            return []

        refs: list[UnresolvedRef] = []
        for _, captures in self._matches(query, root):
            anchor_name = next((c for c in captures if c.startswith("ref.")
                                and c not in ("ref.name", "ref.arg")), None)
            if anchor_name is None:
                continue
            try:
                ref_kind = RefKind(anchor_name.removeprefix("ref."))
            except ValueError:
                logger.warning(
                    "spec/%s/refs.scm declares unknown ref kind %r; ignoring",
                    spec.lang, anchor_name,
                )
                continue

            anchors = captures[anchor_name]
            if not anchors:
                continue
            span = Span.from_tree_sitter(anchors[0])
            name = _text(captures.get("ref.name"))
            if not name:
                continue

            # Argument spans are retained so the resolver can build PARAM_BINDS
            # once the call target is known (§9). Schema 3 computed 3,326 of
            # these and persisted none, which is why parameter binding could
            # not be built on it.
            #
            # No index is assigned here. tree-sitter binds one @ref.arg per
            # match, so a two-argument call arrives as two matches each holding
            # a single argument -- enumerating within a match would label every
            # argument 0, and PARAM_BINDS would then bind position 0 twice and
            # position 1 never. `_dedupe_refs` assigns indices from source order
            # once all the matches for one call site are merged.
            args = [
                {"text": a.text.decode("utf-8", "replace"),
                 "start_byte": a.start_byte,
                 "end_byte": a.end_byte,
                 "line": a.start_point[0] + 1}
                for a in captures.get("ref.arg", ())
            ]

            refs.append(UnresolvedRef(
                from_node=lookup.innermost(span.start_byte, span.end_byte),
                ref_text=name,
                ref_kind=ref_kind,
                span=span,
                file_path=path,
                receiver_hint=_text(captures.get("receiver")) or None,
                metadata={"args": args} if args else {},
            ))
        return _dedupe_refs(refs)

    def _collect_dataflow(
        self,
        spec: LanguageSpec,
        dialect: str,
        root: Any,
        path: str,
        lookup: _ContainerLookup,
    ) -> list[UnresolvedRef]:
        """Intraprocedural def-use facts, emitted as references (§9).

        Reads and writes are references like any other: the extractor sees a
        name in value position and does not know what it binds to. Modelling
        them as ``UnresolvedRef`` means one resolver handles both, and a
        data-flow target that cannot be resolved lands in the same
        ``unresolved_refs`` accounting as an unresolvable call.
        """
        query = self._query(spec, dialect, "dataflow")
        if query is None:
            return []

        flow_kinds = {"read": RefKind.READ, "write": RefKind.WRITE}
        refs: list[UnresolvedRef] = []
        for _, captures in self._matches(query, root):
            anchor_name = next((c for c in captures if c.startswith("flow.")
                                and c not in ("flow.name", "flow.value")), None)
            if anchor_name is None:
                continue
            verb = anchor_name.removeprefix("flow.")
            ref_kind = flow_kinds.get(verb)
            if ref_kind is None:
                continue

            anchors = captures[anchor_name]
            if not anchors:
                continue
            span = Span.from_tree_sitter(anchors[0])
            name = _text(captures.get("flow.name"))
            if not name:
                continue

            value = _text(captures.get("flow.value"))
            refs.append(UnresolvedRef(
                from_node=lookup.innermost(span.start_byte, span.end_byte),
                ref_text=name,
                ref_kind=ref_kind,
                span=span,
                file_path=path,
                metadata={"value": value} if value else {},
            ))
        return _dedupe_refs(refs)


class _ContainerLookup:
    """Finds the innermost container span holding a byte range.

    Restricted to container kinds, which keeps the candidate set to tens of
    entries per file rather than thousands -- a reference is written inside the
    method that contains it, never inside a sibling parameter.
    """

    __slots__ = ("_fallback", "_spans")

    def __init__(self, spans: list[tuple[Span, str]], *, fallback: str) -> None:
        # Widest first at a shared start, so a later match is always tighter.
        self._spans = sorted(spans, key=lambda s: (s[0].start_byte, -s[0].end_byte))
        self._fallback = fallback

    def innermost(self, start_byte: int, end_byte: int) -> str:
        best: tuple[int, str] | None = None
        for span, node in self._spans:
            if span.start_byte > start_byte:
                break  # sorted by start; nothing further can contain this
            if span.end_byte >= end_byte:
                length = span.byte_length
                if best is None or length < best[0]:
                    best = (length, node)
        return best[1] if best else self._fallback


# ---------------------------------------------------------------------------
# capture helpers
# ---------------------------------------------------------------------------

def _match_sort_key(captures: dict[str, list[Any]]) -> tuple[int, int, str]:
    """Position-major sort key for one match's captures."""
    starts = [n.start_byte for nodes in captures.values() for n in nodes]
    ends = [n.end_byte for nodes in captures.values() for n in nodes]
    if not starts:
        return (0, 0, "")
    return (min(starts), -max(ends), ",".join(sorted(captures)))


def _text(nodes: list[Any] | None) -> str:
    if not nodes:
        return ""
    return nodes[0].text.decode("utf-8", "replace")


def _texts(nodes: list[Any] | None) -> list[str]:
    if not nodes:
        return []
    return [n.text.decode("utf-8", "replace") for n in nodes]


def _capture_text(captures: dict[str, list[Any]], name: str) -> str | None:
    value = _text(captures.get(name))
    return value or None


def _clean_docstring(raw: str | None) -> str | None:
    """Strip quote and comment markers from a captured doc comment."""
    if not raw:
        return None
    text = raw.strip()
    for triple in ('"""', "'''"):
        if text.startswith(triple) and text.endswith(triple) and len(text) >= 6:
            text = text[3:-3]
            break
    else:
        if text.startswith('"') and text.endswith('"') and len(text) >= 2:
            text = text[1:-1]
        elif text.startswith("/**"):
            text = text.removeprefix("/**").removesuffix("*/")
            text = "\n".join(
                line.strip().removeprefix("*").strip() for line in text.splitlines()
            )
        else:
            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                for prefix in ("///", "//!", "//", "#"):
                    if stripped.startswith(prefix):
                        stripped = stripped.removeprefix(prefix).strip()
                        break
                lines.append(stripped)
            text = "\n".join(lines)
    cleaned = text.strip()
    return cleaned or None


def _collect_parse_errors(root: Any) -> list[ParseError]:
    """Walk the tree for ERROR and MISSING nodes.

    Recorded rather than swallowed: a syntax error means part of the file was
    not understood, and every negative answer over that region is unreliable.
    """
    if not root.has_error:
        return []

    found: list[ParseError] = []
    stack = [root]
    while stack and len(found) < 100:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            row, col = node.start_point
            found.append(ParseError(
                line=row + 1, col=col,
                message="missing token" if node.is_missing else "syntax error",
            ))
            continue
        if node.has_error:
            stack.extend(reversed(node.children))
    found.sort(key=lambda e: (e.line, e.col))
    return found
