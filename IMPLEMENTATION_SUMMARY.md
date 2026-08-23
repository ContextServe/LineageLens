# Graph Readability & Lineage Enhancement — Implementation Complete

**Date**: 2026-08-22  
**Branch**: `fix/graph-readability-and-lineage`  
**Status**: All changes **unstaged and uncommitted** — ready for your testing

## What was done

All four phases implemented to address your graph readability concerns:

### Phase 0 — Git Housekeeping ✅
- Created new branch `fix/graph-readability-and-lineage` carrying the prior `6f7b6f6` fix commit
- Reset `main` branch to `origin/main` (commit `1ca1bbf`)
- **Result**: `main` is clean; all new work is on the feature branch, uncommitted

### Phase 1 — Compound Node Grouping (Root Cause Fix) ✅
**Problem**: Symbols' `parent` fields pointed to Container IDs that didn't exist in the graph, so Cytoscape couldn't group them hierarchically.

**Changes**:
- **Backend** (`src/lineagelens/rest.py`):
  - `graph_view` endpoint now emits **Container nodes** (packages & modules) alongside Symbol nodes
  - Containers respect the existing `module` scoping parameter
  - Defensive cleanup: nulls out any `parent` refs that don't resolve to rendered nodes
- **Frontend** (`frontend/src/graph/CytoscapeGraph.tsx`):
  - Added styling for `node:parent` (container) to display labels as group headers
  - Containers now show module/package names as light box headers

**Verify**: Functions/classes now visually nest inside their file's box; files nest inside package boxes — a proper hierarchy instead of a flat cloud.

### Phase 2 — Test/Source File Separation ✅
**Problem**: Test and source code symbols render indistinguishably mixed together.

**Changes**:
- **Backend** (`src/lineagelens/queries.py`):
  - New `is_test_path(file_or_id, test_roots)` function classifies files based on test_roots config
  - Classification logic mirrors `analyzer.py:222` but works on root-relative paths
- **Backend** (`src/lineagelens/rest.py`):
  - Loads `ProjectConfig` to access `test_roots`
  - Computes `is_test: bool` for every emitted node (Symbol & Container)
  - Added `is_test` field to `NodeView` Pydantic model
- **Frontend** (`frontend/src/App.tsx`):
  - Added `testFilter` state: `'all' | 'source' | 'tests'` (default: `'all'`)
  - New `getFilteredGraphData()` function filters nodes/edges and cleans up parent refs
  - Filters passed to graph component
- **Frontend** (`frontend/src/components/Toolbar.tsx`):
  - Added 3-way segmented control: "All files" / "Source only" / "Tests only"
  - New `Toolbar.css` with styling for buttons and segmented control
  - Added color legend showing all four node classifications

**Verify**: Toggling "Source only" hides all `tests/` symbols; "Tests only" shows just those; "All files" restores everything.

### Phase 3 — Full Lineage Highlighting ✅
**Problem**: Clicking a node only highlighted immediate callers/callees (1 hop), not the full chain.

**Changes**:
- **Backend** (no changes needed):
  - Existing `/api/v1/symbols/{id}/lineage?direction=...&max_depth=...` endpoint already does multi-hop traversal via `queries.get_lineage()`
- **Frontend** (`frontend/src/graph/CytoscapeGraph.tsx`):
  - Node tap handler now calls `/lineage?direction=backward&max_depth=9999` and `/lineage?direction=forward&max_depth=9999`
  - Unions the returned `symbol_id`s to build a `reachableIds` set
  - Passes this set to the highlight function
- **Frontend** (`frontend/src/graph/highlight.ts`):
  - Renamed `highlightFlow()` → `highlightLineage(cy, reachableIds: Set<string>)`
  - Generalized logic: highlights every node in the set, and every edge whose both endpoints are in the set
  - Replaces the prior "edge must touch the tapped node directly" check

**Verify**: Clicking a nested function highlights the entire chain from its originating entry point (API route, CLI command) all the way to its leaf callees.

### Phase 4 — Dead Code & Duplicate Detection ✅
**Problem**: No visual signal for unreferenced or duplicate-named symbols.

**Changes**:
- **Backend** (`src/lineagelens/queries.py`):
  - New `list_unreferenced_symbols(graph)` function:
    - Finds symbols with zero incoming relations (calls)
    - **Excludes entry points** (API routes, CLI commands, tests, framework callbacks) — they are externally invoked by design
    - Returns candidates for dead code
  - New `find_duplicate_names(graph)` function:
    - Groups symbols by `(kind, name)` tuple
    - Returns set of names appearing more than once (per kind)
    - Heuristic-based: "same name in multiple places," not byte-hash duplicates
- **Backend** (`src/lineagelens/rest.py`):
  - `graph_view` endpoint precomputes both sets for efficiency
  - Computes `possibly_dead: bool` and `duplicate_name: bool` for every node
  - Added both fields to `NodeView` Pydantic model
- **Frontend** (`frontend/src/App.tsx`):
  - Extended `GraphViewData.nodes` type with the two new boolean fields
- **Frontend** (`frontend/src/graph/CytoscapeGraph.tsx`):
  - Extended `background-color` priority logic: entry_point (blue) > has_risk (orange) > possibly_dead (red) > default (gray)
  - Added border styling for `duplicate_name`: purple border, independent of fill color
  - Color logic ensures entry points are always blue, never red (even if unreferenced)
- **Frontend** (`frontend/src/components/Toolbar.tsx`):
  - Added color legend showing: Entry Point (blue), Risk (orange), Possibly Dead (red), Duplicate Name (purple border)

**Verify**: 
- A helper function with zero callers (not an entry point) renders red
- Two functions sharing a name in different files both show the duplicate-name border
- An entry point with zero callers stays blue (not falsely flagged red)

## Files Modified

### Backend (Python)
- `src/lineagelens/queries.py` — Added `is_test_path()`, `list_unreferenced_symbols()`, `find_duplicate_names()`
- `src/lineagelens/rest.py` — Updated `NodeView` model, modified `graph_view` endpoint to emit containers & compute flags

### Frontend (React/TypeScript)
- `frontend/src/App.tsx` — Added `testFilter` state, `getFilteredGraphData()` function
- `frontend/src/components/Toolbar.tsx` — Added filter buttons, legend, props
- `frontend/src/components/Toolbar.css` — Styling for segmented control and legend
- `frontend/src/graph/CytoscapeGraph.tsx` — Added node field passthrough, extended color logic, switched to full-lineage fetch
- `frontend/src/graph/highlight.ts` — Replaced `highlightFlow()` with `highlightLineage()`, generalized edge matching

## Testing Checklist

Before committing, verify each of these:

1. **Git state correct**:
   ```bash
   git log --oneline -3          # Should show 6f7b6f6 at tip of this branch
   git log main --oneline -1     # Should show 1ca1bbf (origin/main)
   git status                    # Should show changes unstaged (not committed)
   ```

2. **Build frontend**:
   ```bash
   cd frontend && npm install && npm run build
   ```

3. **Analyze a real codebase**:
   ```bash
   lineagelens analyze /path/to/momentum_analyzer
   lineagelens serve /path/to/momentum_analyzer
   ```
   - Graph should load without errors
   - UI should not show blank page

4. **Phase 1 verification** — Compound grouping:
   - Navigate the graph
   - **Expected**: Functions/classes visually nest inside their module boxes; modules nest inside package boxes
   - **Not just a flat cloud anymore** ✓

5. **Phase 2 verification** — Test/source toggle:
   - Click "Source only" button in toolbar
   - **Expected**: All `tests/` nodes disappear; edge count drops
   - Click "Tests only"
   - **Expected**: Only test nodes visible
   - Click "All files"
   - **Expected**: Everything returns

6. **Phase 3 verification** — Full lineage highlighting:
   - Click a deeply nested function (not an entry point)
   - **Expected**: Highlighting traces back through all intermediate functions to the originating entry point (API route, CLI, etc.), then forward to all leaf callees
   - **Not just immediate neighbors** ✓

7. **Phase 4 verification** — Color coding:
   - Look for nodes colored red (possibly dead code)
   - **Expected**: These are helper functions with zero callers (not entry points)
   - Click an API route (should be blue)
   - **Expected**: Blue color, never red (even if it has no in-repo callers)
   - Look for nodes with purple borders (duplicate names)
   - **Expected**: Multiple functions/methods with the same name in different modules

8. **Backend tests** (once you're in the proper Python environment):
   ```bash
   pytest tests/test_queries.py -xvs
   ```
   - All tests should pass (existing tests unchanged, new functions included)

## Key Design Decisions

1. **Server-side test classification**: `is_test_path()` logic lives in `queries.py` (single source of truth), not duplicated client-side. `NodeView` DTO carries the derived `is_test: bool` to the frontend.

2. **Defensive parent cleanup**: Nodes' `parent` refs are nulled if the parent doesn't exist in the rendered set. This prevents Cytoscape silently failing to group (same class of bug as the edge-target fix from `6f7b6f6`).

3. **Entry point protection in dead code detection**: `list_unreferenced_symbols()` explicitly excludes entry points because API routes, CLI commands, framework callbacks, and test functions are *designed* to have zero in-repo callers. Without this exclusion, 100% of entry points would be falsely flagged.

4. **Full transitive lineage**: The `/lineage` endpoint with `max_depth=9999` captures the unbounded transitive closure. `direction=both` is split into two calls (`backward` + `forward`) to ensure all reachable nodes are included.

5. **Duplicate detection is name-based**: Without a body-hash field on `Symbol`, duplicates are detected by `(kind, name)` collisions. This catches copy-paste and naming conflicts; it's labeled as such in the legend.

## What's NOT Committed

- ✅ All changes are **unstaged** (green in `git status`)
- ✅ No commits made to either branch
- ✅ `main` branch has been reset and is ready to sync with `origin/main`
- ✅ Feature branch carries the prior fix commit + all new work, uncommitted

Once you've tested and are satisfied, you can:
```bash
git add -A
git commit -m "feat: graph readability — compound grouping, lineage highlighting, test/source split, dead-code coloring"
git push origin fix/graph-readability-and-lineage
# Then open a PR
```

Or if changes are needed, edit and test further — git is clean and ready for iteration.

---

**Ready for your testing!** Let me know what you find.
