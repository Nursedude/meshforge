# The In-Domain Principle — Never Quit to Fix It

> **Foundational design rule.** Once a user is inside MeshForge, they do
> everything in the domain — install, configure, diagnose, *and repair* —
> without leaving the app. **If the user has to quit MeshForge to fix
> MeshForge, we did something wrong.**

This is not a UX nicety. It is a success criterion for the application. A NOC
that ejects its operator to a shell to fix a service, edit a config, or read a
log is a NOC that fails the moment the operator is in the field, on a phone, or
new to the domain. The whole value proposition — *unify two mesh ecosystems and
make them operable by one person* — collapses if operating them still requires
fluency in `systemctl`, `nano`, `journalctl`, and `pipx`.

---

## The success test

For every operation, failure, and config in MeshForge, ask:

> *When this goes wrong, can the user fix it from inside the app — or do they
> have to drop to a shell?*

A "drop to a shell" answer is a **defect**, tracked in the gap register below,
not a documentation TODO. We close them like bugs.

---

## The shape: one remediation surface, many producers

The review of 2026-05-29 found that MeshForge already *has* the primitives to do
everything in-app — `service_check.py` (systemd ops), `diagnostic_engine.py`
(carries `auto_recoverable` + `recovery_action`), the mini-dudeai engine
(candidate-promotion). The gap was never missing capability: **the "detect"
side and the "fix" side were built and never wired together.** Diagnostics
produced text (including literal `sudo systemctl restart …` strings) instead of
actions. Configs were viewable but editable only via `nano`. Recovery handlers
were defined but unregistered.

The cure is a single in-app **remediation surface** — *finding → proposed
action → ratify → apply* — that everything routes through, instead of each
handler reinventing (or punting to a shell):

```
  PRODUCERS                       SURFACE                        BACKENDS
  diagnostic_engine findings ─┐                            ┌──→ service_check  (start/stop/restart/enable)
  mini-dudeai escalations ────┼─→ proposal → ratify → apply ┼──→ config writers (in-app forms, not nano)
  the standalone chat-compiler┘   (one keystroke, in-app)   └──→ candidate-promoter (rules)
```

Properties that make this the right spine:

- **The trust model already exists.** mini-dudeai's rules use propose→ratify
  (the runtime validates and atomic-promotes a `.candidate`; it never writes the
  canonical file itself). We generalize that one safe pattern to the whole app:
  a producer *proposes* a fix, the operator *ratifies* with one keystroke, a
  backend *applies* it. Destructive/privileged actions always ratify; trivial
  ones may auto-apply by policy.
- **It unifies the two missions.** The same surface that turns a diagnostic
  into a "Fix it" button is where a mini-dudeai escalation becomes *actionable*
  (instead of text a cloud session reads later), and where the standalone
  WireClaw-like variant's spoken rule lands. Fleet and standalone share one
  engine, one surface, two producer bundles.
- **mini-dudeai is the detect engine; this is the fix engine it lacked a home
  in.** The dream/synthesis loop surfaces *insights*; the remediation surface
  is where they get acted on without quitting.

---

## Gap register (triaged backlog — the work)

From the 2026-05-29 in-app audit. The raw scan flagged ~60 strings; this is the
honest triage (cross-app interop and protocol facts are **not** defects — see
Legitimate exceptions).

### Class 1 — Detect-but-don't-fix (deepest; the unwired loop)
- `diagnostic_engine.py` defines `auto_recoverable` / `recovery_action` but
  nothing wires them to a TUI action; findings render as text, incl. literal
  shell commands.
- `config_doctor.py` is explicitly read-only ("no fixes applied").
- **Close by:** the remediation surface (Arc 2), proven on one finding.

### Class 2 — Config editing drops to `nano`/`vi`
- `meshtasticd_config.py` (config.yaml + config.d), `rns_config.py`,
  `_nomadnet_io_ops.py` spawn an external editor.
- **No form for LoRa region/preset/frequency** — the most-touched config is
  hand-edited YAML.
- **Close by:** the in-app config-form pattern (Arc 3), proven on LoRa.

### Class 3 — Logs eject the user
- `journalctl` runs as an uncaptured `subprocess.run` to the terminal.
- **Close by:** capture log output in-pane (cheap standalone win).

### Class 4 — mini-dudeai rules have no in-app editor
- A rules candidate is produced only by hand-editing JSON or an AI session.
- **Close by:** in-app rule editor that writes a candidate through the surface
  (Arc 4) — directly unblocks the standalone variant.

### Class 5 — Failure-path fallbacks (lower frequency, real)
- Install failures → "run `apt`/`pip`/`pipx` manually" (~20 sites).
- Permission errors → "run `chown` manually" (app can't self-escalate).
- Service-recovery fallbacks → "restart manually"; config bootstrap →
  "mkdir/cp manually" / "run with sudo"; port conflict → "`lsof` + kill".
- **Close by:** in-app install/elevate/cleanup actions through the surface
  (Arc 5), alongside install integration.

---

## Legitimate exceptions (NOT defects)

The principle governs fixing *MeshForge*. It does not forbid:

- **Cross-app interop.** Showing an `rnid`/LXMF hash for the user to paste into
  a *different* app (Sideband, NomadNet) is interop, not a shell-escape.
- **Protocol facts.** "NomadNet nodes don't broadcast GPS" is a protocol
  limitation. (We may still offer a position-entry form — that's a feature, not
  a violation.)
- **Genuinely external prerequisites** the app cannot perform (e.g. an
  interactive cloud login the operator must run once). Mark these explicitly.

When an escape is a legitimate exception, mark it in code with an inline
`# in-domain-ok: <reason>` comment so the guard (MF018) defers to that judgment
and the next reviewer sees the reasoning.

---

## The rule for new code (MF018)

> New TUI code must offer an **in-app action**, never a bare shell instruction.

Enforced by lint rule **MF018** (`scripts/lint.py`) over `src/launcher_tui/`:
it counts shell-escape patterns (editor spawns, "run/install … manually",
"run with sudo", uncaptured `journalctl`/`lsof` instructions in user-facing
strings) against a **frozen per-file baseline**. The baseline can only
*shrink* — closing a gap removes its entry; introducing a new escape exceeds
the baseline and fails the build. This is the Issue #29 regression-ratchet
applied to UX: we close the backlog faster than we add to it.

Mark a legitimate exception with `# in-domain-ok: <reason>` to exclude that
line. A new in-app remediation (a "Fix it" action through the surface) is the
*correct* way to resolve an MF018 failure — not suppression.

---

## The arc roadmap (methodical, one spine at a time)

1. **Foundation** — this doc + MF018 ratchet + regression test. *(this arc)*
2. **Remediation surface** — proven end-to-end on one diagnostic (detect→fix).
   The spine every later fix hangs off.
3. **Config-form pattern** — proven on the LoRa region/preset/frequency edit.
   Second spine; every config editor inherits it.
4. **mini-dudeai rule editor** — propose→ratify through the surface; unblocks
   the standalone variant.
5. **Install integration + failure-path close-out** — both mini-dudeai variants
   into `setup_wizard.py`/`install_noc.sh`; in-app install/elevate/cleanup.

Each arc closes fully — shipped, tested, verified live, baseline shrunk — before
the next opens.

---

*Companion to `.claude/foundations/ai_principles.md` (why MeshForge exists) and
`tui_architecture.md` (how the TUI is built). This doc is the bar the TUI is
held to.*
