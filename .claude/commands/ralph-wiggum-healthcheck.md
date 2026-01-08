# MeshForge Knowledge Healthcheck

Run a comprehensive audit of the MeshForge knowledge base to prevent memory degradation.

---

## Healthcheck Protocol

I will systematically audit the `.claude/` knowledge base for:

### 1. **Continuity Check**
- Cross-reference all documentation files
- Identify contradictions between files
- Flag outdated information vs current codebase
- Verify architecture docs match actual code structure

### 2. **Fragmentation Analysis**
- Find duplicated information across files
- Identify orphaned docs (referenced nowhere)
- Check for circular references/loops
- Map information dependencies

### 3. **File Size Audit**
- Flag files over 1,500 lines for splitting
- Identify logical break points
- Propose sub-file structure where sensible

### 4. **First Assumption Challenge**
- Question initial findings
- Re-verify with fresh perspective
- Look for hidden assumptions in docs

### 5. **Codebase Sync**
- Compare documented features vs actual src/
- Verify paths and imports still exist
- Check version references are current

---

## Output Format

I will produce:
1. **Continuity Report** - Contradictions and gaps found
2. **Fragmentation Map** - Duplicates and orphans
3. **Split Recommendations** - Large files to break up
4. **Fix Actions** - Updates made to restore coherence
5. **Summary** - Overall knowledge health score

---

When complete: `<promise>HEALTHCHECK COMPLETE</promise>`

*"My cat's breath smells like cat food."* - Ralph Wiggum
