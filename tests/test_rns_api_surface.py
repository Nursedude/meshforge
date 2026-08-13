"""Every RNS/LXMF attribute our source CALLS must exist on the pinned fork.

WHY (2026-08-12). ``_rns_bridge_connection._disconnect_rns`` called
``RNS.Transport.exithandler()``. That spelling exists on no RNS we have ever
pinned — the real name is ``exit_handler`` — so the call raised AttributeError
on EVERY gateway shutdown, and the ``except Exception`` around it logged the
failure at DEBUG and moved on. Found only by reading the journal of a gateway
that had just been restarted by hand; both gateway boxes had been doing it for
as long as the line existed, and MeshAnchor's twin carried the identical bug.

This is the honest_failure_modes class in its purest form: a broken call whose
only witness is a DEBUG line nobody greps. RNS and LXMF are MeshForge-OWNED
FORKS that we periodically merge upstream into (see persistent_issues, the
1.3.8/1.0.1 arc, whose notes already record call sites that had to be
RE-PORTED rather than carried). A rename landing in a swallowed except is
therefore a standing hazard of the fork strategy, not a one-off typo — so it
gets a gate rather than a fixed line.

⚠️ AST, not grep. A regex sweep over the same tree reported 6 hits of which 5
were false: a docstring describing ``RNS.Reticulum.resourcepath``, a
``hasattr``-guarded ``RNS.Transport._packet_filter``, the string literal
``"RNS.Utilities.rnsd"`` matched against a process cmdline, ``Protocol.RNS.value``
(an enum member whose tail merely looks like the module), and another
docstring. A guard with a 5-in-6 false-positive rate gets muted, and a muted
guard is worse than none. Walking real ``ast.Attribute`` chains rooted at the
NAME ``RNS``/``LXMF`` drops all five without an allowlist entry.

⚠️ SKIPS when RNS is not importable, and a skip is NOT a pass. CI installs the
minimal-deps profile, so this gate does its work on the fleet and on any dev
box with the fork installed. That is stated out loud rather than left for
someone to discover from a green tick.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC_DIR)

#: (module, dotted path) -> why it is legitimately absent from the live fork.
#: Never add an entry to silence a finding; add it only when the CODE already
#: proves it tolerates absence.
ALLOWED_ABSENT = {
    ("RNS", "Transport._packet_filter"):
        "private upstream attr, and the call site is guarded by "
        "hasattr(RNS.Transport, '_packet_filter') before every use — the "
        "code already treats absence as normal (rns_sniffer.py)",
}


def _annotate_parents(tree):
    """ast gives no parent links, and the longest-chain skip in
    :func:`_rns_attr_paths` is meaningless without them. Defined ABOVE its
    caller and actually called — the first cut of this file defined it and
    never invoked it, so the skip was dead code and every chain was also
    reported as its own prefixes. Passing tests did not notice, because a
    prefix of a resolving path resolves too."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent


def _rns_attr_paths():
    """(module, dotted_path, 'file:line') for every real attribute access
    rooted at the NAME ``RNS`` or ``LXMF``."""
    out = []
    for dirpath, dirnames, files in os.walk(SRC_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (OSError, SyntaxError):
                continue
            _annotate_parents(tree)   # the longest-chain skip below needs this
            rel = os.path.relpath(path, REPO_ROOT)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                parts = []
                cur = node
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if not isinstance(cur, ast.Name) or cur.id not in ("RNS", "LXMF"):
                    continue
                # Only the LONGEST chain per expression: an outer Attribute
                # already covers its inner prefixes, and checking prefixes
                # separately would double-report one site.
                if isinstance(getattr(node, "parent", None), ast.Attribute):
                    continue
                out.append((cur.id, ".".join(reversed(parts)),
                            f"{rel}:{node.lineno}"))
    return out


@pytest.fixture(scope="module")
def rns_mods():
    mods = {}
    for name in ("RNS", "LXMF"):
        mods[name] = pytest.importorskip(
            name,
            reason=(f"{name} not installed here (CI runs the minimal-deps "
                    f"profile). SKIPPED IS NOT PASSED — this gate enforces on "
                    f"the fleet and on dev boxes carrying the fork."),
        )
    return mods


class TestRNSApiSurfaceResolves:

    def test_scanner_is_not_vacuous(self):
        """A scan that finds nothing would pass this file forever."""
        paths = _rns_attr_paths()
        assert len(paths) >= 20, (
            f"only {len(paths)} RNS/LXMF attribute access(es) found — the AST "
            f"walker broke and this gate enforces nothing")

    def test_scanner_ignores_prose_and_lookalikes(self):
        """The five false positives that killed the regex version must not
        come back. Each is a real construct in this tree."""
        found = {p for _m, p, _s in _rns_attr_paths()}
        # `Protocol.RNS.value` is rooted at the name `Protocol`, not `RNS`.
        assert "value" not in found
        # These two appear ONLY inside docstrings / string literals.
        assert "Reticulum.resourcepath" not in found
        assert "Utilities.rnsd" not in found

    def test_every_called_attribute_exists_on_the_pinned_fork(self, rns_mods):
        unresolved = []
        for mod, path, site in _rns_attr_paths():
            if (mod, path) in ALLOWED_ABSENT:
                continue
            obj, walked, ok = rns_mods[mod], mod, True
            for part in path.split("."):
                if not hasattr(obj, part):
                    ok = False
                    break
                obj = getattr(obj, part)
                walked += "." + part
            if not ok:
                unresolved.append(f"{mod}.{path} at {site} "
                                  f"(resolves only to {walked})")
        assert not unresolved, (
            "source calls RNS/LXMF attributes that do not exist on the "
            "installed fork:\n  " + "\n  ".join(sorted(set(unresolved))) +
            "\n\nRNS/LXMF are MeshForge-owned forks we merge upstream into, so "
            "a rename lands here as an AttributeError inside whatever except "
            "wraps the call — which is exactly how RNS.Transport.exithandler() "
            "failed on every gateway shutdown, unseen, until 2026-08-12. Fix "
            "the call site, or add an ALLOWED_ABSENT entry ONLY if the code "
            "provably tolerates absence.")

    def test_the_original_defect_stays_fixed(self, rns_mods):
        """Named pin for the bug that motivated the gate."""
        assert not hasattr(rns_mods["RNS"].Transport, "exithandler"), (
            "upstream grew an `exithandler` — re-read this gate's premise")
        assert hasattr(rns_mods["RNS"].Transport, "exit_handler")
        src = open(os.path.join(SRC_DIR, "gateway",
                                "_rns_bridge_connection.py"),
                   encoding="utf-8").read()
        assert "RNS.Transport.exit_handler()" in src
        assert "RNS.Transport.exithandler()" not in src

    def test_allowlist_has_no_stale_entries(self):
        """An exemption that outlives its call site reads as sanctioned."""
        live = {(m, p) for m, p, _s in _rns_attr_paths()}
        stale = [f"{m}.{p}" for (m, p) in ALLOWED_ABSENT if (m, p) not in live]
        assert not stale, (
            f"ALLOWED_ABSENT entries with no remaining call site: {stale} — "
            f"delete them so the allowlist keeps meaning what it says")
