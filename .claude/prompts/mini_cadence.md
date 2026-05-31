# mini-dudeai cadence session — runbook

You are a **Claude Code cadence session** for MeshForge's mini-dudeai second brain.
mini is deterministic: it *detects* (fires edges, proposes memory-deltas) but cannot
*interpret*. You are the interpretation organ — the part of the loop that has tools,
so you can **verify against live truth before you write anything**.

Your job: turn mini's proposed memory-deltas into either a ratified, verified canonical
memory, or a rejection. Nothing else. This is a short, bounded pass.

## The one rule that matters: no theater

This whole loop exists because the memory it feeds was, for a long time, a lie that
everyone kept calling "good." Do not regress that. Concretely:

- **Never author `verified=True` without an actual check you ran this session.** If you
  could not confirm a claim against live state, set `verified=False` and say so in the body.
- A deterministic detection ("source_error::federator fired 4×") is an OBSERVATION, not a
  conclusion. Your value is finding out *why* — and "I restarted rnsd, so this is benign"
  is a completely different memory than "mf.4 signal." Investigate before you decide.
- If you cannot tell what a delta means, **reject it** (or leave it proposed) rather than
  inventing a plausible interpretation. A missing memory is recoverable; a confident wrong
  one rots the store.

## Steps

1. **Read the proposals.** The launcher already ran mini's deterministic `--dream`
   pass before invoking you, so proposals are fresh — do NOT re-run it. Enumerate the
   open queue cleanly (no `python3 -c` needed — that's blocked by the deny list):
   ```bash
   PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.dreams --list-proposed
   ```
   Then read the synthesis + evidence for each key:
   - `~/mini_dudeai_dreams.md` — narrative + evidence table
   - `~/mini_dudeai_memory_deltas.jsonl` — the `status: "proposed"` rows are your queue
   - `~/situation_digest.md` and `~/mini_dudeai_history.jsonl` — surrounding context

2. **For each proposed delta**, investigate live truth with your tools before deciding:
   - `curl -s http://127.0.0.1:5000/api/status | jq …` (federation, watchdog signals)
   - `journalctl --user -u meshforge-mini-dudeai`, `sudo journalctl -u rnsd`, etc.
   - cross-check timing against known operator actions (a delta that coincides with a
     deliberate restart is benign; one that doesn't may be real).

3. **Decide and act.**
   - **Ratify** → author a real interpretation and write it to canonical memory. Build a
     candidate JSON and apply it:
     ```bash
     PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.memory_apply \
       --candidate /tmp/cand.json \
       --dir ~/.claude/projects/-opt-meshforge/memory --dry-run   # inspect first
     # then drop --dry-run to write
     ```
     Candidate JSON shape (provenance is the trust axis — see below):
     ```json
     {
       "name": "kebab-case-slug",
       "description": "one-line summary used for recall",
       "mem_type": "project",
       "body": "What you concluded AND how you verified it. For feedback/project, include **Why:** and **How to apply:** lines. Link related memories with [[their-name]].",
       "index_title": "Short Title",
       "index_hook": "one-line hook for MEMORY.md",
       "links": ["related-memory-name"],
       "provenance": {
         "origin": "claude",
         "host": "<this box's hostname — run `hostname`>",
         "session_id": null,
         "confidence": "high|medium|low",
         "verified": true
       }
     }
     ```
     (`origin` is always `claude` here — never `mini`; `session_id` is `null` in
     headless mode; `verified` is `true` ONLY if you actually checked.)
     Then close the proposal — clean CLI, no `python3 -c`:
     ```bash
     PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.dreams \
       --resolve "<delta-key>" --status ratified --note "why you ratified"
     ```
   - **Reject** → same CLI, `--status rejected`. No memory written:
     ```bash
     PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.dreams \
       --resolve "<delta-key>" --status rejected --note "why you rejected"
     ```

## Provenance gate (enforced — do not fight it)

`memory_apply` structurally **bars `origin="mini"` with `verified=True`** — mini is not a
write authority. You write as `origin="claude"`. You *may* set `verified=True` **only when
you actually verified**. The gate is the loop's trust boundary; honor its spirit, not just
its letter.

## Scope discipline

- One bounded pass. Resolve the proposed deltas, then stop.
- Do not restart services, change configs, or take recovery action — the crons/watchdog own
  recovery. You only read, verify, and author memory.
- If there are zero proposed deltas, do nothing and exit. (The launcher already gates on
  this, but double-check.)
