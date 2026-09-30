"""meshforge-digest — the manager box's fleet situation digest (observation-only).

A persistent daemon on the MANAGER box that periodically reads fleet GROUND
TRUTH and writes a distilled, "why-it-matters" situation digest to
~/situation_digest.md, and regenerates mini-dudeai's warm-start brief.

  - GROUND TRUTH first: federation /api/status, the cloud map /api/status, the
    local alert/watch logs. Distilled state, NOT raw dumps.
  - WHY IT MATTERS framing on every section + domain awareness (peers whose
    federation backoff is EXPECTED, e.g. a gateway-only box, are named in config).
  - FRESHNESS stamped globally and per-source; an unreachable source is a gap,
    never a crash. A source this box is not configured for reads as inert.
  - OBSERVATION ONLY: reads, never acts. The crons/watchdog own recovery.

Moved into the repo 2026-09-30 from an untracked ~/meshforge_digest.py — it
imported repo mini_dudeai yet lived off-repo, so nothing versioned or
deploy-restarted it (it ran 3 days stale). Operator-specific values are now
config, not code:

  ~/.config/meshforge/digest.json (optional; real user's home)
    {"cloud_url": "http://<cloud-box>:8808/api/status",
     "federation_url": "http://localhost:5000/api/status",
     "expected_backoff": {"<peer-name-substring>": "<why it is expected>"}}
  env: MESHFORGE_DIGEST_INTERVAL (s, default 900), MESHFORGE_DIGEST_CLOUD_URL

Usage (from src/):
  python3 -m monitoring.meshforge_digest --once   # one-shot, write digest, exit
  python3 -m monitoring.meshforge_digest          # daemon loop (systemd)
Unit: templates/systemd/meshforge-digest.service (manager box only — NOT
auto-installed by update.sh).
"""
import os
import sys
import json
import signal
import threading
import datetime
import urllib.request
from urllib.error import URLError

from utils.paths import get_real_user_home

INTERVAL = int(os.environ.get("MESHFORGE_DIGEST_INTERVAL", "900"))
MINI_STALE_S = 300  # 30s tick → >5m means the daemon is likely down/wedged


class _Paths:
    """Resolved lazily (tests point HOME at a temp dir)."""

    @property
    def HOME(self):
        return str(get_real_user_home())

    @property
    def OUT(self):
        return os.path.join(self.HOME, "situation_digest.md")

    # mini-dudeai artifacts (the local 24/7 sub-agent — its history is what
    # lets a warm cloud session start without re-reading the whole fleet).
    @property
    def MINI_STATE(self):
        return os.path.join(self.HOME, "mini_dudeai_state.json")

    @property
    def MINI_HIST(self):
        return os.path.join(self.HOME, "mini_dudeai_history.jsonl")

    @property
    def MINI_ANNOT(self):
        return os.path.join(self.HOME, "mini_dudeai_digest_annotations.md")

    @property
    def MINI_BRIEF(self):
        return os.path.join(self.HOME, "mini_dudeai_brief.md")

    @property
    def CONFIG(self):
        return os.path.join(self.HOME, ".config", "meshforge", "digest.json")


P = _Paths()


def load_config():
    """The digest's operator-specific values. A MISSING config is absent by
    design: the cloud section reads inert and no peer is an expected backoff.
    A config that exists but cannot be used is NOT that — it sets
    ``config_error``, which the cloud and federation sections render as a GAP,
    so a bad hand-edit can never silently switch a check off."""
    cfg, error = {}, None
    try:
        with open(P.CONFIG) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg = data
        else:
            error = f"top level is {type(data).__name__}, expected an object"
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        error = f"{type(e).__name__}: {e}"
    backoff = cfg.get("expected_backoff") or {}
    if not isinstance(backoff, dict):
        error = error or f"expected_backoff is {type(backoff).__name__}, expected an object"
        backoff = {}
    if any(not str(k).strip() for k in backoff):
        # an empty key is a substring of EVERY peer name: it would file a real
        # outage under "expected". Refuse the whole map rather than guess.
        error = error or "expected_backoff has an empty key (it would match every peer)"
        backoff = {}
    return {
        "federation_url": cfg.get("federation_url") or "http://localhost:5000/api/status",
        "cloud_url": os.environ.get("MESHFORGE_DIGEST_CLOUD_URL") or cfg.get("cloud_url") or "",
        "expected_backoff": {str(k): str(v) for k, v in backoff.items()},
        "config_error": error,
    }


def _config_gap(cfg):
    err = cfg.get("config_error")
    return f"{P.CONFIG} unusable ({err}) — fix it; defaults in effect" if err else None


def _write_mini_brief():
    """Regenerate the mini-dudeai warm-start brief (the artifact a warm cloud
    session reads first). Best-effort: the digest still writes if this fails."""
    try:
        from mini_dudeai import write_brief
        write_brief(P.MINI_STATE, P.MINI_HIST, P.MINI_BRIEF)
    except Exception as e:  # observation tool: never die on a bad cycle
        print(f"mini brief skipped: {type(e).__name__}: {e}", flush=True)


def _mini_recent_escalations(hist):
    """Windowed + deduped escalation payloads, via the shared mini_dudeai
    helper so the digest's mini section agrees with the warm-start brief (one
    source of truth). Falls back to the raw extras-only filter if the helper
    cannot be imported."""
    try:
        from mini_dudeai import recent_escalations
        import time
        return recent_escalations(hist, time.time())
    except Exception:
        return [(h.get("outcome") or {}).get("extras", {}).get("escalation")
                for h in hist
                if (h.get("outcome") or {}).get("extras", {}).get("escalation")]


_stop = threading.Event()


def _fetch_json(url, timeout=8):
    """Return (data, None) or (None, error_str). Never raises."""
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace")), None
    except (URLError, OSError, ValueError, TimeoutError) as e:
        return None, f"{type(e).__name__}: {e}"


def _read_log(path, last=0):
    try:
        with open(path) as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
        return lines[-last:] if last else lines, None
    except FileNotFoundError:
        return [], "not found"
    except OSError as e:
        return [], str(e)


def _read_json_file(path):
    """Return (data, None) or (None, error_str). Never raises."""
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "not found"
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def _read_jsonl_tail(path, last=20):
    """Return (list-of-parsed-objects, error). Skips malformed lines. Never raises."""
    lines, err = _read_log(path, last=last)
    if err:
        return [], err
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out, None


def _age(ts_epoch):
    if not ts_epoch:
        return "?"
    secs = max(0, int(datetime.datetime.now().timestamp() - float(ts_epoch)))
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    return f"{secs // 3600}h"


class Section:
    def __init__(self, title, source):
        self.title = title
        self.source = source       # source label for freshness line
        self.fresh = "unknown"     # freshness note
        self.posture = "green"     # green | amber | red
        self.gap = None            # set when the source couldn't be read
        self.lines = []

    def render(self):
        dot = {"green": "🟢", "amber": "🟡", "red": "🔴"}[self.posture]
        head = f"## {dot} {self.title}  ·  _{self.source}_  ·  fresh: {self.fresh}"
        body = "\n".join(f"- {l}" for l in self.lines) if self.lines else "- (nothing notable)"
        if self.gap:
            body = f"- ⚠️ **GAP:** {self.gap}\n" + body
        return head + "\n" + body


def sect_federation(cfg):
    url = cfg["federation_url"]
    s = Section("Federation — is the fleet talking?", url)
    d, err = _fetch_json(url)
    if err:
        s.posture, s.gap, s.fresh = "red", f"federation /api/status unreachable ({err})", "STALE"
        return s
    fed = d.get("federation") or {}
    peers = fed.get("peer_status") or fed.get("peers") or []
    s.fresh = _age(fed.get("last_sync")) + " since last_sync"
    ok = bk = bad = 0
    notable = []
    for p in peers:
        name = p.get("peer_name") or p.get("hostname") or p.get("name") or "?"
        in_bk = p.get("in_backoff")
        mult = p.get("backoff_multiplier")
        reachable = p.get("reachable", p.get("last_ok_ts") is not None)
        last_err = p.get("last_error")
        expected = next((r for k, r in cfg["expected_backoff"].items()
                         if str(k).lower() in str(name).lower()), None)
        if in_bk or (last_err and not reachable):
            if expected:
                bk += 1
                notable.append(f"{name}: backoff (mult={mult}) — **expected** ({expected})")
            else:
                bad += 1
                notable.append(f"{name}: ⚠️ NON-OK (backoff={in_bk} mult={mult}) last_err={last_err!r} — **investigate**")
        else:
            ok += 1
    s.lines.append(f"peers: {ok} ok / {bk} expected-backoff / {bad} unexpected")
    if _config_gap(cfg):
        s.gap = s.gap or _config_gap(cfg) + " — expected backoffs NOT applied"
        s.posture = "amber" if s.posture == "green" else s.posture
    s.lines += notable
    if bad:
        s.posture = "red"
    elif bk and not notable:
        s.posture = "amber"
    # directory size-budget (the GIL-wedge class, Issue #70/#71)
    dirb = d.get("directory") or {}
    if dirb:
        alarm = dirb.get("size_alarm")
        s.lines.append(
            f"directory: total={dirb.get('total')} size_raw={dirb.get('size_bytes_raw')} "
            f"alarm={alarm} — {'⚠️ over budget (wedge risk)' if alarm else 'within budget'}"
        )
        if alarm:
            s.posture = "amber" if s.posture == "green" else s.posture
    wd = d.get("watchdog") or {}
    if wd:
        sig = wd.get("last_signal") or wd.get("signal") or wd.get("state")
        if sig and str(sig).lower() not in ("ok", "nominal", "healthy", "none"):
            s.lines.append(f"watchdog: signal={sig} — **check** (a wedge class may be firing)")
            s.posture = "amber" if s.posture == "green" else s.posture
    s.lines.append("_why: peer health = is the fleet federating; unexpected NON-OK = a real outage to chase; directory alarm = the json+gzip GIL-wedge class._")
    return s


def sect_cloudmap(cfg):
    url = cfg["cloud_url"]
    s = Section("Cloud map — is the public map fresh?", url or "not configured")
    if not url and _config_gap(cfg):
        s.posture, s.gap, s.fresh = "amber", _config_gap(cfg), "unknown"
        return s
    if not url:
        # Absent by design on this box: inert, never a gap or an alarm.
        s.fresh = "n/a"
        s.lines.append("not configured here (set cloud_url in ~/.config/meshforge/digest.json) — inert")
        return s
    d, err = _fetch_json(url)
    if err:
        s.posture, s.gap, s.fresh = "red", f"cloud map /api/status unreachable ({err})", "STALE"
        return s
    stale = d.get("data_stale")
    age = d.get("data_age_seconds")
    s.fresh = f"data_age={age}s"
    s.lines.append(f"status={d.get('status')} data_stale={stale} mqtt_live={d.get('mqtt_live')} nodes={d.get('mqtt_node_count')}")
    if stale:
        s.posture = "red"
        s.lines.append("⚠️ data_stale=True — publish/reboot issue (the 28h-stale-on-reboot class); check the cloud-push timer on the cloud-publisher box.")
    sh = d.get("source_health") or {}
    bad_src = [f"{k}={v}" for k, v in sh.items() if isinstance(v, str) and v.lower() not in ("ok", "healthy", "fresh")]
    if bad_src:
        s.lines.append("source_health attn: " + ", ".join(bad_src[:6]))
        s.posture = "amber" if s.posture == "green" else s.posture
    alerts = d.get("alerts")
    if isinstance(alerts, dict):
        # Summary object: only notable when something actually fired/active.
        active = (alerts.get("active_alerts") or 0) + (alerts.get("total_alerts_fired") or 0)
        if active:
            s.lines.append(f"alerts: {active} active/fired — {json.dumps(alerts.get('by_severity', {}))}")
            s.posture = "amber" if s.posture == "green" else s.posture
    elif alerts:  # non-empty list of actual alerts
        s.lines.append(f"alerts: {json.dumps(alerts)[:200]}")
        s.posture = "amber" if s.posture == "green" else s.posture
    s.lines.append("_why: stale cloud map = the operator-facing map is lying; source_health/alerts surface publish + ingest faults._")
    return s


def sect_monitors():
    s = Section("Monitors & alerts — did anything fire?", "~/[fleet|soak]_alerts.log, synth_stress.log")
    s.fresh = "tail at digest time"
    # fleet_alerts: HIGH-severity offline/role alerts
    fa, _ = _read_log(f"{P.HOME}/fleet_alerts.log")
    if fa:
        s.posture = "red"
        s.lines.append(f"🔴 fleet_alerts: {len(fa)} line(s) — last: {fa[-1][:120]}")
    else:
        s.lines.append("fleet_alerts: quiet ✓")
    # soak_alerts: dup/airtime/telemetry
    sa, _ = _read_log(f"{P.HOME}/soak_alerts.log")
    if sa:
        recent = [l for l in sa if l[:10] == datetime.date.today().isoformat()]
        s.lines.append(f"soak_alerts: {len(sa)} total, {len(recent)} today — last: {sa[-1][:110]}")
        if recent:
            s.posture = "amber" if s.posture == "green" else s.posture
    else:
        s.lines.append("soak_alerts: quiet ✓")
    # synth_stress: RF-independent regression flags (the push-worthy ones)
    syn, _ = _read_log(f"{P.HOME}/synth_stress.log")
    if syn:
        leaks = [l for l in syn if "DEDUP=FAIL-leak" in l or "ATTR=FAIL-miss" in l]
        last = syn[-1]
        rid = (last.split("| ")[1].split(" ")[0] if "| " in last else "?")
        if leaks:
            s.posture = "red"
            s.lines.append(f"🔴 synth_stress: {len(leaks)} RF-INDEPENDENT regression(s) — dedup/attribution broke!")
        else:
            s.lines.append(f"synth_stress: last cycle {rid}, no RF-independent regressions ✓ ({len(syn)} cycles logged)")
    else:
        s.lines.append("synth_stress: no cycles logged")
    s.lines.append("_why: fleet_alerts = a box/service is down (HIGH); synth dedup/attr leak = a shipped gateway fix regressed (the only RF-independent failures)._")
    return s


def sect_mini_dudeai():
    """The local 24/7 sub-agent: alive? what fired? what should the cloud chase?

    Reads mini-dudeai's own state + history + annotations (all operator-home,
    observation-only). This is the section that makes "a piece of you is already
    here" real — a warm cloud session reads it to see what happened while away.
    """
    s = Section("mini-dudeai — the local watcher", "~/mini_dudeai_{state,history,annotations}")
    state, err = _read_json_file(P.MINI_STATE)
    if err or not isinstance(state, dict):
        s.posture, s.gap, s.fresh = "amber", f"mini_dudeai_state.json unreadable ({err}) — daemon may not be installed/running here", "STALE"
        return s
    last_tick = state.get("last_tick_ts")
    age_s = None
    if last_tick:
        age_s = max(0, int(datetime.datetime.now().timestamp() - float(last_tick)))
    s.fresh = _age(last_tick) + " since last tick"
    err_count = state.get("error_count") or 0
    rules = state.get("rules") or {}
    active = [rs for rs in rules.values() if rs.get("currently_active")]

    if age_s is not None and age_s > MINI_STALE_S:
        s.posture = "red"
        s.lines.append(f"🔴 STALE: last tick {s.fresh} (>{MINI_STALE_S}s) — meshforge-mini-dudeai.service likely down/wedged")
    else:
        s.lines.append(f"alive: {state.get('rule_count', len(rules))} rules, {len(active)} active, src_errors={err_count} (host={state.get('host', '?')})")
    if err_count:
        s.posture = "amber" if s.posture == "green" else s.posture
        s.lines.append(f"⚠️ source errors this tick: {err_count} — mini is blind on a source (check source_error rules)")
    for rs in active:
        s.lines.append(f"active: **{rs.get('rule_id')}** · {rs.get('subject')} · {rs.get('last_detail', '')[:100]}")

    # Recent history: surface escalations (look-here-first) + today's edge_up fires.
    hist, _ = _read_jsonl_tail(P.MINI_HIST, last=40)
    today = datetime.date.today().isoformat()
    escalations = _mini_recent_escalations(hist)
    fires_today = [h for h in hist if h.get("transition") == "edge_up" and str(h.get("iso", "")).startswith(today)]
    if escalations:
        s.posture = "amber" if s.posture == "green" else s.posture
        s.lines.append(f"🔎 {len(escalations)} escalation(s) proposed — LOOK HERE FIRST:")
        for esc in escalations[-5:]:
            s.lines.append(f"   → {esc.get('rule')} · {esc.get('subject')} · {esc.get('detail', '')[:90]}")
    if fires_today:
        s.lines.append(f"fires today: {len(fires_today)} edge_up — last: {fires_today[-1].get('rule_id')} · {fires_today[-1].get('subject')}")
    elif not active and s.posture == "green":
        s.lines.append("quiet: no active conditions, no fires today ✓")

    annot, _ = _read_log(P.MINI_ANNOT, last=3)
    if annot:
        s.lines.append(f"known-normal annotations (latest of {len(annot)}): {annot[-1][:120]}")

    s.lines.append("_why: mini is your always-on local presence; escalations = what the next cloud session should chase; stale = the watcher itself went dark._")
    return s


def sect_freshness(sections):
    s = Section("Freshness & gaps — what this digest could NOT see", "self")
    s.fresh = "now"
    gaps = [sec for sec in sections if sec.gap]
    if gaps:
        for sec in gaps:
            s.lines.append(f"⚠️ {sec.title}: {sec.gap}")
        s.posture = "amber"
    else:
        s.lines.append("all sources read cleanly ✓")
    s.lines.append("deep per-box sources (journals, messages.db, MeshCore ring buffer) — NOT yet wired (Phase 0 next iteration)")
    s.lines.append("_why: a digest that hides its own blind spots is worse than none — Phase 1 hardens this into per-source self-stale detection._")
    return s


def build_digest(cfg=None):
    cfg = cfg or load_config()
    sections = [sect_federation(cfg), sect_cloudmap(cfg), sect_monitors(), sect_mini_dudeai()]
    sections.append(sect_freshness(sections))
    worst = "green"
    for sec in sections:
        if sec.posture == "red":
            worst = "red"
            break
        if sec.posture == "amber":
            worst = "amber"
    map_claim = "map fresh" if cfg["cloud_url"] else "cloud map not watched here"
    tldr = {
        "green": f"🟢 NOMINAL — fleet federating, {map_claim}, monitors quiet.",
        "amber": "🟡 ATTENTION — non-fatal items below want an eyeball.",
        "red": "🔴 ACTION — a real fault is firing; see 🔴 sections.",
    }[worst]
    now = datetime.datetime.now()
    out = [
        "# MeshForge Fleet — Situation Digest",
        f"_generated {now.strftime('%Y-%m-%d %H:%M:%S %Z')} · refresh ~{INTERVAL // 60}m · observation-only (flags, never fixes)_",
        "",
        f"## TL;DR  {tldr}",
        "",
    ]
    out += [sec.render() + "\n" for sec in sections]
    out.append("---\n_meshforge-digest Phase 0 · reads ground truth, distills, stamps freshness · the crons/watchdog own recovery._")
    return "\n".join(out)


def write_digest():
    text = build_digest()
    out = P.OUT
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, out)  # atomic
    return text


def _handle_sig(signum, frame):
    _stop.set()


def main():
    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
    once = "--once" in sys.argv
    if once:
        write_digest()
        _write_mini_brief()
        print(f"wrote {P.OUT}")
        return
    print(f"meshforge-digest started · interval={INTERVAL}s · out={P.OUT}", flush=True)
    while not _stop.is_set():
        try:
            write_digest()
            _write_mini_brief()
            print(f"digest refreshed {datetime.datetime.now().strftime('%H:%M:%S')}", flush=True)
        except Exception as e:  # observation tool must never die on a bad cycle
            print(f"digest cycle error (continuing): {type(e).__name__}: {e}", flush=True)
        _stop.wait(INTERVAL)
    print("meshforge-digest stopped", flush=True)


if __name__ == "__main__":
    main()
