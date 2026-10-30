# Compatibility record

Which versions of the domain's four repos are known to work together — measured,
not promised. A row here says "this combination ran together on the reference
fleet and passed the named check on this date". Anything not in a row is
**untested**, not "incompatible".

## Release tags

Each tag sits on the commit where that version was set, not on a later HEAD.

### How a release is cut (monthly, from 2026-11)

MeshForge commits straight to `main` and the reference fleet runs `main`, so
HEAD has soaked nowhere and no single commit stays on the fleet for a week.
A release therefore tags a commit **X** whose code has *soaked as an
ancestor*:

1. **Pick X** on `main`, at least 7 days old, with CI green for that exact SHA.
2. **Soak:** for the 7 days after X reached the fleet, every deploy carried X
   (X or later commits), and `scripts/honest_status.sh` exited 0 on them.
3. **Disqualify:** if any `fix:` commit in those 7 days repairs a defect that
   was already present in X, X is out — pick a later X and start again.
4. **Stranger drill:** the virtual install drill (1.0 gate 5) passes on X.
5. **Set the version on top of X:** one commit whose parent is X and which
   changes only `src/__version__.py` and the changelog. The tag points at it;
   no branch does, and `main` gets the same version bump separately. Its date
   goes in the table above, and a "Tested together" row records the check.
6. **Nothing qualifies → no release that month**, and the changelog says so.
   A skipped month is an honest result; a forced tag is not.

A release says "this code ran for a week on real radios and nothing in it
needed fixing" — not "bug-free". 1.0 has no date; its gates are separate.

| Repo | Tag | Commit | Date |
|---|---|---|---|
| [meshforge](https://github.com/Nursedude/meshforge) | `v0.7.0-beta` | `13b9a739` | 2026-10-15 |
| [meshanchor](https://github.com/Nursedude/meshanchor) | `v0.3.0-beta` | `8d59f000` | 2026-10-15 |
| [meshforge-maps](https://github.com/Nursedude/meshforge-maps) | `v0.7.4-beta` | `d13dde4` | 2026-04-14 |
| [meshing_around_meshforge](https://github.com/Nursedude/meshing_around_meshforge) | `v0.6.0` | `e33911b` | 2026-07-07 |

## Tested together

| As of | meshforge | meshanchor | meshforge-maps | meshing_around_meshforge | Check |
|---|---|---|---|---|---|
| 2026-10-18 | `12c39388` (0.7.0-beta + 71) | `3fd84b20` (0.3.0-beta + 30) | `b20a93a1` (0.7.4-beta + 77) | `b17ca52` deployed on the field node — **not checked** against MeshForge | MeshForge 9/9 fleet boxes and MeshAnchor 1/1 at those SHAs, maps on all 4 boxes that run it (`fleet_pull`); CI green for each SHA; `scripts/honest_status.sh` full run exit 0 on the last code change (`ff94052a`) and every fleet leg PASS at `12c39388` (`--quick`: suite leg skipped, covered by CI); maps CI runs its core-health contract test against MeshForge's real `SharedHealthState` |

## What is pinned by a test, not by this table

- **meshforge-maps ⇄ meshforge (health DB):** `tests/test_core_health_contract.py` in
  meshforge-maps builds the DB with MeshForge's own producer on every CI run. A
  schema change in MeshForge fails maps CI the same day.
- **meshforge ⇄ meshanchor (shared files):** `scripts/parity_check.py` in meshforge
  byte-locks the twinned files (RNS init, canonical message, the queue outage mixin, …).

## Known gaps

- meshforge-maps' `manifest.json` declares `min_meshforge_version: 0.5.0`; nothing
  has tested maps against MeshForge older than the row above.
- meshing_around_meshforge has no automated check against MeshForge.
