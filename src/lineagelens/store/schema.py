"""Schema 4 DDL. The single source of truth for a graph.

Two design choices carry the weight here.

**One node table, one edge table.** Data-flow, contract and call edges share
``edges`` rather than living in sibling tables. The moment they are separated,
a path that mixes them becomes inexpressible -- and every question issue #51
exists to answer is a mixed path: "this route reaches this handler (contract),
which calls this service (control), which writes this field (data)". Kinds are
data, so a new language or framework adds enum members and no migration.

**Spans on edges, not just nodes.** An edge records where it is *written* -- the
call site, the assignment, the annotation. That is what makes "what does
changing line 412 affect" answerable without exploding the graph into one node
per statement: the line maps straight onto the operations on it.

Everything an extractor computes is persisted. Schema 3 kept 9 of ~25 symbol
attributes, so 315 entry points, 499 signatures, 590 return descriptors and
3,326 argument lists were recomputed on every index and silently dropped on
write, leaving every richer MCP tool returning empty without an error.
"""

from __future__ import annotations

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- identity and provenance
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS services (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    root          TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- maven|gradle|npm|pypi|go|cargo|dotnet|docker|k8s
    manifest_path TEXT
);

-- The completeness ledger. Every file the walker saw gets a row, including the
-- ones that were skipped: a negative answer is only trustworthy alongside a
-- statement of what was never read.
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    lang         TEXT NOT NULL,
    service_id   TEXT REFERENCES services(id) ON DELETE SET NULL,
    content_hash TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    parse_status TEXT NOT NULL,     -- ok|partial|failed|skipped
    parse_errors TEXT,              -- JSON [{line,col,message}]
    -- generated|vendored|binary|too_large|excluded|missing_grammar
    -- |missing_tier_b|below_required_level
    skip_reason  TEXT,
                                    -- |missing_grammar|missing_tier_b
    extractors   TEXT NOT NULL      -- JSON [{name,version,tier}]
);

CREATE TABLE IF NOT EXISTS nodes (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    lang           TEXT NOT NULL,
    file_id        INTEGER REFERENCES files(id) ON DELETE CASCADE,
    file_path      TEXT NOT NULL,
    service_id     TEXT REFERENCES services(id) ON DELETE SET NULL,
    signature      TEXT,
    signature_hash TEXT NOT NULL DEFAULT '',
    docstring      TEXT,
    return_type    TEXT,
    type_ref       TEXT,
    visibility     TEXT,
    flags          INTEGER NOT NULL DEFAULT 0,
    type_params    TEXT,            -- JSON array
    decorators     TEXT,            -- JSON array
    parent_id      TEXT,            -- denormalised CONTAINS parent, for fast ancestry
    -- byte offsets are authoritative; line/col are the derived display view
    start_byte     INTEGER NOT NULL,
    end_byte       INTEGER NOT NULL,
    start_line     INTEGER NOT NULL,
    start_col      INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    end_col        INTEGER NOT NULL
);

-- How a Tier B oracle's own symbol vocabulary joins to canonical nodes.
-- Replaces hybrid_merger's (file, line, target_name) fuzzy reconciliation,
-- which could only match within one language and silently mismatched when two
-- calls shared a line.
CREATE TABLE IF NOT EXISTS node_aliases (
    node_id          TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    engine           TEXT NOT NULL,  -- scip|jedi|tsserver|javac|gopls|rust-analyzer|roslyn
    engine_symbol_id TEXT NOT NULL,
    PRIMARY KEY (engine, engine_symbol_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS edges (
    id             INTEGER PRIMARY KEY,
    src            TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst            TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    file_id        INTEGER REFERENCES files(id) ON DELETE CASCADE,
    file_path      TEXT,
    evidence_tier  TEXT NOT NULL,   -- fact|heuristic|probabilistic
    evidence_label TEXT NOT NULL,
    confidence     REAL,            -- only for probabilistic
    resolution     TEXT NOT NULL,   -- exact|inferred|ambiguous|external
    provenance     TEXT NOT NULL,
    metadata       TEXT,            -- JSON: arg_index, candidates, contract_key, ...
    start_byte     INTEGER,
    end_byte       INTEGER,
    line           INTEGER,
    col            INTEGER
);

-- References that could not become edges. Retained, never discarded: this is
-- the difference between an honest graph and one that reports resolution=exact
-- on every surviving edge because the failures were deleted.
CREATE TABLE IF NOT EXISTS unresolved_refs (
    id            INTEGER PRIMARY KEY,
    from_node     TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    file_id       INTEGER REFERENCES files(id) ON DELETE CASCADE,
    file_path     TEXT NOT NULL,
    ref_text      TEXT NOT NULL,
    ref_kind      TEXT NOT NULL,
    receiver_hint TEXT,
    candidates    TEXT,             -- JSON array of node ids
    status        TEXT NOT NULL,    -- pending|ambiguous|external|failed
    reason        TEXT,
    metadata      TEXT,
    start_byte    INTEGER NOT NULL,
    end_byte      INTEGER NOT NULL,
    line          INTEGER NOT NULL,
    col           INTEGER NOT NULL
);

-- Where analysis provably stops. Written instead of an edge, and reported by
-- every query that crosses one.
CREATE TABLE IF NOT EXISTS boundaries (
    id         INTEGER PRIMARY KEY,
    node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    detail     TEXT,
    candidates TEXT,                -- JSON array
    line       INTEGER,
    col        INTEGER
);

-- Usage sites: Track how symbols (especially external ones) are used.
-- Enables "find where X is called/imported/used" for agents to understand
-- how external APIs are integrated into this codebase.
CREATE TABLE IF NOT EXISTS usage_sites (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_name        TEXT NOT NULL,          -- "anthropic.Anthropic", "ChatAnthropic"
    usage_type         TEXT NOT NULL,          -- "import", "call", "instantiate", "attribute_access", "type_annotation"
    file_id            INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    file_path          TEXT NOT NULL,
    start_line         INTEGER NOT NULL,
    end_line           INTEGER NOT NULL,
    start_byte         INTEGER NOT NULL,
    end_byte           INTEGER NOT NULL,
    calling_symbol_id  TEXT REFERENCES nodes(id) ON DELETE SET NULL,  -- which local symbol uses it
    context_before     TEXT,                   -- 1-2 lines before for context
    context_line       TEXT NOT NULL,          -- The actual usage line
    context_after      TEXT,                   -- 1-2 lines after for context
    indexed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coverage (
    file_id          INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    nodes_found      INTEGER NOT NULL,
    refs_total       INTEGER NOT NULL,
    refs_exact       INTEGER NOT NULL,
    refs_inferred    INTEGER NOT NULL,
    refs_unresolved  INTEGER NOT NULL,
    boundaries_count INTEGER NOT NULL,
    dataflow_status  TEXT NOT NULL,  -- computed|unsupported
    -- "zero silent drops" as a checked property rather than a claim (§16.5)
    CHECK (refs_total = refs_exact + refs_inferred + refs_unresolved)
);

CREATE TABLE IF NOT EXISTS graph_meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version   INTEGER NOT NULL,
    project_root     TEXT NOT NULL,
    -- Provenance. Deliberately outside `build_digest`: that digest asserts
    -- "same source, same graph", so folding a commit into it would make
    -- `verify --determinism` fail between any two commits with identical
    -- content and destroy the guarantee it exists to provide.
    commit_sha       TEXT,           -- NULL outside a git checkout
    branch           TEXT,           -- NULL when detached or not a checkout
    dirty            INTEGER,        -- 1 = built from a modified tree; NULL = unknown
    build_digest     TEXT,           -- determinism check (§11)
    grammar_digest   TEXT,           -- pinned grammar version set
    spec_digest      TEXT,           -- extraction spec files
    adapter_digest   TEXT,           -- contract adapter specs
    ontology_digest  TEXT,           -- node/edge taxonomy this graph is read against
    -- The §8.2 adapter work list: frameworks present but unmodelled. Its own
    -- column because it is a payload, not a digest. It previously lived inside
    -- `ontology_digest` as JSON, which let two features share one field
    -- without either of them working (#69).
    unclaimed_frameworks TEXT,       -- JSON array
    built_at         TEXT,
    deterministic_ok INTEGER
);

-- Ranked search, replacing an O(n) substring scan that returned the first N
-- matches in dict order with no ranking at all.
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    node_id UNINDEXED,
    name,
    qualified_name,
    docstring,
    signature,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- ---------------------------------------------------------------------------
-- indices
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_nodes_kind      ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name      ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qname     ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_lower     ON nodes(lower(name));
CREATE INDEX IF NOT EXISTS idx_nodes_service   ON nodes(service_id);
CREATE INDEX IF NOT EXISTS idx_nodes_lang      ON nodes(lang);
CREATE INDEX IF NOT EXISTS idx_nodes_parent    ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file      ON nodes(file_id);
-- span lookups: "which nodes contain line N", "which node holds byte offset B"
CREATE INDEX IF NOT EXISTS idx_nodes_file_line ON nodes(file_id, start_line, end_line);
CREATE INDEX IF NOT EXISTS idx_nodes_file_byte ON nodes(file_id, start_byte, end_byte);
-- overload disambiguation
CREATE INDEX IF NOT EXISTS idx_nodes_qname_sig ON nodes(qualified_name, signature_hash);

CREATE INDEX IF NOT EXISTS idx_edges_src_kind  ON edges(src, kind);
CREATE INDEX IF NOT EXISTS idx_edges_dst_kind  ON edges(dst, kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind      ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_tier      ON edges(evidence_tier);
CREATE INDEX IF NOT EXISTS idx_edges_prov      ON edges(provenance);
-- the line-level impact index (§10.4)
CREATE INDEX IF NOT EXISTS idx_edges_file_line ON edges(file_id, line);
CREATE INDEX IF NOT EXISTS idx_edges_file_byte ON edges(file_id, start_byte, end_byte);

-- Two calls to the same target from the same body on different lines are two
-- distinct edges; collapsing them would discard the line precision §10.4 needs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_identity
    ON edges(src, dst, kind, IFNULL(start_byte, -1), IFNULL(end_byte, -1));

CREATE INDEX IF NOT EXISTS idx_unresolved_status ON unresolved_refs(status);
CREATE INDEX IF NOT EXISTS idx_unresolved_from   ON unresolved_refs(from_node);
CREATE INDEX IF NOT EXISTS idx_unresolved_text   ON unresolved_refs(ref_text);
CREATE INDEX IF NOT EXISTS idx_unresolved_file   ON unresolved_refs(file_id);

CREATE INDEX IF NOT EXISTS idx_boundaries_node   ON boundaries(node_id);
CREATE INDEX IF NOT EXISTS idx_boundaries_kind   ON boundaries(kind);

CREATE INDEX IF NOT EXISTS idx_usage_symbol      ON usage_sites(symbol_name);
CREATE INDEX IF NOT EXISTS idx_usage_file        ON usage_sites(file_id);
CREATE INDEX IF NOT EXISTS idx_usage_calling     ON usage_sites(calling_symbol_id);
CREATE INDEX IF NOT EXISTS idx_usage_type        ON usage_sites(usage_type);

CREATE INDEX IF NOT EXISTS idx_aliases_node      ON node_aliases(node_id);
"""

#: Applied on every connection. WAL keeps a reader (an MCP server answering a
#: query) from blocking a writer (a watch-mode reindex), which the schema-3
#: store could not do. ``synchronous=NORMAL`` is safe under WAL and materially
#: faster on the bulk insert path.
CONNECTION_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",  # 256 MiB
    "PRAGMA cache_size = -65536",    # 64 MiB, negative = KiB units
)

#: Recursive CTE traversals can be deep on a hub node; the default limit of 1000
#: is reached by a legitimate 1000-hop closure before any runaway occurs.
RECURSION_LIMIT = 100_000
