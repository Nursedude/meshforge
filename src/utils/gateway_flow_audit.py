"""Gateway Flow Audit — runtime diagnostic for a MeshForge gateway box.

Automates the manual investigation captured in
``docs/gateway_traffic_flow_analysis_2026_05_08.md``. Detects the most
common gateway-side failure modes from journal + config + service state:

- Service health (meshforge-gateway, meshtasticd, rnsd, mosquitto)
- Bridge counter snapshot (M→R, R→M attempted/delivered/dropped)
- R→M dedup misclassification (project_rns_to_mesh_queue_drop)
- R→M ECONNREFUSED idle-keep-alive (docs flow analysis root cause #2)
- :4403 contention (Issue #53)
- rpc_key alignment (Issue #41)
- Channel config consistency (Issue #42)

Companion to ``gateway_diagnostic.py`` which covers setup-time checks
(is meshtasticd installed, is the venv right). This module covers
runtime flow once everything is deployed.

Run on a gateway box (reads local journalctl). Output is a Markdown
memo of the same shape as the manual investigation memo, suitable for
operator review or saving to ``~/.local/share/meshforge/audits/``.

Usage::

    python3 -m utils.gateway_flow_audit
    python3 -m utils.gateway_flow_audit --lookback 4h
    python3 -m utils.gateway_flow_audit --out /tmp/audit.md
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from utils.gateway_diagnostic import CheckResult, CheckStatus
from utils.paths import get_real_user_home
from utils.service_check import check_service

logger = logging.getLogger(__name__)

_COUNTER_RE = re.compile(
    r"attempted/delivered/dropped — M->R: (\d+)/(\d+)/(\d+)\s+R->M: (\d+)/(\d+)/(\d+)"
)
_TORADIO_RAISED_RE = re.compile(r"toradio_put\[[\da-f]+\]\] raised after")


@dataclass
class AuditResult:
    """Findings from a gateway flow audit."""

    hostname: str
    timestamp: datetime
    lookback: str
    checks: List[CheckResult] = field(default_factory=list)
    bridge_counters: Optional[dict] = None

    @property
    def overall_status(self) -> CheckStatus:
        if any(c.status == CheckStatus.FAIL for c in self.checks):
            return CheckStatus.FAIL
        if any(c.status == CheckStatus.WARN for c in self.checks):
            return CheckStatus.WARN
        return CheckStatus.PASS


def _journalctl(unit: str, since: str) -> str:
    """Read journalctl output for a unit. Returns empty string on error."""
    try:
        result = subprocess.run(
            ["sudo", "journalctl", "-u", unit, "--since", since, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"journalctl read failed for {unit}: {e}")
        return ""


def check_services() -> List[CheckResult]:
    """Verify all gateway-side services are active. Issue #20 SSOT."""
    results: List[CheckResult] = []
    for unit in ("meshforge-gateway", "meshtasticd", "rnsd", "mosquitto"):
        status = check_service(unit)
        if status.available:
            results.append(
                CheckResult(
                    name=f"service.{unit}",
                    status=CheckStatus.PASS,
                    message=f"{unit} is {status.state.value}",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"service.{unit}",
                    status=CheckStatus.FAIL,
                    message=f"{unit} is {status.state.value}",
                    fix_hint=status.fix_hint or f"sudo systemctl start {unit}",
                )
            )
    return results


def parse_bridge_counters(journal: str) -> Optional[dict]:
    """Find the most recent 'attempted/delivered/dropped' counter line.

    Returns a dict with mr_/rm_ attempted/delivered/dropped or None if no
    counter line is found in the journal text.
    """
    last = None
    for match in _COUNTER_RE.finditer(journal):
        last = match
    if last is None:
        return None
    return {
        "mr_attempted": int(last.group(1)),
        "mr_delivered": int(last.group(2)),
        "mr_dropped": int(last.group(3)),
        "rm_attempted": int(last.group(4)),
        "rm_delivered": int(last.group(5)),
        "rm_dropped": int(last.group(6)),
    }


def check_bridge_counters(
    journal: str,
) -> Tuple[List[CheckResult], Optional[dict]]:
    """Snapshot bridge counters and flag dropped-rate anomalies."""
    counters = parse_bridge_counters(journal)
    if counters is None:
        return (
            [
                CheckResult(
                    name="bridge.counters",
                    status=CheckStatus.SKIP,
                    message="No bridge counter line found in journal",
                )
            ],
            None,
        )

    results: List[CheckResult] = []
    for direction in ("mr", "rm"):
        att = counters[f"{direction}_attempted"]
        delv = counters[f"{direction}_delivered"]
        drop = counters[f"{direction}_dropped"]
        label = "M→R" if direction == "mr" else "R→M"
        if att == 0:
            results.append(
                CheckResult(
                    name=f"bridge.{direction}_counter",
                    status=CheckStatus.SKIP,
                    message=f"{label}: no attempts in window",
                )
            )
            continue
        drop_rate = drop / att
        if drop_rate < 0.10:
            status = CheckStatus.PASS
            hint = None
        elif drop_rate < 0.50:
            status = CheckStatus.WARN
            hint = "Some drops — see bridge.dedup_pattern check"
        else:
            status = CheckStatus.FAIL
            hint = (
                "High drop rate. Likely dedup misclassification per "
                "project_rns_to_mesh_queue_drop.md (LXMF redelivery hits dedup; "
                "cosmetic counter inflation, not real loss)."
            )
        results.append(
            CheckResult(
                name=f"bridge.{direction}_counter",
                status=status,
                message=f"{label}: {att}/{delv}/{drop} ({drop_rate * 100:.0f}% drop)",
                fix_hint=hint,
            )
        )
    return (results, counters)


def check_dedup_pattern(journal: str) -> CheckResult:
    """Detect dedup-misclassification pattern.

    LXMF best-effort delivery retries each unique inbound message ~5×;
    each retry hits ``_is_duplicate`` and logs ``Failed to enqueue
    RNS→Mesh`` at WARNING. This inflates ``rns_to_mesh_dropped``. Ratio
    of failed-to-enqueue / queued > 2 is a clean signal.
    """
    queued = len(re.findall(r"Bridge RNS→Mesh \(queued\)", journal))
    failed = len(re.findall(r"Failed to enqueue RNS→Mesh", journal))
    if queued == 0 and failed == 0:
        return CheckResult(
            name="bridge.dedup_pattern",
            status=CheckStatus.SKIP,
            message="No R→M activity in window",
        )
    if queued == 0:
        return CheckResult(
            name="bridge.dedup_pattern",
            status=CheckStatus.WARN,
            message=f"{failed} 'Failed to enqueue' with no queued — investigate",
        )
    ratio = failed / queued
    if ratio > 2.0:
        return CheckResult(
            name="bridge.dedup_pattern",
            status=CheckStatus.WARN,
            message=(
                f"Dedup misclassification likely: {failed} 'Failed to "
                f"enqueue' for {queued} unique queued ({ratio:.1f}× retry inflation)"
            ),
            fix_hint=(
                "LXMF redelivery retries hit dedup → counter inflated. "
                "Cosmetic. See project_rns_to_mesh_queue_drop.md"
            ),
        )
    return CheckResult(
        name="bridge.dedup_pattern",
        status=CheckStatus.PASS,
        message=f"R→M ratio healthy: {failed} dedup-failed for {queued} queued",
    )


def check_econnrefused(journal: str) -> CheckResult:
    """Detect Errno 111 idle-keep-alive pattern.

    See ``docs/gateway_traffic_flow_analysis_2026_05_08.md`` root cause #2
    and ``project_gateway_rm_counter_inflation.md``. meshtasticd's API
    server suspends after idle and refuses the first toradio_put;
    persistent queue retries succeed ~3.4 s later via ``_post_toradio``.
    """
    count = len(_TORADIO_RAISED_RE.findall(journal))
    if count == 0:
        return CheckResult(
            name="bridge.econnrefused",
            status=CheckStatus.PASS,
            message="No toradio_put failures in window",
        )
    return CheckResult(
        name="bridge.econnrefused",
        status=CheckStatus.WARN,
        message=(
            f"{count} toradio_put raised events — meshtasticd API-suspend "
            f"after idle; persistent queue covers it (~3.4 s recovery)"
        ),
        fix_hint=(
            "meshtasticd-side defect; not addressable in send_text_direct "
            "(reverted in 790b6a5). See docs root cause #2."
        ),
    )


def check_4403_contention() -> CheckResult:
    """Issue #53: a stuck python client on :4403 starves :9443."""
    try:
        result = subprocess.run(
            ["sudo", "ss", "-tnp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return CheckResult(
            name="contention.4403",
            status=CheckStatus.SKIP,
            message=f"ss command unavailable: {e}",
        )
    contended = [
        ln for ln in result.stdout.splitlines() if ":4403" in ln and "python" in ln
    ]
    if contended:
        return CheckResult(
            name="contention.4403",
            status=CheckStatus.FAIL,
            message=f"Issue #53 pattern: {len(contended)} python process holding :4403",
            details="\n".join(contended),
            fix_hint="sudo systemctl restart meshforge-map.service",
        )
    return CheckResult(
        name="contention.4403",
        status=CheckStatus.PASS,
        message="No python contention on :4403",
    )


def _read_rpc_key(path: Path) -> Optional[str]:
    """Inline rpc_key reader (matches paths.ReticulumPaths.get_shared_rpc_key shape)."""
    try:
        text = path.read_text()
    except (OSError, PermissionError):
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != "rpc_key":
            continue
        key = value.strip()
        if len(key) == 64 and all(c in "0123456789abcdefABCDEF" for c in key):
            return key.lower()
        return None
    return None


def check_rpc_key_alignment() -> CheckResult:
    """Issue #41: rpc_key must be set in both rnsd config and client config."""
    rnsd_path = Path("/etc/reticulum/config")
    client_path = Path("/tmp/meshforge_rns_client/config")
    rnsd_key = _read_rpc_key(rnsd_path) if rnsd_path.exists() else None
    client_key = _read_rpc_key(client_path) if client_path.exists() else None
    if rnsd_key is None and client_key is None:
        return CheckResult(
            name="rns.rpc_key",
            status=CheckStatus.WARN,
            message="rpc_key not set in either config",
            fix_hint="Add rpc_key to /etc/reticulum/config; restart rnsd. See Issue #41",
        )
    if rnsd_key is None or client_key is None:
        return CheckResult(
            name="rns.rpc_key",
            status=CheckStatus.WARN,
            message=(
                f"rpc_key in only one config "
                f"(rnsd={'set' if rnsd_key else 'unset'}, "
                f"client={'set' if client_key else 'unset'})"
            ),
            fix_hint="Issue #41: align rpc_key in both /etc/reticulum/config and the client config",
        )
    if rnsd_key != client_key:
        return CheckResult(
            name="rns.rpc_key",
            status=CheckStatus.FAIL,
            message="rpc_key mismatch between rnsd and client config",
            fix_hint=(
                "Issue #41: regenerate the client config to inherit rnsd's "
                "rpc_key, or align both files manually; restart meshforge-gateway"
            ),
        )
    return CheckResult(
        name="rns.rpc_key",
        status=CheckStatus.PASS,
        message="rnsd and client rpc_key aligned (64-hex)",
    )


def check_channel_consistency() -> CheckResult:
    """gateway.json meshtastic.channel index ↔ mqtt_bridge.channel name."""
    cfg_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
    if not cfg_path.exists():
        return CheckResult(
            name="config.channel",
            status=CheckStatus.SKIP,
            message=f"gateway.json not found at {cfg_path}",
        )
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return CheckResult(
            name="config.channel",
            status=CheckStatus.WARN,
            message=f"gateway.json read failed: {e}",
        )
    idx = cfg.get("meshtastic", {}).get("channel")
    name = cfg.get("mqtt_bridge", {}).get("channel")
    if idx is None or not name:
        return CheckResult(
            name="config.channel",
            status=CheckStatus.WARN,
            message="gateway.json missing meshtastic.channel or mqtt_bridge.channel",
        )
    return CheckResult(
        name="config.channel",
        status=CheckStatus.PASS,
        message=f"meshtastic.channel={idx}, mqtt_bridge.channel={name!r}",
        details="Issue #42 channel resolver verifies idx↔name match at gateway startup.",
    )


def render_markdown(audit: AuditResult) -> str:
    """Render the audit as a Markdown memo (same shape as the manual memo)."""
    lines = [
        f"# Gateway Flow Audit — {audit.hostname}",
        "",
        f"**Timestamp**: {audit.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Journal lookback**: {audit.lookback}",
        f"**Overall**: **{audit.overall_status.value}**",
        "",
    ]

    if audit.bridge_counters:
        c = audit.bridge_counters
        lines.extend(
            [
                "## Bridge Counter Snapshot",
                "",
                "| Direction | Attempted | Delivered | Dropped |",
                "|---|---|---|---|",
                f"| M→R | {c['mr_attempted']} | {c['mr_delivered']} | {c['mr_dropped']} |",
                f"| R→M | {c['rm_attempted']} | {c['rm_delivered']} | {c['rm_dropped']} |",
                "",
            ]
        )

    lines.extend(
        ["## Check Results", "", "| Check | Status | Message |", "|---|---|---|"]
    )
    for c in audit.checks:
        lines.append(f"| `{c.name}` | {c.status.value} | {c.message} |")

    failing = [
        c for c in audit.checks if c.status in (CheckStatus.WARN, CheckStatus.FAIL)
    ]
    if failing:
        lines.extend(["", "## Recommendations", ""])
        for c in failing:
            if c.fix_hint:
                lines.append(f"- **`{c.name}`** ({c.status.value}): {c.fix_hint}")

    lines.extend(
        [
            "",
            "---",
            "",
            "*Generated by `python3 -m utils.gateway_flow_audit`. "
            "See `docs/gateway_traffic_flow_analysis_2026_05_08.md` for the "
            "manual investigation that this recipe automates.*",
        ]
    )
    return "\n".join(lines)


def run_audit(lookback: str = "1h") -> AuditResult:
    """Run all checks against the local box's gateway state."""
    journal = _journalctl("meshforge-gateway", lookback)
    audit = AuditResult(
        hostname=socket.gethostname(),
        timestamp=datetime.now(),
        lookback=lookback,
    )
    audit.checks.extend(check_services())
    counter_results, counters = check_bridge_counters(journal)
    audit.checks.extend(counter_results)
    audit.bridge_counters = counters
    audit.checks.append(check_dedup_pattern(journal))
    audit.checks.append(check_econnrefused(journal))
    audit.checks.append(check_4403_contention())
    audit.checks.append(check_rpc_key_alignment())
    audit.checks.append(check_channel_consistency())
    return audit


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback",
        default="1h",
        help="Journal lookback window (e.g. '30 min ago', '4h', '1 day ago'). Default: 1h",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write Markdown memo to file (default: stdout)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    audit = run_audit(lookback=args.lookback)
    memo = render_markdown(audit)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(memo)
        logger.info(f"Wrote audit memo to {args.out}")
    else:
        print(memo)

    if audit.overall_status == CheckStatus.FAIL:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
