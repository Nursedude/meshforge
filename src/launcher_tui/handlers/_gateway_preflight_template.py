"""Template-driven drift checks + export for Gateway Pre-Flight handler.

Split from gateway_preflight.py for size and separation of concerns. This
module holds template loading, per-field drift comparison, and current-state
export. The TUI handler imports TEMPLATE_DIR, load_default_template,
check_template_drift, and export_current_as_template.

Templates live in /opt/meshforge/src/gateway/templates/preflight/*.json.
Schema is documented in that directory's README.md.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from utils.paths import get_real_user_home
from utils.safe_import import safe_import
from utils.service_check import check_service, get_rns_shared_instance_info

logger = logging.getLogger(__name__)

# Built-in templates ship with the repo.
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "gateway" / "templates" / "preflight"

# ANSI colors matching the main handler.
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_OK = f"{_GREEN}✓{_RESET}"
_FAIL = f"{_RED}✗{_RESET}"
_WARN = f"{_YELLOW}⚠{_RESET}"


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def load_default_template() -> Optional[Dict[str, Any]]:
    """Load the first built-in template, or None if none exist or directory missing."""
    if not TEMPLATE_DIR.is_dir():
        return None
    candidates = sorted(TEMPLATE_DIR.glob("*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load template %s: %s", candidates[0], e)
        return None


def list_templates() -> List[Path]:
    """Return sorted list of available built-in template paths."""
    if not TEMPLATE_DIR.is_dir():
        return []
    return sorted(TEMPLATE_DIR.glob("*.json"))


def load_templates() -> List[Dict[str, Any]]:
    """Load every parseable built-in template (unparseable ones logged, skipped)."""
    out: List[Dict[str, Any]] = []
    for path in list_templates():
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load template %s: %s", path, e)
    return out


def check_template_drift_best(
    live: Dict[str, Any],
    templates: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Tuple[str, str, Optional[str]]], Optional[str], int]:
    """Judge live state against EVERY template; report the best match.

    The fleet deliberately runs more than one radio profile (e.g. a
    LONG_FAST/slot20 leg and a SHORT_TURBO/slot8 leg joined by moc's
    cross-preset bridge — operator decision, 2026-07-09), so a single
    default template structurally false-fails whichever leg it wasn't
    exported from. Each box is judged against the template it matches
    best (fewest fails, then fewest warns); a box matching NO template
    still reports the closest one's failures loudly.

    Returns (results, chosen_template_name, template_count);
    ([], None, 0) when no templates exist.
    """
    if templates is None:
        templates = load_templates()
    if not templates:
        return [], None, 0
    best = None
    for template in templates:
        results = check_template_drift(template, live)
        fails = sum(1 for s, _, _ in results if s == _FAIL)
        warns = sum(1 for s, _, _ in results if s == _WARN)
        name = str(template.get("name", "unnamed"))
        if best is None or (fails, warns) < (best[0], best[1]):
            best = (fails, warns, results, name)
    return best[2], best[3], len(templates)


# ---------------------------------------------------------------------------
# Live state capture (shared between drift-check and export)
# ---------------------------------------------------------------------------


def capture_live_state(info_text: Optional[str] = None) -> Dict[str, Any]:
    """Build a dict of the live system state for drift-check or export.

    info_text: pre-captured output of `meshtastic --host 127.0.0.1 --info`.
    If None, the caller can still export without the Meshtastic section.
    """
    state: Dict[str, Any] = {
        "captured_at": datetime.now().isoformat(),
        "meshtastic": {},
        "gateway": {},
        "packages": {},
        "services": {},
        "rns_shared_instance": {},
        "nomadnet": {},
    }

    if info_text:
        # Region, preset, channel_num come from the `lora` block in --info.
        region = _extract_quoted(info_text, r'"region":\s*"([^"]+)"')
        if region:
            state["meshtastic"]["region"] = region
        preset = _extract_quoted(info_text, r'"modemPreset":\s*"([^"]+)"')
        if preset:
            state["meshtastic"]["modem_preset"] = preset
        cnum = _extract_number(info_text, r'"channelNum":\s*(\d+)')
        if cnum is not None:
            state["meshtastic"]["channel_num"] = cnum
        # First channel with both uplink+downlink enabled is the bridge channel.
        state["meshtastic"]["bridge_channels"] = _extract_uplinked_channels(info_text)

    # Gateway config
    cfg_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        state["gateway"]["bridge_mode"] = cfg.get("bridge_mode")
        state["gateway"]["mqtt_channel"] = (
            cfg.get("mqtt_bridge", {}).get("channel")
            or cfg.get("meshtastic", {}).get("mqtt_channel")
        )
        state["gateway"]["mqtt_region"] = cfg.get("mqtt_bridge", {}).get("region")
        default_dest = cfg.get("rns", {}).get("default_lxmf_destination")
        state["gateway"]["default_lxmf_destination"] = default_dest
        state["gateway"]["default_lxmf_destination_set"] = bool(default_dest)
    except (OSError, json.JSONDecodeError):
        pass

    # Package versions (captured even if not matched to template)
    for pkg in ("RNS", "LXMF"):
        mod, present = safe_import(pkg)
        if present:
            state["packages"][pkg.lower()] = {
                "installed": True,
                "version": getattr(mod, "__version__", "unknown"),
            }
        else:
            state["packages"][pkg.lower()] = {"installed": False}

    # Services
    for svc in ("meshtasticd", "rnsd", "mosquitto"):
        status = check_service(svc)
        state["services"][svc] = "active" if status.available else "inactive"

    # RNS shared instance
    info = get_rns_shared_instance_info()
    state["rns_shared_instance"]["available"] = bool(info.get("available"))
    state["rns_shared_instance"]["detail"] = info.get("detail")

    # NomadNet identity (if logfile present)
    logfile = get_real_user_home() / ".nomadnetwork" / "logfile"
    if logfile.exists():
        try:
            matches = re.findall(
                r"LXMF Router ready to receive on: <([0-9a-f]+)>",
                logfile.read_text(errors="ignore"),
            )
            if matches:
                state["nomadnet"]["lxmf_identity"] = matches[-1]
        except OSError:
            pass

    return state


def _extract_quoted(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _extract_number(text: str, pattern: str) -> Optional[int]:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _extract_uplinked_channels(info_text: str) -> List[Dict[str, Any]]:
    """Return list of channels with both uplinkEnabled + downlinkEnabled = true."""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(
        r'Index (\d+):.*?"name":\s*"([^"]*)".*?"uplinkEnabled":\s*(true|false).*?"downlinkEnabled":\s*(true|false)',
        info_text,
    ):
        idx, name, up, down = m.groups()
        if up == "true" and down == "true":
            out.append({"index": int(idx), "name": name})
    return out


# ---------------------------------------------------------------------------
# Drift comparison
# ---------------------------------------------------------------------------


def check_template_drift(
    template: Dict[str, Any],
    live: Dict[str, Any],
) -> List[Tuple[str, str, Optional[str]]]:
    """Compare live state against template expectations.

    Returns a list of (status_glyph, message, optional_fix) tuples ready for
    display by the TUI handler's existing result loop.
    """
    results: List[Tuple[str, str, Optional[str]]] = []

    for category, fields in template.items():
        if category.startswith("$") or category in {
            "name", "description", "maintainer", "validated_date",
            "validated_on", "region_compliance",
        }:
            continue
        if not isinstance(fields, dict):
            continue
        for field_name, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            results.append(_check_one(category, field_name, spec, live))
    return results


def _check_one(
    category: str,
    field: str,
    spec: Dict[str, Any],
    live: Dict[str, Any],
) -> Tuple[str, str, Optional[str]]:
    severity = spec.get("severity", "fail")
    fail_glyph = _FAIL if severity == "fail" else _WARN
    expected = spec.get("expected")
    min_version = spec.get("min_version")
    install_hint = spec.get("install")

    actual = _resolve_live_value(category, field, live)
    label = f"{category}.{field}"

    # UNOBSERVABLE is never drift (honest_failure_modes #1/#2): a failed
    # radio query or an absent gateway.json leaves live values None/missing,
    # and comparing None against an expectation manufactured FAILs on every
    # non-gateway box (2026-07-09 fleet sweep). Categories below with their
    # own branches (packages/services/rns_shared_instance/nomadnet) always
    # OBSERVE their values, so they keep their loud failure semantics.
    if category == "meshtastic" and field in {
        "bridge_channel_name",
        "bridge_channel_uplink_enabled",
        "bridge_channel_downlink_enabled",
    } and "bridge_channels" not in (live.get("meshtastic") or {}):
        # --info never ran: channel data unobservable (an OBSERVED empty
        # list — info ran, nothing uplinked — still fails below).
        return (_WARN, f"{label}: unobservable — cannot verify "
                       f"(meshtastic --info unavailable)", None)
    if category not in {"packages", "services", "rns_shared_instance",
                        "nomadnet"} and field not in {
        "bridge_channel_name", "bridge_channel_uplink_enabled",
        "bridge_channel_downlink_enabled",
    } and field not in (live.get(category) or {}):
        # KEY-presence, not value-None: capture_live_state omits keys it could
        # not observe (no --info, no gateway.json) but stores an explicit None
        # when the source was READ and the field was missing — that is
        # OBSERVED drift and must keep failing below (fix re-review,
        # 2026-07-09: `actual is None` conflated the two and silently
        # downgraded a misconfigured gateway.json to 'unobservable').
        return (_WARN, f"{label}: unobservable — cannot verify "
                       f"(expected {expected!r})", None)

    # Package version checks use min_version semantics
    if category == "packages":
        pkg_state = live.get("packages", {}).get(field, {})
        if not pkg_state.get("installed"):
            return (fail_glyph, f"{label}: not installed", install_hint)
        version = pkg_state.get("version")
        if min_version and version:
            if _version_ge(version, min_version):
                return (_OK, f"{label}: {version} (>= {min_version})", None)
            return (
                fail_glyph,
                f"{label}: {version} — template requires >= {min_version}",
                install_hint,
            )
        return (_OK, f"{label}: {version or 'installed'}", None)

    # Service checks compare active/inactive strings
    if category == "services":
        return (
            _OK if actual == expected else fail_glyph,
            f"{label}: {actual or '(unknown)'}"
            + ("" if actual == expected else f" — expected {expected}"),
            None if actual == expected else f"sudo systemctl start {field}",
        )

    # Meshtastic bridge channel-name check picks the first uplinked channel
    if category == "meshtastic" and field == "bridge_channel_name":
        chans = live.get("meshtastic", {}).get("bridge_channels") or []
        if not chans:
            return (fail_glyph, f"{label}: no uplinked channels found", None)
        names = [c.get("name") for c in chans if c.get("name")]
        if expected in names:
            return (_OK, f"{label}: '{expected}' present in uplinked set {names}", None)
        return (
            fail_glyph,
            f"{label}: expected '{expected}' but uplinked channels are {names}",
            None,
        )

    if category == "meshtastic" and field in {
        "bridge_channel_uplink_enabled", "bridge_channel_downlink_enabled",
    }:
        chans = live.get("meshtastic", {}).get("bridge_channels") or []
        # If we got ANY uplinked+downlinked channel, both flags are effectively True.
        actual_val = len(chans) > 0
        return (
            _OK if actual_val == expected else fail_glyph,
            f"{label}: {actual_val}",
            None if actual_val == expected
            else "meshtastic --ch-index N --ch-set uplink_enabled true --ch-set downlink_enabled true",
        )

    if category == "rns_shared_instance" and field == "reachable":
        actual_val = bool(live.get("rns_shared_instance", {}).get("available"))
        return (
            _OK if actual_val == expected else fail_glyph,
            f"{label}: {actual_val}"
            + (f" ({live['rns_shared_instance'].get('detail')})" if actual_val else ""),
            None if actual_val == expected else "sudo systemctl restart rnsd",
        )

    if category == "nomadnet" and field == "identity_matches_default_lxmf_destination":
        nomad_hash = live.get("nomadnet", {}).get("lxmf_identity")
        default_dest = live.get("gateway", {}).get("default_lxmf_destination")
        if not nomad_hash:
            return (_WARN, f"{label}: NomadNet identity unknown (logfile missing)", None)
        if not default_dest:
            return (
                fail_glyph if severity == "fail" else _WARN,
                f"{label}: gateway.json has no default_lxmf_destination",
                None,
            )
        # Accept either a single hash (legacy) or a list (multi-recipient). The
        # local NomadNet just needs to be in the recipient set for Mesh→RNS to
        # land in its inbox.
        if isinstance(default_dest, str):
            dest_list = [default_dest] if default_dest else []
        elif isinstance(default_dest, list):
            dest_list = [d for d in default_dest if isinstance(d, str) and d]
        else:
            dest_list = []
        matches = nomad_hash in dest_list
        if matches == expected:
            if len(dest_list) > 1:
                detail = f"nomadnet={nomad_hash[:12]}… is one of {len(dest_list)} recipients"
            else:
                detail = f"nomadnet={nomad_hash[:12]}…, gateway={dest_list[0][:12] if dest_list else '?'}…"
            return (_OK, f"{label}: match ({detail})", None)
        return (
            fail_glyph,
            f"{label}: drift (nomadnet={nomad_hash[:12]}… not in gateway recipient list)",
            "add this NomadNet's hash to rns.default_lxmf_destination in ~/.config/meshforge/gateway.json",
        )

    # Simple scalar comparison for everything else
    if actual == expected:
        return (_OK, f"{label}: {actual}", None)
    return (
        fail_glyph,
        f"{label}: {actual!r} — expected {expected!r}",
        None,
    )


def _resolve_live_value(category: str, field: str, live: Dict[str, Any]) -> Any:
    cat = live.get(category) or {}
    return cat.get(field)


def _version_ge(actual: str, required: str) -> bool:
    """Return True if actual >= required using naive numeric tuple compare."""
    def tup(v: str):
        return tuple(int(x) for x in re.findall(r"\d+", v))
    try:
        return tup(actual) >= tup(required)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_current_as_template(
    live: Dict[str, Any],
    target_dir: Optional[Path] = None,
) -> Path:
    """Write the captured live state as an exportable JSON template.

    Returns the path written. Does not overwrite existing files (timestamp
    suffix makes each export unique).
    """
    if target_dir is None:
        target_dir = get_real_user_home() / ".config" / "meshforge" / "templates"
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"exported_{ts}.json"
    target.write_text(json.dumps(live, indent=2) + "\n")
    return target


def run_meshtastic_info(timeout: float = 20.0) -> Optional[str]:
    """Run `meshtastic --host 127.0.0.1 --info` and return stdout, or None."""
    try:
        result = subprocess.run(
            ["meshtastic", "--host", "127.0.0.1", "--info"],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("meshtastic --info failed: %s", e)
        return None
