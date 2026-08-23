# Debugging: Why Jedi Resolution Still Shows False Positives

**Status**: Investigating why `ArchetypeEngine.__init__` and `run_bulk_sr_job` still show 0 callers

## The Core Issue

The Jedi integration is now active, but it's still not matching our symbol IDs. Three possible reasons:

### 1. **Module Path Mismatch** (Most Likely)

Our symbol IDs are built from file paths like:
- File: `v2/services/archetype.py`
- Module name: `v2.services.archetype`
- Symbol ID: `v2.services.archetype.ArchetypeEngine`

But Jedi might return:
- Full name: `archetype.ArchetypeEngine` (just the module basename, not the full path)
- Or: `momentum_analyzer.v2.services.archetype.ArchetypeEngine` (with project name prefix)

**Fix implemented**: We now try suffix matching to account for this, but it might not be comprehensive enough.

### 2. **Constructor Call Resolution**

For `ArchetypeEngine()`:
- Jedi's `goto()` at a class instantiation site resolves to the **class**, not `__init__`
- We need to look for the `__init__` method of that class

**Fix implemented**: Added special case handling for when Jedi returns `type == "class"`

### 3. **Symbol Not in Graph at All**

The symbol `ArchetypeEngine.__init__` might not be in our graph if:
- The `__init__` method is not explicitly defined (uses inherited `__init__`)
- Or it's defined but not captured during the Definitions pass

**Check**: Look at the `.lineagelens/graph.json` and grep for `ArchetypeEngine.__init__`

## How to Debug This

### Step 1: Enable Logging
Edit `src/lineagelens/analyzer.py` and set:
```python
logging.basicConfig(level=logging.DEBUG)  # was logging.INFO
```

Then re-run:
```bash
lineagelens analyze /path/to/momentum_analyzer 2>&1 | grep -i jedi
```

Look for lines like:
- `Jedi resolved X to Y` — means Jedi is working and finding matches
- `Jedi resolution failed` — means Jedi is erroring out
- No Jedi output — means Jedi is returning empty results

### Step 2: Check the Graph
```bash
# See if ArchetypeEngine.__init__ is in the graph
jq '.symbols | map(select(.id | contains("ArchetypeEngine")))' .lineagelens/graph.json

# See all symbols in the archetype module
jq '.symbols | map(select(.module | contains("archetype"))) | .[].id' .lineagelens/graph.json
```

### Step 3: Check Relations
```bash
# See if there are ANY relations pointing to __init__
jq '.relations | map(select(.target | contains("__init__")))' .lineagelens/graph.json | head -20

# See if there are unresolved relations
jq '.relations | map(select(.target | startswith("external:"))) | .[0:5]' .lineagelens/graph.json
```

### Step 4: Manually Test Jedi
```python
import jedi

project = jedi.Project(path="/path/to/momentum_analyzer")
# Find the call site of ArchetypeEngine()
script = jedi.Script(path="/path/to/file_with_call.py", project=project)
# Try to goto that line/column
results = script.goto(line=N, column=C, follow_imports=True)
for r in results:
    print(f"Full name: {r.full_name}, Type: {r.type}, File: {r.module_path}")
```

## Most Likely Root Cause

I suspect the issue is the **module path mismatch**. Our symbol IDs include the full path from the project root (`v2.services.archetype.ClassName`), but Jedi might return just the module name part.

Example:
- We have: `v2.services.archetype.ArchetypeEngine`
- Jedi returns: `archetype.ArchetypeEngine` or `archetype.ArchetypeEngine.__init__`
- Our suffix matching should catch this with `endswith("." + jedi_full_name)`, but it might not be working

## Quick Improvements to Try

### 1. More Aggressive Suffix Matching
Instead of just suffix matching, try a "contains" approach as a fallback:
```python
# In resolve(), if suffix matching fails:
for sym_id in self.graph.symbols:
    # Extract the method/function name from both
    our_tail = sym_id.split(".")[-2:] if "." in sym_id else [sym_id]
    jedi_tail = jedi_target.full_name.split(".")[-2:] if "." in jedi_target.full_name else [jedi_target.full_name]
    if our_tail == jedi_tail:
        return sym_id, "resolved_via_inference", "jedi_inference"
```

### 2. Print What Jedi Is Returning
Add detailed logging right after Jedi call:
```python
if jedi_results:
    logger.info(f"Jedi for {raw} returned: {[(r.full_name, r.type) for r in jedi_results]}")
    logger.info(f"Available symbols starting with relevant parts: {[s for s in self.graph.symbols if 'ArchetypeEngine' in s]}")
```

### 3. Verify `__init__` Methods Are Captured
The Definitions pass must be capturing `__init__` methods. Check:
```bash
jq '.symbols | map(select(.name == "__init__")) | length' .lineagelens/graph.json
```

If this is 0 or very low, `__init__` methods aren't being captured.

## Next Steps

1. **Run the debug steps above** to see what Jedi is actually returning
2. **Share the logging output** so we can see where the mismatch is
3. **Implement the quick improvements** based on what we find

The infrastructure is in place — we just need to align the symbol ID formats.
