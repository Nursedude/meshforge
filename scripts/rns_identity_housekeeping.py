#!/usr/bin/env python3
"""RNS identity house cleaning — report from measured use, remove only on request.

Operator 2026-10-07: "if a hash is stale then it needs to go — like pids —
there needs to be a connection, a measurable use"; window 14 days; NO
auto-remove. A firm remove is cheap: a hash that is still useful
re-announces, and under observe its next message lands in the ingress
ledger, named by the identity registry — evidence to add it back.

Report (default, read-only): one row per hash in this box's gateway.json
``rns.bridge_source_identities`` ∪ ``default_lxmf_destination`` ∪
``peer_gateway_destinations`` — name (lxmf_identities.json / lab_peers),
roles, last VALIDATED inbound message (from the ingress ledger), verdict:

  active               inbound within the window
  stale                no inbound for the whole window, AND the window was watched
  too early            the ledger has not watched a full window yet
  unknown (no ledger)  nothing measured — never "stale"

⚠️ The quantity is INBOUND. A delivery destination may be used only
outbound; its row says so. Removal is a judgement, this script only measures.

Remove (explicit, full 32-hex hashes only):
  rns_identity_housekeeping.py --remove <hash> [<hash> ...]
backs gateway.json up to ``gateway.json.bak-housekeeping-<stamp>``, drops
each hash from every list above, writes atomically. Restart the gateway
for it to take effect.

Run as the gateway's user on the gateway box.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from gateway.lxmf_identity_registry import load_registry  # noqa: E402
from gateway.rns_ingress_policy import (  # noqa: E402
    LEDGER_FILENAME, USE_STALE_S, project_ledger_doc)

LISTS = (("bridge_source_identities", "allowlist"),
         ("default_lxmf_destination", "destination"),
         ("peer_gateway_destinations", "peer"))
_HEX = set("0123456789abcdef")


def _is_hash(h: object) -> bool:
    return isinstance(h, str) and len(h) == 32 and set(h.lower()) <= _HEX


def _as_list(v: object) -> List[str]:
    if isinstance(v, str):
        return [v] if v else []
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def default_config_dir() -> Path:
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshforge"


def default_ledger() -> Path:
    from gateway.rns_ingress_policy import default_ledger_path
    return default_ledger_path()


def _load_gateway(cfg_dir: Path) -> dict:
    return json.loads((cfg_dir / "gateway.json").read_text(encoding="utf-8"))


def build_rows(cfg_dir: Path, ledger_path: Path, *, now: float) -> List[dict]:
    rns = _load_gateway(cfg_dir).get("rns") or {}
    roles: Dict[str, List[str]] = {}
    for key, role in LISTS:
        for h in _as_list(rns.get(key)):
            roles.setdefault(h.lower(), [])
            if role not in roles[h.lower()]:
                roles[h.lower()].append(role)
    reg = load_registry(cfg_dir)
    use = None
    try:
        doc = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
        use = project_ledger_doc(doc, now=now).get("use")
    except (OSError, ValueError):
        use = None
    days = int(USE_STALE_S // 86400)
    rows = []
    for h, rl in roles.items():
        last = (use or {}).get("last_inbound", {}).get(h)
        if use is None:
            verdict = "unknown (no ledger)"
        elif not use.get("judgeable"):
            verdict = "too early"
        elif last is not None and now - last <= USE_STALE_S:
            verdict = "active"
        else:
            verdict = "stale"
        note = ""
        if verdict == "stale" and "destination" in rl:
            note = ("also a delivery destination — may be used OUTBOUND; "
                    "inbound is all this measures")
        rows.append({"hash": h, "name": reg.label(h), "roles": rl,
                     "last_inbound_days": (None if last is None
                                           else round((now - last) / 86400, 1)),
                     "verdict": verdict, "note": note,
                     "observed_days": (None if not use or use.get("observed_s") is None
                                       else round(use["observed_s"] / 86400, 1)),
                     "window_days": days})
    order = {"stale": 0, "too early": 1, "unknown (no ledger)": 2, "active": 3}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), r["name"]))
    return rows


def remove(cfg_dir: Path, hashes: List[str], *, stamp: str) -> Dict[str, List[str]]:
    """Drop each named hash from every list; backup first; atomic write."""
    bad = [h for h in hashes if not _is_hash(h)]
    if bad:
        raise ValueError(f"full 32-hex hashes only (refusing {bad})")
    want = {h.lower() for h in hashes}
    path = cfg_dir / "gateway.json"
    raw = path.read_text(encoding="utf-8")
    gw = json.loads(raw)
    rns = gw.get("rns") or {}
    found: Dict[str, List[str]] = {h: [] for h in want}
    for key, _ in LISTS:
        cur = rns.get(key)
        if isinstance(cur, str):
            cur = [cur] if cur else []
        if not isinstance(cur, list):
            continue
        keep = []
        for x in cur:
            if isinstance(x, str) and x.lower() in want:
                found[x.lower()].append(key)
            else:
                keep.append(x)
        rns[key] = keep
    missing = [h for h, where in found.items() if not where]
    if missing:
        raise ValueError(f"not in any list on this box: {missing}")
    backup = cfg_dir / f"gateway.json.bak-housekeeping-{stamp}"
    backup.write_text(raw, encoding="utf-8")
    mode = os.stat(path).st_mode & 0o777
    tmp = cfg_dir / f".gateway.json.housekeeping-{os.getpid()}"
    tmp.write_text(json.dumps(gw, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return found


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config-dir", type=Path, default=None)
    ap.add_argument("--ledger", type=Path, default=None)
    ap.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--remove", nargs="+", metavar="HASH")
    a = ap.parse_args(argv)
    cfg_dir = a.config_dir or default_config_dir()
    if a.remove:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            found = remove(cfg_dir, a.remove, stamp=stamp)
        except ValueError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        for h, where in found.items():
            print(f"removed {h} from {', '.join(where)}")
        print(f"backup: gateway.json.bak-housekeeping-{stamp} — restart the "
              f"gateway to apply")
        return 0
    rows = build_rows(cfg_dir, a.ledger or default_ledger(),
                      now=a.now if a.now is not None else time.time())
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if rows:
        r0 = rows[0]
        print(f"RNS identity use — window {r0['window_days']}d, watched "
              f"{r0['observed_days'] if r0['observed_days'] is not None else '?'}d "
              f"(INBOUND, validated signatures only)")
    for r in rows:
        last = ("never" if r["last_inbound_days"] is None
                else f"{r['last_inbound_days']}d ago")
        print(f"  {r['verdict']:<19} {r['hash'][:8]}  {r['name'][:32]:<32} "
              f"{'+'.join(r['roles']):<22} last inbound {last}"
              + (f"  ⚠ {r['note']}" if r["note"] else ""))
    stale = [r["hash"] for r in rows if r["verdict"] == "stale"]
    if stale:
        print("\nTo remove (your call, explicit):\n  "
              f"{Path(__file__).name} --remove " + " ".join(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
