# Dep stray-copy watching: why the scope stops where it does (2026-07-19)

> Structural-dark burn-down **row 3**. The registry row
> `dep_version_drift_strays_blind` asked: extend stray/floor watching to other
> deps, or formally accept the residual? **Decision: ACCEPT AS PERMANENT**
> (operator, 2026-07-19), on the fleet-wide survey below. This file is the
> dated note the row points at, so the question doesn't get re-derived.

## What is watched today

| Dep | Watcher | Why it earns a watcher |
|-----|---------|------------------------|
| `meshtastic` | `probe_dep_version_drift` (floor) + `probe_dep_install_fragmented` (split) | installed by pip, pipx AND apt into competing consumer positions; its updates kept failing (#83, PEP 668, missed rolls) |
| `rns` / `lxmf` | `probe_rns_version_drift` (fork pin) + `probe_rns_env_coherence` (intra-box agreement) | MeshForge-owned fork pins; a stray copy breaks the shared rnsd substrate (the moc3 nomadnet-venv lesson) |

## The survey (all 8 boxes, every root-readable install location)

Run with the probes' own `_enumerate_pkg_installs` + `_consumer_of_record_version`,
so the numbers are what a probe would see — not what `pip list` says:

- **The only deps installed by competing TOOLS are the two already watched.**
  Everything else arrives exactly one way: apt (OS libs) or pip-into-the-venv.
- **For OS-shipped libs, "fragmentation" is the designed state, not drift.**
  venv/user-site shadowing `system-dist` is how the layering is supposed to
  work. Observed and CORRECT: moc4 `requests` venv 2.34.2 over system 2.28.1;
  moc4 `pyyaml` venv 6.0.3 over system 6.0; VolcanoAI `folium` at 0.20.0 in
  three locations at once.
- **Extending the floor check would manufacture a confidently-wrong page.**
  moc4's *system* `requests` 2.28.1 is genuinely below the `core.txt`
  `requests>=2.31.0` floor — while the actual consumer runs a compliant venv
  copy. A signal saying "requests below floor" there would be true about a copy
  nothing imports and false about the box. That is the exact defect class
  `honest_failure_modes` exists to prevent.
- **Or it would never fire at all.** `rich>=13.0.0`, `pyyaml>=6.0`,
  `distro>=1.8.0` are floors reality outgrew years ago; a probe that cannot
  trip is false assurance, which is worse than a named blind spot.
- `paho-mqtt` and `folium` (MeshForge-installed, not OS-shipped) are the only
  plausible extension candidates. Neither is fragmented on ANY box today, and
  both arrive via pip into the venv only — no competing installer, so no
  mechanism for the class to occur.

## Two benign findings, recorded so they aren't re-discovered as bugs

- **kiai `meshtastic` is split**: system-dist 2.7.10 vs user-pipx 2.7.9.
  `probe_dep_install_fragmented` stays silent BY DESIGN — its below-floor
  clause is load-bearing, so a pipx CLI legitimately running ahead of the lib
  does not page. Both copies are ≥ the 2.7.9 floor. Working as specified.
- **moc5 `pypubsub`**: user-site 4.0.3 shadows a newer system-dist 4.0.7 — the
  #83 shadowing shape, but on an unwatched, non-critical dep with no observed
  impact. Noted, not actioned.

## The invariant this leaves behind

**Stray-copy risk is a property of deps installed by competing TOOLS, not of
deps in general.** Add a watcher when a dep gains a second installer (an apt
package appearing alongside the pip one, a pipx shim, a fork pin) — not merely
because it is important. If that ever happens, see the ⚠️ comment above
`_DEP_VERSION_WATCHED` in `utils/watchdog_probes_drift.py`: two call sites index
`[0]`, and `TestDepWatchedTupleClosedConsumer` fails until they are fixed.
