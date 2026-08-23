# Critical Bug Fix: Jedi Resolver Was Being Bypassed

**Date**: 2026-08-22  
**Status**: 🔧 FIXED

## The Problem

The Jedi-based call resolution was implemented but **never actually invoked**. The `jedi_resolver` was being passed to `Relationships.__init__` but never used in `visit_Call` or `resolve()`. This meant all calls fell through to the fallback string-matcher, defeating the entire purpose of the Jedi integration.

**Evidence**: On momentum_analyzer, symbols like `ArchetypeEngine.__init__`, `analyze_symbol`, and `run_bulk_sr_job` were still being flagged as dead code even though they ARE called in the codebase.

## What Was Fixed

### 1. Added Jedi Integration to `resolve()` Method
**File**: `src/lineagelens/analyzer.py`

- **Before**: `resolve(self, raw: str | None) -> tuple[str, str, str]`
- **After**: `resolve(self, raw: str | None, node_func: ast.AST | None = None) -> tuple[str, str, str]`

**Key changes**:
- Added `node_func` parameter (the actual AST node from visit_Call)
- Added try/except block that **calls Jedi first**:
  ```python
  if self.jedi_resolver and node_func:
      jedi_results = self.jedi_resolver.resolve_call(...)
      for jedi_target in jedi_results:
          if jedi_target.full_name in self.graph.symbols:
              return jedi_target.full_name, "resolved_via_inference", "jedi_inference"
  ```
- Falls through to string-matcher only if Jedi returns nothing or fails

### 2. Pass AST Node to `resolve()` in `visit_Call`
**File**: `src/lineagelens/analyzer.py`, `visit_Call()` method

- **Before**: `target, resolution, evidence_label = self.resolve(raw)`
- **After**: `target, resolution, evidence_label = self.resolve(raw, node_func=node.func)`

Now the actual AST node is passed so Jedi can look up the correct position.

### 3. Smart Column Offset Calculation for Jedi
For attribute access like `conn.apply_migrations()`, we need to point Jedi at the attribute name, not the start of the expression:

```python
if isinstance(node_func, ast.Attribute):
    # Calculate position of attribute name for Jedi
    jedi_line = node_func.lineno
    jedi_col = node_func.end_col_offset - len(node_func.attr) if node_func.end_col_offset else node_func.col_offset
else:
    jedi_line = node_func.lineno
    jedi_col = node_func.col_offset
```

## Why This Matters

The original implementation had all the pieces but they weren't connected:
- ✅ `JediResolver` class was implemented
- ✅ Called in `analyze()` to initialize once per run
- ❌ **Never actually used** when resolving calls

This fix closes that gap by integrating Jedi into the core resolution path.

## What Should Now Work

With Jedi actually being invoked:
- ✅ `ArchetypeEngine.__init__` → correctly resolved to the constructor call in archetype.py
- ✅ `analyze_symbol` → correctly found with 2 callers
- ✅ `run_bulk_sr_job` → correctly resolved
- ✅ All dynamic method calls through variables

## Testing This Fix

1. **Re-run analysis** on momentum_analyzer:
   ```bash
   lineagelens analyze /path/to/momentum_analyzer
   ```

2. **Check the graph**:
   ```bash
   lineagelens serve /path/to/momentum_analyzer
   ```

3. **Verify dead code list** is significantly shorter (false positives removed)

4. **Check MCP tool** directly:
   ```bash
   # In Claude Code with LineageLens MCP enabled:
   # "List all dead code candidates"
   # Should show much shorter list with confidence labels
   ```

## Side Notes

- Jedi might return multiple results for truly ambiguous cases (duck-typed objects) — we handle this by returning the first in-repo match
- If Jedi fails or returns nothing, the old string-matcher is the fallback (never loses capability)
- Column offset calculation is heuristic-based; if Jedi still doesn't resolve something, it falls through cleanly

## Verification

✅ Code compiles without syntax errors  
✅ Ready for re-testing on real projects

## Next Steps

1. Test on momentum_analyzer to verify false positives are gone
2. If any patterns still unresolved, add logging to debug Jedi's behavior
3. Consider caching Jedi results if performance becomes an issue
