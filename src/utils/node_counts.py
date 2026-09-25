"""Node counts for this box — one source per number, each with its provenance.

Born 2026-09-25 from the live-truth pass over Dashboard › Node Count, which
was wrong BY CONSTRUCTION on every meshtasticd box:
  * "RNS destinations" counted lines starting with '<' in `rnstatus -a` — a
    command that lists INTERFACES, not destinations — so it read 0 while the
    path table held 43 network destinations (Stack Health, same box).
  * "Meshtastic" asked meshtasticd's /json/nodes, an ESP32-only API that
    meshtasticd never serves (#76), so it always read "HTTP API unavailable".
Stack Health and Node Count now share `rns_path_table_counts()`, so the two
screens cannot disagree again (honest_failure_modes #5).

Every count is a tri-state: a number with its source, or None with WHY — never
0 for "could not ask".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from typing import Dict, Optional, Tuple

RNS_CONFIG_DIR = "/etc/reticulum"
MAP_STATUS_URL = "http://127.0.0.1:5000/api/status"


def parse_rnpath_table(text: str) -> Tuple[int, int]:
    """(network destinations, local IPC peers) from `rnpath -t` output. Pure.

    "LocalInterface" rows are shared-instance IPC peers (other local
    processes connected to rnsd), not network destinations."""
    lines = [ln for ln in text.splitlines() if " is " in ln and " away via " in ln]
    ipc = sum(1 for ln in lines if "LocalInterface" in ln)
    return len(lines) - ipc, ipc


def rns_path_table_counts(timeout: int = 10) -> Dict:
    """{"network": n, "ipc": m, "source": ...} or {"network": None, "why": ...}."""
    rnpath = shutil.which("rnpath")
    if not rnpath:
        return {"network": None, "ipc": None, "why": "rnpath command not installed"}
    try:
        proc = subprocess.run([rnpath, "--config", RNS_CONFIG_DIR, "-t"],
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"network": None, "ipc": None,
                "why": "rnpath timed out — rnsd may be unresponsive to RPC"}
    except OSError as e:
        return {"network": None, "ipc": None, "why": f"rnpath could not run ({e})"}
    network, ipc = parse_rnpath_table(proc.stdout or "")
    if proc.returncode != 0 and network == 0 and ipc == 0:
        return {"network": None, "ipc": None, "why": f"rnpath exited {proc.returncode}"}
    return {"network": network, "ipc": ipc, "source": "rnsd path table (rnpath -t)"}


def meshtastic_radio_nodes(timeout: float = 4.0) -> Dict:
    """The local radio's node-DB size as the map's collector last read it.

    {"count": n, "source": ...} or {"count": None, "why": ...}. The map reads
    meshtasticd over its own guarded TCP path; this never opens a second
    session to the radio (#17)."""
    try:
        with urllib.request.urlopen(MAP_STATUS_URL, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8", "replace"))
    except OSError as e:
        return {"count": None, "why": f"local map server not reachable ({e.__class__.__name__}) "
                                      "— it is what reads the radio's node DB"}
    except ValueError:
        return {"count": None, "why": "local map server returned non-JSON"}
    src = (doc.get("source_diagnostics") or {}).get("meshtasticd") or {}
    if not src:
        return {"count": None, "why": "the map's status carries no meshtasticd collector"}
    if src.get("reason_if_zero") not in (None, "ok"):
        return {"count": None, "why": f"map collector: {src.get('reason_if_zero')}"}
    return {"count": int(src.get("yielded", 0)),
            "source": f"what the map's collector received from the radio ({src.get('notes', 'tcp')}) "
                      "— the map's view, not the radio's own total"}


def radio_self_report(timeout: int = 10) -> Dict:
    """The radio's OWN node count — the authority — from its telemetry line in
    the meshtasticd journal ("Sending local stats: … num_online_nodes=N,
    num_total_nodes=M"), with the line's age. Never opens a session to the
    radio (#17). Measured 2026-09-25: the radio said 90 online / 334 known
    while the map collector held 191 — the map's number is ITS view, and was
    first mislabelled as the radio's node DB by this very pass."""
    import re
    import time
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "meshtasticd", "-n", "4000", "--no-pager", "-q", "-o", "short-unix"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"online": None, "why": f"journal could not be read ({e.__class__.__name__})"}
    last = None
    for ln in (proc.stdout or "").splitlines():
        if "Sending local stats" in ln:
            last = ln
    if last is None:
        why = ("no telemetry line in the journal (not readable by this user?)"
               if proc.returncode or not proc.stdout else
               "the radio has not reported local stats in the recent journal")
        return {"online": None, "why": why}
    m_on = re.search(r"num_online_nodes=(\d+)", last)
    m_tot = re.search(r"num_total_nodes=(\d+)", last)
    try:
        ts = float(last.split()[0])
    except (ValueError, IndexError):
        ts = None
    if not (m_on and m_tot):
        return {"online": None, "why": "telemetry line carries no node counts"}
    return {"online": int(m_on.group(1)), "total": int(m_tot.group(1)),
            "age_s": (time.time() - ts) if ts else None,
            "source": "the radio's own telemetry (meshtasticd journal)"}


def directory_counts(timeout: float = 4.0) -> Optional[Dict[str, int]]:
    """Nodes SEEN over the map's retention window, per network (history, not now)."""
    try:
        with urllib.request.urlopen(MAP_STATUS_URL, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    d = doc.get("directory") or {}
    return {"by_network": d.get("by_network") or {},
            "retention_local_days": d.get("retention_local_days")}
