import sys
sys.path.insert(0, "/opt/meshforge/src")
from utils.deployment_profiles import list_profiles

ps = list_profiles()
flags = sorted({f for p in ps for f in p.feature_flags})

out = []
w = out.append
w("# Deployment Profiles\n")
w("> **GENERATED from the SSOT** — `src/utils/deployment_profiles.py`.")
w("> Regenerate with `python3 scripts/gen_profiles_doc.py` after changing a")
w("> profile. This file was MISSING for months while `CLAUDE.md` linked to it")
w("> (found 2026-09-15); a doc that does not exist is worse than none, because")
w("> the link implies it was checked.\n")
w("A profile declares **what this box runs**. It is stored in")
w("`~/.config/meshforge/deployment.json` under the `profile` key.")
w("(resolved via `utils.paths.get_real_user_home()` — MF001: the raw stdlib "
  "home lookup returns /root under sudo and breaks config persistence.)\n")
w("⚠️ **`deployment.json` is SHARED with the fleet-role system.**")
w("`scripts/provision_role.py` owns the `role`, `service_overrides` and")
w("`organ_expectations` keys in the same file. Every writer must")
w("read-merge-write; `save_profile()` does, and REFUSES (returns `False`)")
w("rather than overwrite a file it cannot parse.\n")
w("## The profiles\n")
w("| Profile | Display name | Required services | Required packages |")
w("|---|---|---|---|")
for p in ps:
    rs = ", ".join(f"`{s}`" for s in p.required_services) or "—"
    rp = ", ".join(f"`{s}`" for s in p.required_packages) or "—"
    w(f"| `{p.name.value}` | {p.display_name} | {rs} | {rp} |")
w("")
w("## Feature flags\n")
w("A flag gates a TUI menu action via `TUIContext.feature_enabled()`.")
w("⚠️ **As of 2026-09-15 the TUI never populates `feature_flags`, so every")
w("gate passes and all actions are visible on every box.** The seam exists")
w("and is tested; only the feed was removed in the 2026-08-14 Q1 purge.")
w("See the TUI plan's Phase 3.\n")
w("| Profile | " + " | ".join(f"`{f}`" for f in flags) + " |")
w("|---|" + "---|" * len(flags))
for p in ps:
    cells = " | ".join("yes" if p.feature_flags.get(f) else "—" for f in flags)
    w(f"| `{p.name.value}` | {cells} |")
w("")
w("## Descriptions\n")
for p in ps:
    w(f"- **`{p.name.value}`** — {p.description}")
    if p.optional_services:
        w(f"  - optional services: " + ", ".join(f"`{s}`" for s in p.optional_services))
    if p.optional_packages:
        w(f"  - optional packages: " + ", ".join(f"`{s}`" for s in p.optional_packages))
w("")
w("## Selecting a profile\n")
w("```bash")
w("python3 src/launcher.py --profile gateway   # explicit")
w("python3 src/launcher.py                     # auto-detect")
w("```")
w("")
w("In the TUI: **Configuration > MeshForge Settings > Deployment Profile**.")
w("(That action was broken from its introduction until 2026-09-15 — it")
w("imported a `get_profile` symbol that does not exist and passed a string to")
w("`save_profile()`, so every selection reported `Failed to set profile`.)\n")
w("## Consumers\n")
w("`src/launcher.py`, `src/daemon.py`, `src/daemon_config.py`,")
w("`src/setup_wizard.py`, `src/utils/startup_health.py`, and")
w("`src/launcher_tui/handlers/settings.py`.\n")
w("## Not a fleet role\n")
w("A **profile** says what software features this box offers. A **role**")
w("(`docs/fleet_roles.yaml`, `scripts/provision_role.py`) says what the fleet")
w("expects this box to run. They are different vocabularies that happen to")
w("share one file. When they disagree, the role is what the fleet's organs")
w("check against.\n")

open("/opt/meshforge/.claude/foundations/deployment_profiles.md", "w").write("\n".join(out))
print("wrote", len("\n".join(out)), "chars")
