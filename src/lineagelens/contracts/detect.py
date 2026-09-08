"""Applies adapters to observations and produces contract nodes plus edges.

This is where §8 becomes real. The output is deliberately shaped like the
resolver's: contracts (which are nodes), ``EXPOSES``/``CONSUMES`` edges, and
boundaries for keys that could not be determined statically.

Two things happen here that the adapters cannot do alone:

* **Type-keyed contracts.** Dubbo's ``@DubboReference private Greeting greeting;``
  names its contract by the *annotated field's type*, not by a string argument.
  The annotation observation does not carry that type, so it is recovered by
  finding the innermost value node whose span contains the annotation.
* **Resource-file contracts.** ``META-INF/services/<interface>`` is a provider
  declaration written in a file with no grammar at all -- the filename is the
  key and each line is an implementation. ``ontology.py`` listed this as a
  permanent limitation; it is a contract join.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core import (
    Boundary,
    BoundaryKind,
    Contract,
    ContractKind,
    Edge,
    EdgeKind,
    Evidence,
    EvidenceTier,
    Node,
    NodeKind,
    Observation,
    RefKind,
    Resolution,
    Span,
    UnresolvedRef,
    contract_id,
)
from .adapters import AdapterRegistry
from .normalise import normalise_fqn

logger = logging.getLogger(__name__)

#: Where Java's ServiceLoader and Dubbo's SPI look for provider declarations.
SPI_GLOBS = (
    "**/META-INF/services/*",
    "**/META-INF/dubbo/*",
    "**/META-INF/dubbo/internal/*",
)

#: Node kinds whose declared type can key a contract.
_TYPED_KINDS = frozenset({NodeKind.FIELD, NodeKind.PARAMETER, NodeKind.VARIABLE})

#: Node kinds that can themselves be a contract, or implement one.
TYPE_OWNER_KINDS = frozenset({
    NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.STRUCT, NodeKind.TRAIT,
    NodeKind.ENUM,
})


@dataclass(frozen=True, slots=True)
class UnclaimedDeclaration:
    """A framework-shaped declaration that no adapter recognised.

    The answer to "does completeness now depend on someone having written a
    YAML file for my framework?". Without this, an unknown framework is an
    *invisible* gap -- the exact failure the rearchitecture exists to remove.
    With it, the gap is a ranked, queryable list: "47 uses of ``@MyRoute`` with
    a route-shaped argument, no adapter", which is both an honest coverage
    statement and a work list.
    """

    lang: str
    name: str
    #: What made it look like a framework declaration, for triage.
    signal: str
    file_path: str
    line: int
    sample_key: str = ""


@dataclass(slots=True)
class ContractResult:
    """Contracts, the edges attaching symbols to them, and what was missed."""

    contracts: dict[str, Contract] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    unclaimed: list[UnclaimedDeclaration] = field(default_factory=list)

    def merge(self, other: ContractResult) -> None:
        # Contracts dedupe by id by construction: many files declaring the same
        # route must converge on one node, which is the entire point of §8.1.
        self.contracts.update(other.contracts)
        self.edges.extend(other.edges)
        self.boundaries.extend(other.boundaries)
        self.unclaimed.extend(other.unclaimed)

    def unclaimed_summary(self) -> list[dict[str, object]]:
        """Unclaimed declarations grouped by ``(lang, name)``, commonest first.

        This is what the capability matrix (§12) and the completeness envelope
        (§10.6) report, so an agent can see that a repository contains a
        framework LineageLens does not model -- rather than concluding from an
        empty ``contract_map`` that the services are not connected.
        """
        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for item in self.unclaimed:
            key = (item.lang, item.name)
            entry = grouped.setdefault(key, {
                "lang": item.lang, "name": item.name, "signal": item.signal,
                "uses": 0, "example": f"{item.file_path}:{item.line}",
                "sample_key": item.sample_key,
            })
            entry["uses"] = int(entry["uses"]) + 1
        return sorted(
            grouped.values(), key=lambda e: (-int(e["uses"]), str(e["name"]))
        )


def detect_in_observation(
    observation: Observation,
    registry: AdapterRegistry,
) -> ContractResult:
    """Find every contract declared by one file."""
    result = ContractResult()
    lang = observation.file.lang

    if not registry.adapters_for(lang):
        return result

    typed_nodes = sorted(
        (n for n in observation.nodes if n.kind in _TYPED_KINDS and n.type_ref),
        key=lambda n: (n.span.start_byte, -n.span.end_byte),
    )

    types = sorted(
        (n for n in observation.nodes if n.kind in TYPE_OWNER_KINDS),
        key=lambda n: (n.span.start_byte, -n.span.end_byte),
    )
    implemented = [
        r for r in observation.refs
        if r.ref_kind in (RefKind.IMPLEMENT, RefKind.INHERIT)
    ]

    for ref in observation.refs:
        enriched = ref
        # Recover the key a `key_from_type` adapter needs before matching.
        declared = _contract_key_source(ref, typed_nodes, types, implemented)
        if declared and "declared_type" not in ref.metadata:
            enriched = _with_declared_type(ref, declared)

        match = registry.apply(enriched, lang)
        if match is None:
            unclaimed = _looks_like_a_declaration(enriched, lang)
            if unclaimed is not None:
                result.unclaimed.append(unclaimed)
            continue

        contract = match.contract
        result.contracts[contract.id] = contract

        if match.normalised.dynamic:
            # A key assembled at runtime has no static join. The partial prefix
            # is retained as a lead: it still narrows a manual search, which is
            # strictly more useful than dropping the observation.
            result.boundaries.append(Boundary(
                node_id=ref.from_node,
                kind=BoundaryKind.DYNAMIC_CONTRACT_KEY,
                detail=(
                    f"{match.adapter.id}: key built at runtime; "
                    f"resolvable prefix {match.normalised.prefix!r}"
                ),
                span=ref.span,
            ))

        result.edges.append(Edge(
            src=ref.from_node,
            dst=contract.id,
            kind=match.edge_kind,
            # A contract join is a name match on a normalised string, never a
            # syntactic fact -- so it is heuristic even when both sides are
            # unambiguous, and can be filtered out independently of call edges.
            evidence=Evidence(
                tier=EvidenceTier.HEURISTIC,
                label=match.adapter.evidence,
            ),
            resolution=Resolution.INFERRED,
            provenance=f"adapter:{match.adapter.id}",
            span=ref.span,
            file_path=observation.file.path,
            metadata={
                "contract_kind": contract.kind.value,
                "contract_key": contract.normalised_key,
                "written_as": contract.key,
            },
        ))

    return result


def detect_spi_resources(
    project_root: Path,
    nodes_by_qualified_name: dict[str, list[Node]],
) -> ContractResult:
    """Read ``META-INF/services/*`` and Dubbo SPI files.

    The filename is the service interface; each non-comment line names an
    implementation (Dubbo uses ``alias=FQN``). Neither side is visible to any
    parser, which is why ``ontology.py`` recorded this as permanently invisible.

    Implementations that are not in the index are skipped rather than
    fabricated -- a provider from a third-party jar is genuinely outside the
    repository.
    """
    result = ContractResult()

    seen: set[Path] = set()
    for glob in SPI_GLOBS:
        for path in sorted(project_root.glob(glob)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            interface = normalise_fqn(path.name)
            if not interface.key or "." not in interface.key:
                continue  # not an FQN-named file; not an SPI registry

            contract = Contract(
                id=contract_id(ContractKind.SPI.value, interface.key),
                kind=ContractKind.SPI,
                key=path.name,
                normalised_key=interface.key,
                metadata={"registry": str(path.relative_to(project_root))},
            )
            result.contracts[contract.id] = contract

            try:
                lines = path.read_text("utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.debug("cannot read SPI registry %s: %s", path, exc)
                continue

            rel = path.relative_to(project_root).as_posix()
            for number, line in enumerate(lines, start=1):
                entry = line.split("#", 1)[0].strip()
                if not entry:
                    continue
                # Dubbo writes `alias=com.example.Impl`; plain ServiceLoader
                # writes the FQN alone.
                _, _, fqn = entry.rpartition("=")
                impl = normalise_fqn(fqn or entry).key
                if not impl:
                    continue

                targets = _lookup_by_fqn(impl, nodes_by_qualified_name)
                if not targets:
                    result.boundaries.append(Boundary(
                        node_id=contract.id,
                        kind=BoundaryKind.CONFIG,
                        detail=f"{rel}:{number} names {impl}, which is not in the index",
                    ))
                    continue
                if len(targets) > 1:
                    result.boundaries.append(Boundary(
                        node_id=contract.id,
                        kind=BoundaryKind.CONFIG,
                        detail=f"{rel}:{number} {impl} matches {len(targets)} nodes",
                        candidates=tuple(sorted(n.id for n in targets)),
                    ))
                    continue

                result.edges.append(Edge(
                    src=targets[0].id,
                    dst=contract.id,
                    kind=EdgeKind.EXPOSES,
                    evidence=Evidence(
                        tier=EvidenceTier.FACT,
                        label="spi_registry_entry",
                    ),
                    resolution=Resolution.EXACT,
                    provenance="adapter:java.spi",
                    span=Span.at_line(number),
                    file_path=rel,
                    metadata={
                        "contract_kind": ContractKind.SPI.value,
                        "contract_key": interface.key,
                        "registry_line": number,
                    },
                ))

    return result


#: A string argument shaped like a route, topic, or queue name. Deliberately
#: narrow: an annotation carrying an arbitrary string is not evidence of a
#: framework boundary, but one carrying a slash-path or a dotted topic is.
_KEYISH = re.compile(
    r"""^["'`]\s*(?:
        /[\w\-./:{}<>*$%]*          # /api/users/{id}
      | [a-z][\w-]*(?:\.[\w*-]+)+   # orders.created, my.topic.*
      | [A-Z][A-Z0-9_]{2,}          # SOME_ENV_VAR
    )\s*["'`]$""",
    re.VERBOSE,
)


def _looks_like_a_declaration(ref: UnresolvedRef, lang: str) -> UnclaimedDeclaration | None:
    """Does this observation look like a framework binding no adapter claimed?

    Only decorators and annotations are considered, and only when they carry a
    key-shaped string literal. A bare ``@override`` or ``@dataclass`` is not a
    contract and must not be reported, or the list becomes noise and stops
    being read.
    """
    if ref.ref_kind is not RefKind.DECORATE:
        return None

    args = ref.metadata.get("args") or ()
    for arg in args:
        text = str(arg.get("text", ""))
        if _KEYISH.match(text.strip()):
            return UnclaimedDeclaration(
                lang=lang,
                name=ref.ref_text,
                signal="decorator with a route/topic-shaped argument",
                file_path=ref.file_path,
                line=ref.span.start_line,
                sample_key=text.strip(),
            )
    return None


def _contract_key_source(
    ref: UnresolvedRef,
    typed_nodes: list[Node],
    types: list[Node],
    implemented: list[UnresolvedRef],
) -> str | None:
    """The key a type-keyed annotation declares, or ``None``.

    Three sources, most specific first, because the same annotation style keys
    off different things depending on what it is attached to:

    1. **The annotated declaration's type.** ``@DubboReference private
       GreetingService greeting;`` -- the consumer names the contract by the
       field's interface.
    2. **The implemented interface.** ``@DubboService class GreetingServiceImpl
       implements GreetingService`` -- the *provider* names it by what it
       implements, not by its own class name. Getting this wrong is exactly why
       provider and consumer failed to join: the consumer keyed on
       ``GreetingService`` and the provider on nothing at all.
    3. **The annotated type's own name.** Spring's ``@Component class Foo`` with
       no interface is its own contract.
    """
    # 1. innermost typed value declaration containing the annotation
    best: tuple[int, str] | None = None
    for node in typed_nodes:
        if node.span.start_byte > ref.span.start_byte:
            break  # sorted by start; nothing later can contain this
        if node.span.end_byte >= ref.span.end_byte and node.type_ref:
            length = node.span.byte_length
            if best is None or length < best[0]:
                best = (length, node.type_ref)
    if best is not None:
        return best[1]

    # 2/3. the enclosing type: prefer what it implements, else its own name
    owner = _innermost_containing(ref.span, types)
    if owner is None:
        return None
    for candidate in implemented:
        if owner.span.contains(candidate.span):
            return candidate.ref_text
    return owner.name


def _innermost_containing(span: Span, nodes: list[Node]) -> Node | None:
    best: tuple[int, Node] | None = None
    for node in nodes:
        if node.span.start_byte > span.start_byte:
            break
        if node.span.end_byte >= span.end_byte:
            length = node.span.byte_length
            if best is None or length < best[0]:
                best = (length, node)
    return best[1] if best else None


def _with_declared_type(ref, declared: str):
    from dataclasses import replace

    metadata = dict(ref.metadata)
    metadata["declared_type"] = declared
    return replace(ref, metadata=metadata)


def _lookup_by_fqn(
    fqn: str, nodes_by_qualified_name: dict[str, list[Node]]
) -> list[Node]:
    """Find nodes for a Java FQN.

    The index keys on the canonical qualified name (§6), which is
    ``service/lang/module#member`` -- not a Java package path. So the FQN's
    trailing type name is matched against the module tail, which is what the
    package path becomes after normalisation.
    """
    tail = fqn.rsplit(".", 1)[-1]
    matches: list[Node] = []
    package_path = fqn.replace(".", "/")
    for qname, nodes in nodes_by_qualified_name.items():
        if not qname.endswith(f"#{tail}"):
            continue
        if package_path.rsplit("/", 1)[0] in qname or f"/{tail}#{tail}" in qname:
            matches.extend(n for n in nodes if n.is_type)
    if not matches:
        for qname, nodes in nodes_by_qualified_name.items():
            if qname.endswith(f"#{tail}"):
                matches.extend(n for n in nodes if n.is_type)
    return matches
