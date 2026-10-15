"""Operator line (BIC-15) — the status file's ONE schema and its renderer.

The line lives inside the gateway process; the TUI is another process. The
line publishes ``operator_line_status.json`` (writer:
``gateway.operator_line.OperatorLine.publish_status``) and this module turns
it into pane lines. Both halves import ``STATUS_SCHEMA`` from here
(honest_failure_modes #5: one constant for two consumers).

Disposition, in order:
  * line OFF in gateway.json → ``OFF`` with the knob — absent by design is
    inert, never UNKNOWN;
  * ON but no report, a wrong schema, a stale report or a future-stamped one
    → ``UNKNOWN`` with what was seen — never the last-known state as current
    (honest_failure_modes #2, #6);
  * a fresh report → RUNNING / NOT RUNNING with the reason.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_SCHEMA = "operator_line_status/v1"
STATUS_FILENAME = "operator_line_status.json"
#: A report is fresh within this many publish intervals (min floor below).
STALE_TICKS = 4
STALE_FLOOR_S = 120.0
#: A stamp this far in the future is a clock fault, not a fresh report.
FUTURE_SLACK_S = 60.0

_REVOKE = ("revoke: remove the hash from gateway.json operator_line.operators, "
           "then restart the gateway")


def read_status(path: Path) -> Optional[Dict[str, Any]]:
    """The published document, or None when absent/unreadable/not an object."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _age(seconds: float) -> str:
    s = int(seconds)
    if s < 120:
        return f"{s}s ago"
    if s < 7200:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


def render_lines(enabled: bool, doc: Optional[Dict[str, Any]], *, now: float) -> List[str]:
    """Pane lines for the line's config switch + its last published report."""
    if not enabled:
        return ["Operator line: OFF",
                "  enable: gateway.json operator_line.enabled = true, list your",
                "  LXMF hash in operator_line.operators, restart the gateway"]
    if doc is None:
        return ["Operator line: UNKNOWN — enabled, but no status report found",
                "  the gateway has not published one (gateway not running, or",
                "  started before this version)"]
    if doc.get("schema") != STATUS_SCHEMA:
        return [f"Operator line: UNKNOWN — status report schema {doc.get('schema')!r},"
                f" expected {STATUS_SCHEMA!r}"]
    try:
        written = float(doc.get("written_at"))
    except (TypeError, ValueError):
        return ["Operator line: UNKNOWN — status report carries no time stamp"]
    try:
        poll = float(doc.get("alert_poll_s") or 30.0)
    except (TypeError, ValueError):
        poll = 30.0
    limit = max(STALE_FLOOR_S, STALE_TICKS * poll)
    age = now - written
    if age < -FUTURE_SLACK_S:
        return [f"Operator line: UNKNOWN — report stamped {int(-age)}s in the FUTURE"
                " (clock stepped?)"]
    if age > limit:
        return [f"Operator line: UNKNOWN — last report {int(age)}s old (fresh < {int(limit)}s);",
                "  the gateway stopped publishing — is it running?"]

    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    lines: List[str] = []
    if doc.get("running") is True:
        lines.append("Operator line: RUNNING")
    else:
        why = doc.get("identity_error") or "start failed — see the gateway journal"
        lines.append("Operator line: NOT RUNNING")
        lines.append(f"  reason: {why}")
    dest = doc.get("destination") or "(none)"
    lines.append(f"  address:    {dest}")
    ops = doc.get("operators", 0)
    lines.append(f"  operators: {ops}" + ("  — CLOSED, answers nobody" if doc.get("closed") else ""))
    last = doc.get("last_page_at")
    try:
        lines.append(f"  last page:  {_age(now - float(last))}" if last is not None
                     else "  last page:  none since start")
    except (TypeError, ValueError):
        lines.append("  last page:  UNKNOWN (unreadable stamp)")
    if not doc.get("alerts_enabled", True):
        lines.append("  paging:     OFF (alerts_enabled false)")
    elif doc.get("watchdog_blind"):
        lines.append("  paging:     watchdog BLIND — paged once, holding last-known")
    lines.append(f"  answered {stats.get('answered', 0)} · pages {stats.get('pages', 0)}"
                 f" · deliver failed {stats.get('deliver_failed', 0)}")
    lines.append(f"  refused: strangers {stats.get('strangers', 0)} · unverified "
                 f"{stats.get('unverified', 0)} · identity {stats.get('identity_refused', 0)}")
    errs = doc.get("config_errors") or []
    if errs:
        lines.append(f"  config errors: {len(errs)} — {errs[0]}")
    if stats.get("status_write_errors"):
        lines.append(f"  status writes failed: {stats['status_write_errors']}")
    lines.append(f"  report {_age(max(0.0, age))}")
    lines.append("  " + _REVOKE)
    return lines
