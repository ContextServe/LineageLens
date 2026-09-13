"""The graph vocabulary: node kinds, edge kinds, and the labels attached to them.

Kinds are *data*, not schema (spec issue #51 §5.1). Adding a language or a
framework must never require a schema migration or a change to the query layer,
so every new concept arrives as a new member here and nothing else moves.

The enums are ``str``-mixed on purpose: they round-trip through SQLite as plain
TEXT with no adapter, and comparisons against raw strings from a query still
work. That keeps the store readable with the ``sqlite3`` CLI, which matters when
debugging an index.
"""

from __future__ import annotations

from enum import Enum, IntFlag


class NodeKind(str, Enum):
    """Every kind of thing the graph can hold.

    Schema 3 had four (``class``, ``function``, ``method``, ``module_scope``),
    which is why fields, interfaces, enums and constants were unrepresentable and
    ``list_fields_by_type`` could never return anything for Python.
    """

    # containment
    SERVICE = "service"       # deployment unit (§7.4)
    PACKAGE = "package"
    MODULE = "module"
    FILE = "file"

    # types
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    ENUM_MEMBER = "enum_member"
    STRUCT = "struct"         # Go/Rust/C#
    TRAIT = "trait"           # Rust; Go interfaces use INTERFACE
    TYPE_ALIAS = "type_alias"
    ANNOTATION = "annotation"  # Java @interface, C# attribute class

    # callables
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    PROPERTY = "property"

    # values
    FIELD = "field"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    CONSTANT = "constant"

    # cross-framework binding (§8.1)
    CONTRACT = "contract"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class EdgeKind(str, Enum):
    """Every kind of relationship.

    Data-flow, contract and call edges deliberately share one table so a single
    path query can mix them; see the module docstring of ``store.schema``.
    """

    # structural
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    OVERRIDES = "OVERRIDES"
    DECORATES = "DECORATES"
    HAS_TYPE = "HAS_TYPE"
    INSTANTIATES = "INSTANTIATES"
    THROWS = "THROWS"

    # control
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"

    # data (§9)
    READS = "READS"
    WRITES = "WRITES"
    PARAM_BINDS = "PARAM_BINDS"
    RETURNS = "RETURNS"
    FLOWS_TO = "FLOWS_TO"  # transitive closure over the four above; query-time only

    # contract (§8)
    EXPOSES = "EXPOSES"
    CONSUMES = "CONSUMES"

    # test
    USES_FIXTURE = "USES_FIXTURE"
    TESTS = "TESTS"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Named groups, so callers say ``kinds=DATA_EDGES`` instead of spelling out five
# members and drifting from this list when a sixth arrives.
STRUCTURAL_EDGES = frozenset({
    EdgeKind.CONTAINS, EdgeKind.IMPORTS, EdgeKind.EXPORTS, EdgeKind.INHERITS,
    EdgeKind.IMPLEMENTS, EdgeKind.OVERRIDES, EdgeKind.DECORATES,
    EdgeKind.HAS_TYPE, EdgeKind.INSTANTIATES, EdgeKind.THROWS,
})
CONTROL_EDGES = frozenset({EdgeKind.CALLS, EdgeKind.REFERENCES})
DATA_EDGES = frozenset({
    EdgeKind.READS, EdgeKind.WRITES, EdgeKind.PARAM_BINDS,
    EdgeKind.RETURNS, EdgeKind.FLOWS_TO,
})
CONTRACT_EDGES = frozenset({EdgeKind.EXPOSES, EdgeKind.CONSUMES})
TEST_EDGES = frozenset({EdgeKind.USES_FIXTURE, EdgeKind.TESTS})

#: Edges a caller-chain walk follows. Excludes CONTAINS, which would let a walk
#: escape through a shared parent and reach the entire module.
CALL_CHAIN_EDGES = frozenset({
    EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.OVERRIDES, EdgeKind.IMPLEMENTS,
})

#: Materialised in the store. FLOWS_TO is computed per query, never written.
PERSISTED_EDGES = frozenset(EdgeKind) - {EdgeKind.FLOWS_TO}


class EvidenceTier(str, Enum):
    """How much weight an edge carries.

    ``FACT`` requires either literal syntax or a Tier B type resolver. A name
    match is never a fact -- that conflation is what let schema 3 report
    ``resolution=resolved`` on 2,593 Dubbo edges, none of which were traversable.
    """

    FACT = "fact"
    HEURISTIC = "heuristic"
    PROBABILISTIC = "probabilistic"  # opt-in only; never produced by extraction

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Resolution(str, Enum):
    """How a reference came to point at its target."""

    EXACT = "exact"          # Tier B type resolver, or unambiguous literal syntax
    INFERRED = "inferred"    # deterministic inference, single candidate
    AMBIGUOUS = "ambiguous"  # several candidates; all retained in metadata
    EXTERNAL = "external"    # resolves outside the indexed repo

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class BoundaryKind(str, Enum):
    """Why analysis provably stopped here (§9.1).

    A boundary is recorded *instead of* an edge. Every query reports the
    boundaries it crossed, which is what makes an incomplete answer trustworthy
    rather than misleading.
    """

    DYNAMIC_DISPATCH = "dynamic_dispatch"
    REFLECTION = "reflection"
    ALIAS = "alias"
    CONFIG = "config"
    DYNAMIC_CONTRACT_KEY = "dynamic_contract_key"
    MISSING_GRAMMAR = "missing_grammar"
    MISSING_TIER_B = "missing_tier_b"
    PARSE_ERROR = "parse_error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RefKind(str, Enum):
    """What an extractor observed, before anything resolved it (§7.1)."""

    CALL = "call"
    TYPE = "type"
    READ = "read"
    WRITE = "write"
    IMPORT = "import"
    INHERIT = "inherit"
    IMPLEMENT = "implement"
    DECORATE = "decorate"
    INSTANTIATE = "instantiate"
    THROW = "throw"
    CONTRACT_KEY = "contract_key"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RefStatus(str, Enum):
    """Terminal state of a reference the resolver could not turn into an edge."""

    PENDING = "pending"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
    FAILED = "failed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ParseStatus(str, Enum):
    """Per-file extraction outcome, for the completeness ledger (§10.6)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class SkipReason(str, Enum):
    """Why a file was not extracted.

    ``MISSING_GRAMMAR`` and ``MISSING_TIER_B`` are the honest terminal states
    that replace schema 3's silent fallback to a regex line scanner.
    """

    GENERATED = "generated"
    VENDORED = "vendored"
    BINARY = "binary"
    TOO_LARGE = "too_large"
    EXCLUDED = "excluded"
    MISSING_GRAMMAR = "missing_grammar"
    MISSING_TIER_B = "missing_tier_b"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ContractKind(str, Enum):
    """Namespace a contract key lives in (§8).

    Two sides join only within the same kind, so an HTTP route named ``orders``
    never collides with a Kafka topic named ``orders``.
    """

    HTTP_ROUTE = "http_route"
    RPC_SERVICE = "rpc_service"
    TOPIC = "topic"
    TABLE = "table"
    ENV = "env"
    FLAG = "flag"
    CLI = "cli"
    SPI = "spi"
    GRAPHQL = "graphql"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Intent(str, Enum):
    """Query precision, scoped to the task (§10.1).

    A bug fix needs line-exact detail; a feature plan does not. This is the
    primary cost control in the system: savings come from not computing detail
    the question does not need, rather than truncating detail it does.
    """

    PRECISE = "precise"
    PLAN = "plan"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class DataflowStatus(str, Enum):
    """Whether a file's data-flow edges exist.

    §9.2 specified three computation modes -- lazy, eager, incremental -- and
    made lazy the default. That is gone, and data flow is now always computed.
    Two reasons, both measured:

    * lazy's premise does not hold. It was justified on "most queries touch a
      small slice", but resolution is *global*: resolving one data-flow
      reference needs the whole symbol index, which is most of the cost. The
      saving was not available.
    * the cost it avoided is small. Computing everything adds 3.2s on this
      repository and 10s on Apache Dubbo's 4,046 files, while lazy dropped 69%
      of the data-flow edges -- for a tool whose headline capability is data
      flow.

    So the only honest states left are "computed" and "this language has no
    data-flow spec".
    """

    COMPUTED = "computed"
    UNSUPPORTED = "unsupported"  # no dataflow.scm for this language

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Tier(str, Enum):
    """Which extraction tier produced a fact (§7)."""

    A = "A"  # declarative tree-sitter spec
    B = "B"  # native type resolver (jedi/tsserver/javac/gopls/...)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class NodeFlags(IntFlag):
    """Boolean node properties, packed into one INTEGER column (§5.2).

    A bitfield rather than 10 columns because these are almost always read as a
    set and written once. ENTRY_POINT is derived: it mirrors the existence of an
    EXPOSES edge, and exists so the hot "is this an entry point" check does not
    need a join.
    """

    NONE = 0
    ASYNC = 1
    STATIC = 2
    ABSTRACT = 4
    FINAL = 8
    EXPORTED = 16
    GENERATED = 32
    TEST = 64
    DEPRECATED = 128
    ENTRY_POINT = 256
    OVERRIDE = 512


class Visibility(str, Enum):
    """Declared access level, normalised across languages."""

    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"
    PACKAGE = "package"    # Java default
    INTERNAL = "internal"  # C#, Rust crate-local

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value
