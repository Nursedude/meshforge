# hs_skew_attr.awk — running-code skew ATTRIBUTION for scripts/honest_status.sh.
#
# Input : `systemctl show -p Id -p ExecStart -p Environment -p WorkingDirectory
#          -p ActiveEnterTimestamp --timestamp=unix <units...>` (blank-line
#          separated records, one per unit).
# Output: "B <repo> <unit> <days>"  behind on CODE
#         "P <repo> <unit> <days>"  behind on prose only (docs/.claude/evals)
#         "U <repo> <unit>"         unknown (no start time / no repo HEAD)
#         nothing                   the unit loads no repo code — untracked
# Vars  : HTMF/HTMA/HTMM (repo HEADs), HCMF/HCMA/HCMM (repo CODE-heads).
#
# WHY THIS FILE EXISTS (2026-09-02 audit). The leg used to decide BOTH
# membership and repo by the unit's NAME (`^(meshforge|meshanchor)-`, then a
# case on the same prefix). A name says who MANAGES a unit; staleness is a
# property of the code the process LOADS. Those correlate by convention, and
# the convention broke in both directions across 50 audited units:
#   * false positive — meshforge-lxmd runs the packaged /usr/local/bin/lxmd
#     (the pinned LXMF fork), loads no repo code, yet was reported behind with
#     "they load it at next restart". Restarting it loaded nothing.
#   * false negatives — nomadnet-silence-watch.service (13d, MeshForge code,
#     name lacks the prefix) and meshanchor.service (17d, the orchestrator,
#     excluded only because the pattern required a HYPHEN after "meshanchor").
#     The latter is the worse one: its hyphenated sibling meshanchor-daemon IS
#     tracked, so the line looked like it covered the orchestrator all along.
# The name-prefix dispatch had already been repaired twice for this same class
# (meshanchor-* 2026-08-09, meshforge-maps 2026-08-11) without the mechanism
# itself being questioned. This is the mechanism fix.
#
# Attribution is deliberately a SEPARATE FILE, not more shell inside the remote
# heredoc: it is the part with real logic, so it must be directly unit-testable
# against synthetic records (tests/test_honest_status_skew_attr.sh) rather than
# only grep-able as source text.
# Resolve, per `systemctl show` record, the repo whose code the unit's process
# actually LOADS — never the unit's NAME.
function decide(   hay, rp, i, n, parts, s, line, H, HC) {
  if (id == "") return
  hay = ex " " env " " wd
  # maps FIRST: /opt/meshforge is a prefix of /opt/meshforge-maps.
  rp = ""
  if (index(hay, "/opt/meshforge-maps")) rp = "/opt/meshforge-maps"
  else if (index(hay, "/opt/meshanchor")) rp = "/opt/meshanchor"
  else if (index(hay, "/opt/meshforge")) rp = "/opt/meshforge"
  if (rp == "") {                    # indirect: a .py target OUTSIDE any repo
    n = split(ex, parts, /[ \t]+/); s = ""
    for (i = 1; i <= n; i++) if (parts[i] ~ /\.py$/) { s = parts[i]; break }
    if (s != "" && s ~ /^\//) {
      while ((getline line < s) > 0) {
        # A MENTION of the repo is not a LOAD of it: require a real path
        # INJECTION. 2026-09-02 live drill — nomadnet_wrapper.py names
        # /opt/meshforge twice inside user-facing HELP TEXT while running
        # nomadnet from a pipx venv; the loose scan called it repo-loading and
        # invented a 5d-stale unit. Keying on a proxy instead of the thing is
        # the exact class this fix exists to end.
        if (line !~ /sys\.path|PYTHONPATH/) continue
        if (index(line, "/opt/meshforge-maps")) { rp = "/opt/meshforge-maps"; break }
        if (index(line, "/opt/meshanchor"))     { rp = "/opt/meshanchor"; break }
        if (index(line, "/opt/meshforge"))      { rp = "/opt/meshforge"; break }
      }
      close(s)
    }
  }
  if      (rp == "/opt/meshforge-maps") { H = HTMM; HC = HCMM }
  else if (rp == "/opt/meshanchor")     { H = HTMA; HC = HCMA }
  else if (rp == "/opt/meshforge")      { H = HTMF; HC = HCMF }
  else return        # loads no repo code: not judgeable, correctly untracked
  # FAIL-SAFE: an unresolvable code-head collapses to HEAD, never to "no code
  # changes" (honest_failure_modes #1 — the degraded value must not overlap
  # the healthy domain).
  if (HC == "" || HC ~ /[^0-9]/) HC = H
  if (ts == "" || ts ~ /[^0-9]/)   { print "U " rp " " id; return }
  if (H  == "SKIP" || H ~ /[^0-9]/) { print "U " rp " " id; return }
  if      (ts + 0 < HC + 0) printf "B %s %s %d\n", rp, id, (HC - ts) / 86400
  else if (ts + 0 < H  + 0) printf "P %s %s %d\n", rp, id, (H  - ts) / 86400
}
/^Id=/                   { id  = substr($0, 4) }
/^ExecStart=/            { ex  = ex " " substr($0, 11) }
/^Environment=/          { env = env " " substr($0, 13) }
/^WorkingDirectory=/     { wd  = wd " " substr($0, 18) }
/^ActiveEnterTimestamp=/ { ts  = substr($0, 22); sub(/^@/, "", ts) }
/^$/                     { decide(); id=""; ex=""; env=""; wd=""; ts="" }
END                      { decide() }
