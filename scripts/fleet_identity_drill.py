#!/usr/bin/env python3
"""fleet_identity_drill.py — prove the name→node identity check can FAIL.

WHY A DRILL AND NOT A UNIT TEST (2026-09-11)
--------------------------------------------
The defect this whole mechanism addresses was found in a detector that had
thirteen passing unit tests: they mocked the resolver, so the mock stood in
for the exact layer that was broken (the 2026-07-25 self-confirming-checker
lesson). And `fleet_naming_drift_check` has run green every morning for weeks
while being structurally incapable of seeing a node swap — a guard that has
never been observed to fail is not evidence that it works
(`feedback_a_guard_that_never_failed_is_not_evidence`).

So the acceptance criterion for the identity leg is NOT "the check passes".
It is: **plant a swapped identity and watch the real chain reject it.**

This drill runs the REAL scripts against a SCRATCH COPY of the live registry
— real resolution through /etc/hosts, real ssh-keyscan against real hosts.
Only the planted fingerprint is synthetic. The live registry is never
touched, never written, never read for anything but a copy.

Exit 0 only if every phase behaved: the clean copy passes, the planted swap
FAILS with identity_mismatch, and an unreachable host stays UNDECLARED
rather than becoming a mismatch.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from utils.fleet_naming import load_registry, registry_path  # noqa: E402

DRIFT = _HERE / "fleet_naming_drift_check.py"
PLANTED = "SHA256:PLANTEDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TIMEOUT_S = 400


def run_drift(reg: Path, identity: bool) -> tuple[int, str]:
    cmd = [sys.executable, str(DRIFT), "--registry", str(reg)]
    if identity:
        cmd.append("--verify-identity")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    live = registry_path()
    registry, errs = load_registry(str(live))
    if registry is None:
        print(f"UNKNOWN: live registry unusable ({'; '.join(errs)}) — "
              f"cannot drill against a registry that does not load")
        return 2

    stamped = sorted(a for a, h in registry.hosts.items() if h.expect_hostkey)
    if not stamped:
        print("UNKNOWN: no alias carries an expect_hostkey yet — the identity "
              "leg has nothing to verify, so this drill cannot prove anything. "
              "Run scripts/fleet_hostkey_stamp.py --apply first.")
        return 2

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "fleet_naming.json"
        doc = json.loads(live.read_text(encoding="utf-8"))

        # ---- phase 1: the unmodified copy behaves like the live one -------
        scratch.write_text(json.dumps(doc), encoding="utf-8")
        rc_clean, out_clean = run_drift(scratch, identity=True)
        print(f"[phase 1] clean copy       rc={rc_clean}  {out_clean[:160]}")
        if rc_clean == 2:
            print("UNKNOWN: the chain could not run at all — drill inconclusive")
            return 2

        # ---- phase 2: plant a swapped identity on ONE stamped alias -------
        # This is the 2026-09-11 event in miniature: the name still resolves,
        # the address still matches the registry, and the box behind it is
        # somebody else.
        victim = stamped[0]
        doc["hosts"][victim]["expect_hostkey"] = PLANTED
        scratch.write_text(json.dumps(doc), encoding="utf-8")
        rc_swap, out_swap = run_drift(scratch, identity=True)
        print(f"[phase 2] planted swap     rc={rc_swap}  {out_swap[:200]}")
        if rc_swap != 1:
            failures.append(
                f"planting a wrong host key on {victim!r} did NOT make the "
                f"check fail (rc={rc_swap}) — the identity leg is not wired "
                f"to the verdict")
        if "identity_mismatch" not in out_swap:
            failures.append(
                f"the failure did not name identity_mismatch for {victim!r}; "
                f"a drift that cannot say WHAT moved is half a detector")

        # ---- phase 3: the SAME planted registry without the flag ----------
        # Proves the finding comes from the identity leg and not from some
        # other drift the copy happened to carry.
        rc_noflag, out_noflag = run_drift(scratch, identity=False)
        print(f"[phase 3] swap, flag off   rc={rc_noflag}  {out_noflag[:160]}")
        if rc_noflag == 1 and "identity_mismatch" in out_noflag:
            failures.append(
                "the no-flag run also reported identity_mismatch — the flag "
                "is not actually gating the keyscan")
        if rc_noflag == 0 and rc_swap == 1:
            print("           ^ exactly the blindness this closes: the same "
                  "swapped registry reads OK without --verify-identity")

        # ---- phase 4: unreachable must stay UNDECLARED, never MISMATCH ----
        # An absent observation rendered as a finding would be the #80 class
        # wearing this mechanism's clothes.
        doc2 = json.loads(live.read_text(encoding="utf-8"))
        doc2["hosts"].setdefault("drill-nonexistent", {})
        doc2["hosts"]["drill-nonexistent"] = {
            "ip_fallback": "192.0.2.254", "expect_hostkey": PLANTED}
        scratch.write_text(json.dumps(doc2), encoding="utf-8")
        rc_dark, out_dark = run_drift(scratch, identity=True)
        print(f"[phase 4] unreachable host rc={rc_dark}  {out_dark[:200]}")
        if "identity_mismatch" in out_dark:
            failures.append(
                "an unreachable host produced identity_mismatch — a failed "
                "observation must read UNKNOWN, never a verdict about identity")

    if failures:
        print("\nFAIL: the identity leg did not behave under drill:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: identity leg proven falsifiable — a planted node swap fails "
          "the real chain, and an unobservable host does not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
