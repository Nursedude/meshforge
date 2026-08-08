#!/usr/bin/env python3
"""Prove probe_oracle_delivery_degraded's FIRE path works on a real box.

Not part of the test suite — it needs a box with a real oracle audit log, and
it runs the probe against copies of it. Run it where the oracle is enrolled:

    sudo env PYTHONPATH=<repo>/src python3 scripts/oracle_fire_drill.py

Why it exists: the quiet path (idle / stale / clean) is verified live on every
tick, but no oracle on this fleet has ever produced a ``send_error`` — so the
PAGE itself had never been observed outside unit tests. Tests pin the model of
the code; only a drill pins the code (2026-08-08, build:fix doctrine). This
runs the DEPLOYED probe under the watchdog's own uid and interpreter against
the box's own log content, with synthetic failures appended to a COPY.

⚠️ The real log is opened READ-ONLY, never moved, never appended to; the drill
asserts its size and mtime are unchanged at the end. Synthetic records are
stamped ``from: DRILLSYNTH`` so a stray copy can never be mistaken for
telemetry.

Cases:
  1. baseline   — untouched copy: no signal, inert/stale
  2. stale-fail — 6 send_errors 3 DAYS old: no signal (staleness guard), inert
                  with the degraded rate still reported in the reason
  3. old-fail   — 5 send_errors 30 days old + 3 fresh deliveries: no signal
                  (fresh-failure guard), CLEAN — nothing recent failed
  4. fresh-fail — 6 send_errors stamped NOW: held by debounce on tick 1,
                  FIRES on tick 2

Exit 0 = every check passed.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes import probe_oracle_delivery_degraded  # noqa: E402
from utils.watchdog_probes_gateway import _resolve_oracle_log_path  # noqa: E402

_fails = 0


def _check(label: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  " + detail) if detail else ""))
    if not cond:
        _fails += 1


def _rec(now: float, delivered: bool, age_s: float, reason=None) -> dict:
    r = {"ts": now - age_s, "transport": "rns", "from": "DRILLSYNTH",
         "query": "drill", "intent": "status", "answer": "drill",
         "delivered": delivered}
    if reason:
        r["reason"] = reason
    return r


def _case(real_log: str, scratch: str, name: str, extra_records):
    """Copy the real log, append synthetic records, run the probe twice."""
    log = os.path.join(scratch, name + ".jsonl")
    shutil.copyfile(real_log, log)
    with open(log, "a", encoding="utf-8") as fh:
        for r in extra_records:
            fh.write(json.dumps(r) + "\n")
    dbp = os.path.join(scratch, name + ".debounce.json")
    out = []
    for _ in range(2):
        reset_dispositions()
        sig = probe_oracle_delivery_degraded(log_path=log, debounce_path=dbp)
        out.append((sig, collect_dispositions().get("oracle_delivery_degraded")))
    return out


def main() -> int:
    real = _resolve_oracle_log_path()
    if not real or not os.path.exists(real):
        print("SKIP: no oracle audit log on this box — run the drill where the "
              "oracle is enrolled (the probe is INERT here by design).")
        return 0

    before = (os.path.getsize(real), int(os.path.getmtime(real)))
    scratch = tempfile.mkdtemp(prefix="oracle_fire_drill_")
    now = time.time()
    print("== interpreter:", sys.executable, "uid:", os.getuid())
    print("== real log (read-only):", real, before[0], "bytes, mtime", before[1])
    print("== scratch:", scratch)

    try:
        print("\n[1] baseline — untouched copy of this box's log")
        (s1, c1), _ = _case(real, scratch, "baseline", [])
        print("   ", s1, c1)
        _check("no signal", s1 is None)
        _check("quiet disposition is inert (not indeterminate)",
               c1["disp"] == "inert", c1.get("reason", ""))

        print("\n[2] stale failures — 6 send_error stamped 3 days ago")
        (s2, c2), _ = _case(real, scratch, "stale",
                            [_rec(now, False, 3 * 86400, "send_error: drill")
                             for _ in range(6)])
        print("   ", s2, c2)
        _check("no signal (staleness guard)", s2 is None)
        _check("inert, and the degraded rate is still reported",
               c2["disp"] == "inert" and "BELOW" in c2["reason"],
               c2.get("reason", ""))

        print("\n[3] old failures inside a FRESH sample — 5 @30d + 3 delivered now")
        (s3, c3), _ = _case(real, scratch, "oldfail",
                            [_rec(now, False, 30 * 86400, "send_error: drill")
                             for _ in range(5)]
                            + [_rec(now, True, 60) for _ in range(3)])
        print("   ", s3, c3)
        _check("no signal (fresh-failure guard)", s3 is None)
        _check("clean — recent answers all landed", c3["disp"] == "clean",
               c3.get("reason", ""))

        print("\n[4] fresh failures — 6 send_error stamped now (THE fire path)")
        (s4a, c4a), (s4b, _c4b) = _case(
            real, scratch, "freshfail",
            [_rec(now, False, 30, "send_error: drill") for _ in range(6)])
        print("    tick1:", s4a, c4a)
        _check("tick 1 held by debounce",
               s4a is None and c4a["disp"] == "indeterminate")
        _check("tick 2 FIRES", s4b is not None)
        if s4b is not None:
            e = s4b.extra
            _check("class / severity / subject",
                   s4b.cls == "oracle_delivery_degraded"
                   and s4b.severity == "degraded"
                   and s4b.subject == "mesh-oracle")
            _check("sample is the last 8 confirmable",
                   e["confirmable"] == e["sample_n"], str(e["confirmable"]))
            _check("all 6 synthetic failures counted as FRESH",
                   e["fresh_send_errors"] == 6, str(e["fresh_send_errors"]))
            _check("rate = delivered / (delivered + send_errors)",
                   abs(e["rate"] - (e["delivered"] / e["confirmable"])) < 1e-9,
                   str(e["rate"]))
            _check("debounce_streak == 2", e.get("debounce_streak") == 2)
            print("    detail:", s4b.detail)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    after = (os.path.getsize(real), int(os.path.getmtime(real)))
    _check("the REAL log was not touched", after == before,
           f"{before} -> {after}")
    print("\nRESULT:", "ALL PASS" if _fails == 0 else f"{_fails} CHECK(S) FAILED")
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
