# Specification: SCIP & Tree-sitter Hybrid Code Engine

**Status:** Proposed  
**Target Branch:** `feature/scip-tree-sitter-hybrid`  
**Feature Flag:** `analysis.engine` (`compiler` | `tree-sitter` | `scip` | `hybrid`)

---

## 1. Executive Summary & Goal

LineageLens currently uses **compiler-native AST generation engines** (Python `ast`, Java `Eclipse JDT`, JS/TS `TypeScript Compiler API`) for code graph generation. While these yield exact compiler-verified symbol and relation facts, extending dedicated compiler subprocess bridges for every new language (Go, Rust, C/C++, C#, SQL, Kotlin) creates unsustainable engineering overhead, external toolchain requirements for users, and brittleness on broken/uncompiled codebases.

This specification introduces a **Hybrid Architecture** combining:
1. **Tree-sitter**: A universal, zero-dependency, ultra-fast syntactic AST parser for all supported languages.
2. **SCIP (Sourcegraph Code Intelligence Protocol)**: A universal protobuf index reader that ingests compiler-verified facts (`index.scip`) emitted by standard indexers (`scip-java`, `scip-typescript`, `scip-python`, `scip-go`, `scip-clang`, `scip-rust`).

---

## 2. Key Requirements & Architectural Principles

1. **Non-Breaking & Backward Compatible:**
   - The existing compiler-based AST generation (`java_bridge.py`, `js_bridge.py`, native Python `ast`) remains **100% active and untouched** as the default engine.
   
2. **Feature Flag Controlled:**
   - Added `analysis.engine` in `lineagelens.yaml` and `--engine` flag to the CLI (`lineagelens analyze`).
   - Valid options:
     - `compiler` *(default)*: Existing native compiler bridges (JDT, TS Compiler API, Python AST).
     - `tree-sitter`: Fast universal syntactic parsing via Tree-sitter across 50+ languages.
     - `scip`: Direct ingestion of compiler-generated `index.scip` protobuf indices.
     - `hybrid`: Dual-stage parsing (Tree-sitter base graph + SCIP compiler fact enrichment).

3. **Evidence-Labelled Trust Preservation:**
   - Tree-sitter relations are assigned `Evidence(tier="deterministic_fact", label="static_ast")` for syntax or `Evidence(tier="deterministic_heuristic", label="name_match")` for unresolved inter-file calls.
   - SCIP enrichment reconciles with the base graph and upgrades relation evidence to `Evidence(tier="deterministic_fact", label="scip_compiler")`.

---

## 3. Engine Architecture & Component Breakdown

```
                       ┌──────────────────────────────────────────────┐
                       │  User Source Code (Polyglot Repo)            │
                       └──────────────────────┬───────────────────────┘
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │                                                   │
     [Engine: compiler (default)]                          [Engine: hybrid / tree-sitter / scip]
                    │                                                   │
  ┌─────────────────▼──────────────────┐              ┌─────────────────▼──────────────────┐
  │ Legacy Compiler Bridges            │              │ Tree-sitter Universal Engine       │
  │ • Python native ast                │              │ • Zero-dependency fast AST         │
  │ • Java Eclipse JDT                 │              │ • Baseline Symbol & Relation Graph │
  │ • JS/TS TypeScript Compiler API    │              └─────────────────┬──────────────────┘
  └─────────────────┬──────────────────┘                                │
                    │                                     ┌─────────────▼──────────────┐
                    │                                     │ SCIP Ingestor & Merger     │
                    │                                     │ • Read index.scip protobuf │
                    │                                     │ • Upgrade relations to     │
                    │                                     │   scip_compiler facts      │
                    │                                     └─────────────┬──────────────┘
                    │                                                   │
                    └─────────────────────────┬─────────────────────────┘
                                              │
                               ┌──────────────▼──────────────┐
                               │ Final CodeGraph (.json)     │
                               │ • Symbols                   │
                               │ • Containers                │
                               │ • Evidence-Labelled Edges   │
                               └─────────────────────────────┘
```

### Component Details

#### Component 1: Engine Configuration & Feature Flag (`src/lineagelens/config.py`)
Add `engine` to `AnalysisConfig`:
```python
@dataclass
class AnalysisConfig:
    engine: Literal["compiler", "tree-sitter", "scip", "hybrid"] = "compiler"
    scip_index_path: str = "index.scip"
    auto_generate_scip: bool = False
```

#### Component 2: CLI Options (`src/lineagelens/cli.py`)
Expose `--engine` flag on `lineagelens analyze`:
```bash
lineagelens analyze . --engine hybrid
lineagelens analyze . --engine tree-sitter
lineagelens analyze . --engine scip
lineagelens analyze . --engine compiler # Default
```

#### Component 3: Universal Tree-sitter Parser (`src/lineagelens/treesitter_analyzer.py`)
- Uses official `tree-sitter` Python bindings.
- Parses source files into standard `Symbol`, `Container`, and `Relation` dataclasses.
- Supports Python, Java, JS/TS out of the box, with extensible grammar loading for Go, Rust, C/C++, C#, etc.

#### Component 4: SCIP Ingestor (`src/lineagelens/scip_ingestor.py`)
- Reads binary `index.scip` Protobuf format.
- Extracts:
  - `Document` -> `Container`
  - `Symbol` definition occurrence -> `Symbol`
  - `Reference` occurrence -> `Relation` (`CALLS`, `IMPORTS`, `IMPLEMENTS`) with `Evidence(tier="deterministic_fact", label="scip_compiler")`.

#### Component 5: Hybrid Graph Merger (`src/lineagelens/hybrid_merger.py`)
- Merges Tree-sitter syntactic graph with SCIP compiler facts.
- Matches nodes by `(file_path, line_range)`.
- Upgrades `deterministic_heuristic` call edges to `deterministic_fact` when confirmed by SCIP.

---

## 6. Verification & Testing Strategy

1. **Backward Compatibility Tests:**
   - Verify `lineagelens analyze . --engine compiler` produces identical output to the existing analyzer.
   - Run existing Java and JS integration tests (`tests/test_java_integration.py`, `tests/test_js_integration.py`) to confirm zero regression.

2. **Tree-sitter Unit & Integration Tests:**
   - Test AST parsing across Python, Java, and JS/TS files.
   - Verify `Symbol` and `Relation` field population match `model.py` schema v2.

3. **SCIP Ingestion Tests:**
   - Parse sample `index.scip` protobuf fixtures.
   - Verify correct symbol URI parsing, definition-reference matching, and evidence tier assignment (`scip_compiler`).

4. **Hybrid Engine Integration Tests:**
   - Run `--engine hybrid` on sample multi-language project fixtures.
   - Assert heuristic edges from Tree-sitter are successfully upgraded to compiler facts when present in SCIP.
