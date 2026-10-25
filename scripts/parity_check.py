"""MeshForge <-> MeshAnchor RNS-reliability parity check.

The two sister NOC apps (MeshForge at /opt/meshforge, MeshAnchor at
/opt/meshanchor) share the fleet's RNS substrate (one rnsd per box, shared
instance). RNS-reliability changes land in MeshForge FIRST (the lead repo),
then this script flags MeshAnchor as drifted, then the change is ported.

Two tiers of parity:

* **Byte-identical tier** — files that MUST match exactly between the repos.
  A SHA-256 mismatch is drift. Today: the guarded RNS-init chokepoint, the
  shared bridge contract, the fleet-truth builder, the mini-dudeai generic
  engine core (WS-A, 2026-07-22 — the observation-only rule/cadence loop is
  now twinned onto MeshAnchor; only each app's ADAPTER preset diverges), and
  the fork-pin block of requirements/rns.txt (compared as a normalized
  sub-block, since the surrounding prose legitimately differs per app).

* **Shape tier** — files that carry the same INTENT but are allowed
  app-specific text. We assert the presence of key symbols, not byte
  equality. Today: the rnstatus parser's ``timed_out`` keystone, the lint
  rules MF009 + MF019, and the two RNS-wedge probes.

* **Calibration claim-gate shared core** — the calibrated-claims rule+gate are
  a by-intent MF<->MA pair (the 2026-06-15 spine arc): MeshAnchor now carries
  the byte-locked mini core (WS-A) INCLUDING calibration_ledger.py, but its
  ``claim_gate.py`` still does NOT feed that ledger (the Stop-hook→ledger feed
  stays MeshForge-only), and it uses its own marker path — so byte/shape parity
  of the GATE is wrong here. But the DETECTION VOCABULARY
  (which phrases are strong completion claims / honest hedges / quoted evidence)
  must agree, or the two gates judge the same closing message differently —
  silent drift. So we AST-extract the three vocabulary constants from both
  ``scripts/claim_gate.py`` copies and compare them as SETS (no import side
  effects; order-insensitive), plus assert the core decision functions are
  present in both. Added 2026-06-19 (self-audit-qa-arc §1) — this REVERSES the
  original "maintain by intent, not parity_check" choice, per operator request,
  so a future MeshForge claim_gate improvement can't silently skip MeshAnchor.

Pure stdlib. Runs from anywhere:

    python3 /opt/meshforge/scripts/parity_check.py
    python3 /opt/meshforge/scripts/parity_check.py --meshforge /opt/meshforge \\
        --meshanchor /opt/meshanchor

Exit codes:
    0  in sync (both tiers)
    1  byte-tier drift (a must-match file differs) OR a shape-tier symbol
       is missing
    2  a repo or expected file is missing, or a leg is BLIND (the file is
       there but this tool cannot read the compared value) — can't compare
"""
import argparse
import ast
import hashlib
import os
import re
import sys
from dataclasses import dataclass

DEFAULT_MESHFORGE = "/opt/meshforge"
DEFAULT_MESHANCHOR = "/opt/meshanchor"

# ── Byte-identical tier ────────────────────────────────────────────────
# repo-relative path -> must be byte-for-byte identical between the repos.
# rns_version_check.py is intentionally NOT here: its LOGIC is identical but
# its docstring legitimately names its own repo path, so it lives in the
# shape tier (symbol presence) instead.
BYTE_IDENTICAL = (
    "src/utils/rns_init.py",
    # The truth-path result type (2026-10-02, ADR plans/adr_truth_kernel_2026_10_02.md)
    # and the per-process substrate probe both repos' rnsd restart order now
    # measures its client set with. Two copies of either that drift = two
    # repos disagreeing about which processes are RNS clients (hfm #5).
    "src/utils/observation.py",
    "scripts/hs_substrate_skew.py",
    "src/gateway/canonical_message.py",
    # The routing decider + its default_route vocabulary (MeshAnchor #151,
    # 2026-10-19). MeshAnchor had silently missed THREE MeshForge routing
    # fixes (drop valid/honoured, drop-first, default_route) — so a "drop"
    # workaround given to a tester was invalid config there. Both bridges
    # must decide who crosses identically.
    "src/utils/classifier.py",
    "src/gateway/routing_vocab.py",
    # M→R queue outage behaviour (ROADMAP 6f, 2026-10-17): defer-don't-count,
    # max age, clock-jump shift. Both bridges sit on one rnsd; two copies
    # that drift = two different outage contracts (review F8).
    "src/gateway/message_queue_outage_mixin.py",
    # The delivery-label vocabulary (2026-09-24, review C F7): which routing
    # lanes count as which transport in delivery_counters. Both repos' writers
    # and readers judge confirmation rates by it; a per-repo drift would make
    # the twins count one radio under different labels. Dependency-free.
    "src/utils/delivery_labels.py",
    # The per-leg delivery vocabulary + bucket store (BIC-2, 2026-10-23):
    # TRANSPORTS, OUTCOMES, BUCKET_S, the leg key and the 15m/1h/24h windows.
    # Both twins' counters write it and both TUIs read it; a drift would make
    # one leg mean two things across a fleet that runs both. Dependency-free.
    "src/gateway/delivery_legs.py",
    # The RNS-tree permission foundation SSOT (configdir/logfile/storage layout
    # for a non-root rnsd — the mf.4/#73 perms class). App-agnostic, stdlib-only,
    # delegated to by both rns_alignment (MeshForge) and fleet_foundation (both
    # repos), so it MUST stay byte-identical.
    "src/utils/rns_tree_perms.py",
    # Root-safe `systemd --user` enrollment reads (2026-08-09). Both repos'
    # provisioners judge a declared `user_timers:` entry against live
    # enablement, and BOTH must answer "is this enabled" identically — a
    # divergence would make one repo's role_drift page for drift the other
    # calls clean, on boxes that run both stacks. App-agnostic, stdlib-only.
    # ⚠️ Its whole reason for existing is that three call sites were about to
    # grow three implementations; a per-repo copy re-opens that. MeshForge is
    # the lead repo. See the module docstring for the any-target lesson.
    "src/utils/user_units.py",
    # The rnsd plugin manifest (2026-10-10, R5): which interfaces/<type>.py
    # rnsd will exec on its next start, by rnsd's own enable rules. Both repos
    # judge the SAME shared rnsd on a box, so the twins must classify alike;
    # its enable rule is the shared rns_interface_flags, pinned with it.
    "src/utils/rnsd_plugin_manifest.py",
    "src/utils/rns_interface_flags.py",
    # RNS→radio ingress allowlist (MeshAnchor port 2026-10-15): WHO may reach
    # our radios through either app's bridge. One verdict, one ledger schema,
    # one fleet projection — two copies that drift would judge the same
    # sender differently on a box running both stacks. MeshAnchor passes its
    # own ledger path (the module's default names MeshForge's data dir).
    "src/gateway/rns_ingress_policy.py",
    # LXMFace deterministic-avatar port (2026-06-14): the pure algorithm, the
    # vendored client-side JS, and the upstream parity vectors. These produce a
    # node's face from its LXMF/RNS hash and MUST agree across both apps (and
    # with Ratspeak) — a silent edit to one repo's copy would diverge the faces.
    # MeshForge is the lead repo for this port; mirror any change to MeshAnchor.
    "src/utils/lxmface.py",
    "web/js/lxmface.js",
    "tests/fixtures/lxmface_vectors.json",
    # Honest fleet-truth builder (Phase 4 of the fleet-truth arc, 2026-07-19):
    # the pure tri-state schema/classifier under both NOCs' /api/fleet/truth.
    # Module-level it imports only its twinned registry below; its lazy
    # imports are guarded and degrade. The classify_block default-dark contract
    # and the worst-of roll-up MUST agree across the twins so the two domains'
    # truth documents mean the same thing. Each app keeps its OWN collector
    # (fleet_truth_collector.py: MF = fleet_hosts + fleet_naming; MA =
    # fleet.json peers) — the collector is deliberately NOT parity.
    "src/utils/fleet_truth.py",
    # Its Known Blind Spots registry (split out 2026-10-20, MF025): pure data,
    # stdlib-only, imported by fleet_truth.py — so it twins WITH it. A one-sided
    # landing reads [MISSING] here, which the pre-push hooks block.
    "src/utils/fleet_blind_spots.py",
    # User-unit template rule (2026-10-21): the ONE rule every *-user.service
    # copier (update.sh, TUI Software Updates, install_noc.sh, provision_role)
    # and the TUI "User Unit Templates" pane obey. Stdlib-only; its tests and
    # update.sh's block test are byte-identical too.
    "src/utils/user_unit_templates.py",
    "tests/test_user_unit_templates.py",
    "tests/test_update_user_templates.py",
    "tests/test_updates_user_unit_refresh.py",
    # MeshChatX client rpc_key step (2026-10-21) — the installers differ per
    # app, the block and its test do not.
    "tests/test_install_meshchatx_client_key.py",
    # One unit name, one definition + the MeshChatX stock-RNS waiver (2026-10-21).
    "tests/test_service_unit_single_definition.py",
    "tests/test_install_meshchatx_stock_waiver.py",
    # BIC-3 operator session store (2026-10-13): hashed sessions + pairing codes
    # behind both maps' default-deny write gate. App-neutral (CONFIG_DIR names
    # the store dir and the CLI), so the twins must refuse, expire and fail
    # closed identically. The gate mixin (_map_auth_endpoints.py) is NOT here:
    # each repo's origin rule differs by design.
    "src/utils/web_auth.py",
    # The NOC read grant (session 3, 2026-10-14). The five modules + the export
    # entry + the shared tests are byte-identical; the ONE per-app file,
    # src/utils/box_truth_app.py, is NOT listed — its NAMES are pinned by
    # tests/test_box_truth_app_seam.py (itself listed), its VALUES differ.
    "src/utils/box_truth.py",
    "src/utils/box_truth_reader.py",
    "src/utils/box_truth_grants.py",
    "src/utils/box_truth_center.py",
    "src/utils/box_truth_collect.py",
    "scripts/box_truth_export.py",
    "tests/test_box_truth_app_seam.py",
    "tests/test_box_truth_core.py",
    "tests/test_box_truth_reader.py",
    "tests/test_box_truth_grants.py",
    "tests/test_box_truth_center.py",
    "tests/test_box_truth_export.py",
    "tests/test_box_truth_role.py",
    "tests/test_box_truth_step3.py",
    # session 3 step 3 (2026-10-15): the spool writer and the grant CLI are
    # the SAME file in both apps — per-app values come from the seam
    # (box_truth_app.expect_hostkey_of / INSTALL_DIR / CONFIG_DIRNAME).
    "scripts/fleet_truth_spool.py",
    "scripts/box_truth_grant.py",
    # step 4 (2026-10-15): the refuse-never-recreate identity loader and its
    # library-faithful fake — one copy of the address-safety rule.
    "src/gateway/identity_store.py",
    "tests/_fake_rns_identity.py",
    # the deferred move (2026-10-15): the status-block transforms both apps'
    # status endpoints and ssh-spool fallbacks call — MA's copy had drifted.
    "src/utils/status_blocks.py",
    # mini-dudeai GENERIC ENGINE CORE (second-brain arc WS-A, 2026-07-22). The
    # observation-only rule/cadence loop is now twinned onto MeshAnchor. These
    # modules carry ZERO app-specific imports (verified import-clean across the
    # whole package), so they copy byte-clean and MUST stay identical — the
    # engine's behavior is the shared contract; only the ADAPTER (each app's
    # preset + role seed + health-surface source) is allowed to diverge.
    # MeshForge is the lead repo: land a core change here, then port. The
    # concrete sources/actions + daemon + presets are DELIBERATELY not here
    # (they are the adapter layer; e.g. MA's meshanchor_fleet reads a SQLite
    # blackout DB where MeshForge's meshforge_fleet reads watchdog.json).
    "src/mini_dudeai/engine.py",
    "src/mini_dudeai/state.py",
    "src/mini_dudeai/history.py",
    "src/mini_dudeai/candidate.py",
    "src/mini_dudeai/config.py",
    "src/mini_dudeai/brief.py",
    "src/mini_dudeai/warmstart.py",
    "src/mini_dudeai/dreams.py",
    "src/mini_dudeai/calibration_ledger.py",
    # The ledger's TEST, tracked alongside its source (2026-07-31). The source
    # was byte-locked; the test was not, so MeshForge accumulated 34 assertions
    # over months while MeshAnchor's identical copy of the module had ZERO —
    # a byte-locked file whose behaviour only one repo actually checked. Found
    # by accident, not by a gate. Byte-locking the test too means a future
    # assertion added in the lead repo must reach the twin or parity_check says
    # so. (tests/fixtures/lxmface_vectors.json is the same precedent.)
    "tests/test_calibration_ledger.py",
    # Same precedent, same shape, found 2026-09-09 while porting a warmstart
    # fix: MeshAnchor carried the byte-locked warmstart.py with NO test file
    # at all, so every assertion protecting that module lived in one repo. It
    # could not be twinned until that day because one test in it
    # (test_default_paths_come_from_the_app_adapter) pinned MeshForge's
    # ADAPTER basenames — legitimately per-app, and MA's own
    # test_mini_artifact_paths.py already covers its side. That test was moved
    # into a same-named MF file, leaving this one app-agnostic (every test
    # pins `path`/`tmp_path` and touches no adapter) and safe to lock.
    "tests/test_mini_warmstart.py",
    "src/mini_dudeai/model_router.py",
    # The engine's generic I/O building blocks — concrete sources + actions.
    # All import-clean and generic (a reader/writer per medium); an app that
    # needs a bespoke source/action defines it in its PRESET (e.g. MA's
    # BlackoutDbSource / MiniSelfSource live inline in meshanchor_fleet, NOT
    # here). So these stay identical too — byte-locking them means MeshForge's
    # test suite covers both twins and drift is caught, without duplicating the
    # tests onto MeshAnchor (WS-A, 2026-07-22).
    "src/mini_dudeai/sources/base.py",
    "src/mini_dudeai/sources/__init__.py",
    "src/mini_dudeai/sources/boot_health.py",
    "src/mini_dudeai/sources/json_file.py",
    "src/mini_dudeai/sources/http_json.py",
    "src/mini_dudeai/sources/file_mtime.py",
    "src/mini_dudeai/sources/nats_sensor.py",
    "src/mini_dudeai/actions/base.py",
    "src/mini_dudeai/actions/__init__.py",
    "src/mini_dudeai/actions/ntfy.py",
    "src/mini_dudeai/actions/propose_escalation.py",
    "src/mini_dudeai/actions/noop.py",
    "src/mini_dudeai/actions/file_annotate.py",
    "src/mini_dudeai/actions/nats_action.py",
    # cadence_fallback exports constants byte-locked brief.py consumes
    # (CADENCE_TRIAGE_BASENAME, TRIAGE_FRESH_S) — an MA-side edit would change
    # locked-module behavior invisibly (07-23 audit, lock-boundary finding).
    # It is app-generic (no app names), so lock it rather than shape-pin it.
    "src/mini_dudeai/cadence_fallback.py",
    # The prune-cron installer both update.sh files (and MF's fleet_sync) call
    # (2026-10-18). It derives its repo path at runtime and both twins share
    # the meshforge state dir, so there is nothing per-repo in it to drift.
    "scripts/ensure_prune_cron.sh",
    "tests/test_ensure_prune_cron.sh",
)

# The fork-pin block is compared as a normalized sub-block: the lines that
# matter (MF-FORK-PIN markers + the git requirement lines) must match, but
# the surrounding header prose is allowed to differ per app.
FORK_PIN_FILE = "requirements/rns.txt"

# ── Shape tier ─────────────────────────────────────────────────────────
# repo-relative path -> tuple of substrings that MUST all be present in
# BOTH repos' copy of the file. Intent parity, not byte parity.
SHAPE_SYMBOLS = {
    "src/utils/rns_status_parser.py": (
        "timed_out",
        "def run_rnstatus(",
        "timeout_s",
    ),
    "scripts/lint.py": (
        "MF009",
        "MF019",
        "MF026",
    ),
    # Same version-drift tool in both repos (repo-specific docstring only).
    "scripts/rns_version_check.py": (
        "MF-FORK-PIN",
        "def pinned_versions(",
        "def installed_version(",
    ),
    # fleet_foundation: the audit/plan ENGINE is parity-shared (pure functions
    # over an injected owner-lookup), but each app declares its own data-roots
    # (meshforge_data_roots vs meshanchor_data_roots), so it's shape- not
    # byte-parity. Assert the shared engine + RNS-tree delegation are present.
    "src/utils/fleet_foundation.py": (
        "def audit_data_roots(",
        "def plan_data_root_fixes(",
        "def apply_foundation(",
        "from utils.rns_tree_perms import",
    ),
    # App self-identification on /api/status (cross-domain fleet presence,
    # Layer 0, 2026-06-23). Both NOCs serve an identically-shaped /api/status on
    # :5000; the `app` block lets a probe know WHICH app answered. The
    # _build_app_block / _read_deployment_role helpers are carried byte-identical
    # (the per-app name falls out of each repo's own __version__), but the file
    # as a whole legitimately differs (MeshForge's status block is richer), so
    # this is shape- not byte-parity. Assert both helpers + the call site exist.
    "src/utils/_map_status_endpoints.py": (
        "def _build_app_block(",
        "def _read_deployment_role(",
        'status["app"] = _build_app_block()',
    ),
    # _util is the mini engine's DELIBERATE twin-divergence point: byte-locked
    # modules import their app-specific values (unit/preset/repo/verdict-dir)
    # from here, and each app's copy carries its own values (07-23 audit —
    # locked files used to hardcode meshforge names, forcing a latent bug into
    # the MA twin). Shape-pin the adapter contract + the namespacing SSOT so
    # an app can't drop a symbol the locked modules depend on.
    "src/mini_dudeai/_util.py": (
        "def resolve_home(",
        "APP_MINI_UNIT",
        "APP_FLEET_PRESET",
        "APP_REPO_ENV",
        "APP_REPO_DEFAULT",
        "APP_VERDICT_SUBDIR",
    ),
}

# ── Calibration claim-gate shared core ─────────────────────────────────
# The detection vocabularies (compared as SETS via AST) + the core decision
# functions (compared as substring presence) that MUST agree between the two
# claim_gate.py copies. The ledger-feeding trio and the marker path legitimately
# DIFFER (MeshAnchor has no mini_dudeai), so they are deliberately NOT checked.
CLAIM_GATE_FILE = "scripts/claim_gate.py"
CLAIM_GATE_CONSTANTS = ("STRONG_CLAIMS", "CALIBRATION_MARKERS", "EVIDENCE_PATTERNS")
CLAIM_GATE_CORE_SYMBOLS = (
    "def has_strong_claim(",
    "def is_calibrated(",
    "def evaluate(",
    "def marker_satisfies(",
)

# ── Delivery-failure reason vocabulary ───────────────────────────────
# The drop reasons that count as a delivery FAILURE (the confirmation-rate
# numerator). delivery_counters.py is an untracked-diverged twin, so only
# this SET is compared: one reason counted as a failure on one side and
# benign on the other is the same event read two ways (2026-10-24, the
# UNSENDABLE twin alignment had to be checked by hand). Members are
# ``DropReason.X.value``, resolved by _extract_reason_set.
REASONS_FILE = "src/gateway/delivery_counters.py"
REASONS_CONSTANT = "DELIVERY_FAILURE_REASONS"

# The two RNS-wedge probes live in different modules in the two repos
# (MeshForge: watchdog_probes.py pure-function Signal idiom; MeshAnchor:
# active_health_probe.py HealthResult idiom). So we assert each repo
# carries the two probe concepts in ITS OWN home, not a shared path.
PROBE_SYMBOLS = {
    DEFAULT_MESHFORGE: (
        "src/utils/watchdog_probes.py",
        ("probe_rns_rpc_responsive", "probe_rns_interface_down_peer_reachable"),
    ),
    DEFAULT_MESHANCHOR: (
        "src/utils/active_health_probe.py",
        ("check_rns_rpc_responsive", "check_rns_interface_down_peer_reachable"),
    ),
}


def _sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _extract_literal_constant(text, name):
    """Value of a module-level ``name = <literal>`` assignment, as a frozenset,
    via AST (no exec/import side effects). The claim_gate detection vocabularies
    are tuples of strings; returned as a set so ordering differences aren't false
    drift. Returns None if the file is unparseable, the name is absent, or the
    value isn't a literal collection (the safe direction — a None on either side
    is reported 'missing', never silently 'ok')."""
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    try:
                        val = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        return None
                    if isinstance(val, (tuple, list, set, frozenset)):
                        return frozenset(val)
                    return None
    return None


def _extract_reason_set(text, name):
    """The set from _read_reason_set, or None for any non-'ok' state."""
    return _read_reason_set(text, name)[1]


def _read_reason_set(text, name):
    """``(state, value)`` for a module-level ``name = frozenset({...})`` whose
    members are string literals or ``Cls.MEMBER.value`` for a class in the same
    file whose body assigns ``MEMBER = "<str>"`` (an Enum). Resolved by AST, no
    import — delivery_counters pulls the gateway package. state is 'ok' |
    'no_file' | 'unparseable' (SyntaxError) | 'absent' (not bound in module scope) |
    'unresolvable' (a form or ANY member this tool cannot read: a partly-read
    set compared as 'ok' would be the false green this check exists to refuse).
    The states need different cures, so they stay distinct (review #2)."""
    if text is None:
        return "no_file", None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "unparseable", None
    classes = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = {
                t.id: st.value.value
                for st in node.body if isinstance(st, ast.Assign)
                and isinstance(st.value, ast.Constant)
                and isinstance(st.value.value, str)
                for t in st.targets if isinstance(t, ast.Name)}
    bindings = _module_bindings(tree, name)
    if not bindings:
        return "absent", None
    # Read only a lone top-level Assign/AnnAssign. A second binding (|=, a
    # conditional rebind, an import) means the value is not the literal: a
    # first-Assign read would be a false 'ok' (review of 528afc79).
    node = bindings[0]
    readable = ((isinstance(node, ast.Assign)
                 and all(isinstance(t, ast.Name) for t in node.targets))
                or (isinstance(node, ast.AnnAssign) and node.value is not None))
    if len(bindings) > 1 or node not in tree.body or not readable:
        return "unresolvable", None
    val = node.value
    if (isinstance(val, ast.Call) and isinstance(val.func, ast.Name)
            and val.func.id in ("frozenset", "set") and len(val.args) == 1):
        val = val.args[0]
    if not isinstance(val, (ast.Set, ast.Tuple, ast.List)):
        return "unresolvable", None
    out = set()
    for el in val.elts:
        if isinstance(el, ast.Constant) and isinstance(el.value, str):
            out.add(el.value)
        elif (isinstance(el, ast.Attribute) and el.attr == "value"
              and isinstance(el.value, ast.Attribute)
              and isinstance(el.value.value, ast.Name)
              and el.value.attr in classes.get(el.value.value.id, {})):
            out.add(classes[el.value.value.id][el.value.attr])
        else:
            return "unresolvable", None
    return "ok", frozenset(out)


_PATTERN = getattr(ast, "pattern", ())
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _module_bindings(tree, name):
    """Statements that bind ``name`` in MODULE scope, in source order: any
    Store of it, an import alias, a def/class of that name, or ``global name``
    inside a function. 'absent' must mean bound nowhere in the module — but a
    local of the same name in a def/class/comprehension is another variable,
    and counting it would mask a real rename as blind (review of 528afc79).
    A bare ``name: T`` annotation binds nothing at runtime."""
    found = []

    def binds(node):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # a star import may bind it — unknowable here, so bound, never absent
            return any((a.asname or a.name.split(".")[0]) in (name, "*") for a in node.names)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.name == name
        if isinstance(node, ast.AnnAssign):
            return (node.value is not None and isinstance(node.target, ast.Name)
                    and node.target.id == name)
        # match captures (MatchAs/MatchStar .name, MatchMapping .rest) are
        # strings on a pattern node, not Name Stores; ast.pattern is 3.10+
        return any((isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store))
                   or (isinstance(n, _PATTERN)
                       and name in (getattr(n, "name", None), getattr(n, "rest", None)))
                   for n in _walk_scope(node))

    def visit(stmts):
        for st in stmts:
            # `global name` in any def — a method's too — binds module scope
            if binds(st) or isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef,
                                            ast.ClassDef)) and any(
                    isinstance(n, ast.Global) and name in n.names for n in ast.walk(st)):
                found.append(st)
            if isinstance(st, _SCOPES):
                continue
            for field in ("body", "orelse", "finalbody"):
                visit(getattr(st, field, []))
            for handler in getattr(st, "handlers", []):
                visit(handler.body)
            for case in getattr(st, "cases", []):
                visit(case.body)

    visit(tree.body)
    return found


def _walk_scope(node):
    """Like ast.walk over ONE statement's own expressions: nested statement
    bodies are visited by _module_bindings, and new scopes are not entered."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.stmt, ast.ExceptHandler) + _SCOPES):
                continue
            stack.append(c)


_REASONS_CURE = {
    "unparseable": "SyntaxError — fix the file",
    "unresolvable": "_read_reason_set cannot read this binding or a member; teach it the form",
}


def _reasons_finding(label, a, b):
    """Classify the reasons leg from each side's ``(state, value)``. A file
    present on both sides with the constant on only one is the repos diverging
    → drift (the shape tier's absent-symbol precedent). Anything this tool
    cannot read → blind, ranked below drift (_overall), with the cure for the
    state that caused it. Absent on BOTH → nothing diverged; the leg's own
    REASONS_CONSTANT is stale → blind."""
    (sa, ra), (sb, rb) = a, b
    sides = f"mf={sa} ma={sb}"
    if sa == sb == "ok":
        if ra == rb:
            return ParityFinding("reasons", label, "ok")
        return ParityFinding("reasons", label, "drift",
            f"(mf-only={sorted(ra - rb)} ma-only={sorted(rb - ra)})")
    if {sa, sb} == {"ok", "absent"}:
        return ParityFinding("reasons", label, "drift",
            f"({sides} — the constant is gone or renamed on one side; port it)")
    if sa == sb == "absent":
        return ParityFinding("reasons", label, "blind",
            f"({sides} — absent in both; REASONS_CONSTANT is stale, update it)")
    cures = [_REASONS_CURE[s] for s in dict.fromkeys((sa, sb)) if s in _REASONS_CURE]
    return ParityFinding("reasons", label, "blind",
        f"({sides} — {'; '.join(cures) or 'cannot compare'})")


def _extract_def_dump(text, name):
    """``ast.dump`` of the module-level function ``name`` with its docstring
    dropped — a BODY fingerprint. Presence-by-name was the whole check before
    2026-09-07, and it could not fire: MF's is_calibrated changed behaviour
    (the self-exempting STRONG_CLAIMS fix) while MA's did not, and this leg
    printed OK for both. None if unparseable or absent (reported 'missing')."""
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            node.body = body
            return ast.dump(node, include_attributes=False)
    return None


def _fork_pin_block(text):
    """Extract the normalized parity-relevant lines from requirements/rns.txt:
    the ``# MF-FORK-PIN`` markers and the ``rns @ git+`` / ``lxmf @ git+``
    requirement lines. Returns a sorted tuple so prose ordering/spacing in
    the surrounding comments doesn't cause false drift."""
    if text is None:
        return None
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if re.match(r"^#\s*MF-FORK-PIN\s+(rns|lxmf)\s", s):
            lines.append(re.sub(r"\s+", " ", s))
        elif re.match(r"^(rns|lxmf)\s*@\s*git\+", s):
            lines.append(re.sub(r"\s+", " ", s))
    return tuple(sorted(lines))


@dataclass
class ParityFinding:
    """One parity comparison result. ``section`` groups the render; ``status`` is
    one of 'ok'|'drift'|'missing'|'blind'; ``detail`` is the formatted line
    suffix. 'blind' = the file is present but this tool cannot read the
    compared value (an extractor limit, not an absent file) — see _overall."""
    section: str   # 'byte' | 'forkpin' | 'shape' | 'probe' | 'reasons' | 'calibgate'
    label: str
    status: str
    detail: str = ""


def check_parity(mf=DEFAULT_MESHFORGE, ma=DEFAULT_MESHANCHOR):
    """Pure parity comparison — no printing. Returns ``(findings, overall)`` where
    ``overall`` is one of:
      'repo_missing' — a repo root isn't a directory (can't compare at all)
      'missing'      — both roots present but a tracked file/symbol is absent,
                       or (with no drift anywhere) a leg is 'blind'
      'drift'        — a must-match file/symbol content-diverged (the real signal)
      'in_sync'      — everything matches
    'missing' takes precedence over 'drift', and 'drift' over 'blind' (_overall).
    ``main()`` renders this; the watchdog ``probe_parity_drift`` consumes it.
    """
    for root in (mf, ma):
        if not os.path.isdir(root):
            return [ParityFinding("repo", root, "missing", "repo root not found")], "repo_missing"

    findings = []

    # ── Byte-identical tier ──
    for rel in BYTE_IDENTICAL:
        a, b = _sha256(os.path.join(mf, rel)), _sha256(os.path.join(ma, rel))
        if a is None or b is None:
            findings.append(ParityFinding("byte", rel, "missing",
                f"(mf={'ok' if a else 'absent'} ma={'ok' if b else 'absent'})"))
        elif a == b:
            findings.append(ParityFinding("byte", rel, "ok"))
        else:
            findings.append(ParityFinding("byte", rel, "drift", f"({a[:12]} != {b[:12]})"))

    # fork-pin sub-block
    fa = _fork_pin_block(_read(os.path.join(mf, FORK_PIN_FILE)))
    fb = _fork_pin_block(_read(os.path.join(ma, FORK_PIN_FILE)))
    label = f"{FORK_PIN_FILE} (fork-pin block)"
    if fa is None or fb is None:
        findings.append(ParityFinding("forkpin", label, "missing"))
    elif fa == fb and fa:
        findings.append(ParityFinding("forkpin", label, "ok"))
    else:
        findings.append(ParityFinding("forkpin", label, "drift", f"\n            mf={fa}\n            ma={fb}"))

    # ── Shape tier ──
    for rel, symbols in SHAPE_SYMBOLS.items():
        ta, tb = _read(os.path.join(mf, rel)), _read(os.path.join(ma, rel))
        if ta is None or tb is None:
            findings.append(ParityFinding("shape", rel, "missing"))
            continue
        for sym in symbols:
            in_a, in_b = sym in ta, sym in tb
            if in_a and in_b:
                findings.append(ParityFinding("shape", f"{rel} :: {sym}", "ok"))
            else:
                # A symbol present in one repo but not the other is real drift, not
                # an absent file. Tag it 'drift' (the CLI counted it that way too).
                findings.append(ParityFinding("shape", f"{rel} :: {sym}", "drift",
                    f"(mf={'y' if in_a else 'n'} ma={'y' if in_b else 'n'})"))

    # probes (different module per repo)
    for root, (rel, syms) in PROBE_SYMBOLS.items():
        actual = mf if root == DEFAULT_MESHFORGE else ma
        rlabel = "meshforge" if root == DEFAULT_MESHFORGE else "meshanchor"
        txt = _read(os.path.join(actual, rel))
        if txt is None:
            findings.append(ParityFinding("probe", f"{rlabel}:{rel}", "missing"))
            continue
        for sym in syms:
            status = "ok" if sym in txt else "drift"
            findings.append(ParityFinding("probe", f"{rlabel}:{rel} :: {sym}", status))

    # ── Delivery-failure reason vocabulary ──
    ta, tb = _read(os.path.join(mf, REASONS_FILE)), _read(os.path.join(ma, REASONS_FILE))
    label = f"{REASONS_FILE} :: {REASONS_CONSTANT}"
    if ta is None or tb is None:
        findings.append(ParityFinding("reasons", label, "missing",
            f"(mf={'ok' if ta is not None else 'absent'} "
            f"ma={'ok' if tb is not None else 'absent'})"))
    else:
        findings.append(_reasons_finding(label, _read_reason_set(ta, REASONS_CONSTANT),
                                         _read_reason_set(tb, REASONS_CONSTANT)))

    # ── Calibration claim-gate shared core ──
    # The detection VOCABULARY must agree or the two gates judge the same closing
    # message differently. Compare the three constant SETS by AST (no import side
    # effects) + assert the core decision fns are present in both. The ledger trio
    # + marker path legitimately differ (MeshAnchor has no mini_dudeai) → not here.
    cga = _read(os.path.join(mf, CLAIM_GATE_FILE))
    cgb = _read(os.path.join(ma, CLAIM_GATE_FILE))
    if cga is None or cgb is None:
        findings.append(ParityFinding("calibgate", CLAIM_GATE_FILE, "missing",
            f"(mf={'ok' if cga else 'absent'} ma={'ok' if cgb else 'absent'})"))
    else:
        for name in CLAIM_GATE_CONSTANTS:
            va = _extract_literal_constant(cga, name)
            vb = _extract_literal_constant(cgb, name)
            label = f"{CLAIM_GATE_FILE} :: {name}"
            if va is None or vb is None:
                findings.append(ParityFinding("calibgate", label, "missing",
                    f"(mf={'ok' if va is not None else 'absent'} "
                    f"ma={'ok' if vb is not None else 'absent'})"))
            elif va == vb:
                findings.append(ParityFinding("calibgate", label, "ok"))
            else:
                findings.append(ParityFinding("calibgate", label, "drift",
                    f"(mf-only={sorted(va - vb)} ma-only={sorted(vb - va)})"))
        for sym in CLAIM_GATE_CORE_SYMBOLS:
            # "def name(" → name; compare the function BODY, not its presence.
            fname = sym[len("def "):].rstrip("(")
            da, db = _extract_def_dump(cga, fname), _extract_def_dump(cgb, fname)
            label = f"{CLAIM_GATE_FILE} :: {sym}"
            if da is None or db is None:
                findings.append(ParityFinding("calibgate", label, "missing",
                    f"(mf={'ok' if da is not None else 'absent'} "
                    f"ma={'ok' if db is not None else 'absent'})"))
            elif da == db:
                findings.append(ParityFinding("calibgate", label, "ok"))
            else:
                findings.append(ParityFinding("calibgate", label, "drift",
                    "(function body differs — port the decision core, then re-run)"))

    return findings, _overall(findings)


def _overall(findings):
    """missing > drift > blind > in_sync. A BLIND leg is this tool failing to
    read a file that is there, so it must never out-rank another leg's real
    drift: the watchdog turns 'missing' into indeterminate + a streak reset,
    and a parser limit would otherwise silence every other leg (review #1,
    2026-10-24). Alone it still cannot be called in sync — it reports
    'missing' (can't compare), keeping the overall vocabulary closed."""
    statuses = {f.status for f in findings}
    if "missing" in statuses:
        return "missing"
    if "drift" in statuses:
        return "drift"
    if "blind" in statuses:
        return "missing"
    return "in_sync"


_TAG = {"ok": "OK     ", "drift": "DRIFT  ", "missing": "MISSING", "blind": "BLIND  "}
_SECTION_HEADER = {
    "byte": "Byte-identical tier:",
    "shape": "\nShape tier (symbol presence in both repos):",
    "probe": "\nRNS-wedge probes (each repo, own idiom):",
    "reasons": "\nDelivery-failure reasons (the set, both twins):",
    "calibgate": "\nCalibration claim-gate shared core (detection vocabulary + decision fns):",
}


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="parity_check",
        description="Check RNS-reliability parity between MeshForge and MeshAnchor.",
    )
    p.add_argument("--meshforge", default=DEFAULT_MESHFORGE)
    p.add_argument("--meshanchor", default=DEFAULT_MESHANCHOR)
    args = p.parse_args(argv)

    mf, ma = args.meshforge, args.meshanchor
    findings, overall = check_parity(mf, ma)

    if overall == "repo_missing":
        for f in findings:
            print(f"[MISSING] {f.detail}: {f.label}")
        return 2

    print(f"RNS parity: {mf}  <->  {ma}\n")
    seen_sections = set()
    for f in findings:
        # byte + forkpin render under the byte header; print headers once.
        header_key = "byte" if f.section == "forkpin" else f.section
        if header_key not in seen_sections:
            print(_SECTION_HEADER[header_key])
            seen_sections.add(header_key)
        suffix = f"  {f.detail}" if f.detail else ""
        print(f"  [{_TAG[f.status]}] {f.label}{suffix}")

    print()
    if overall == "missing":
        if not any(f.status == "missing" for f in findings):
            print("RESULT: BLIND — a leg this tool cannot read; cannot fully compare.")
        else:
            print("RESULT: MISSING files/symbols — cannot fully compare.")
        return 2
    if overall == "drift":
        print("RESULT: DRIFT — port the flagged changes (MeshForge is the lead repo).")
        return 1
    print("RESULT: in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
