# hs_skew_attr.awk — running-code skew ATTRIBUTION for scripts/honest_status.sh.
#
# Input : `systemctl show -p Id -p ExecStart -p Environment -p WorkingDirectory
#          -p ActiveEnterTimestamp --timestamp=unix <units...>` (blank-line
#          separated records, one per unit).
# Output: "B <repo> <unit> <days>"  behind on CODE
#         "P <repo> <unit> <days>"  behind on prose only (docs/.claude/evals)
#         "U <repo> <unit>"         unknown (no start time / no repo HEAD)
#         "N <unit> <interp>"       DISCLOSURE: the unit runs a venv we could
#                                   NOT inspect, so an editable install hiding
#                                   there would be invisible. A venv we CAN
#                                   read is answered, never disclosed.
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
# Return the venv-ish python interpreter in an ExecStart, or "" if the unit
# runs a system interpreter / no python at all. `systemctl show` emits tokens
# as path=/x and argv[]=/x, so strip any key= prefix before matching.
function venv_interp(s,   n, parts, i, p) {
  n = split(s, parts, /[ \t]+/)
  for (i = 1; i <= n; i++) {
    p = parts[i]
    sub(/^[^=]*=/, "", p)
    if (p !~ /\/bin\/python[0-9.]*$/) continue
    if (p ~ /^\/usr\/bin\//)       continue
    if (p ~ /^\/usr\/local\/bin\//) continue
    if (p ~ /^\/bin\//)            continue
    return p
  }
  return ""
}


# Resolve a repo `pip install -e`'d into the unit's venv. An editable install
# lives as a .pth / __editable__* entry in the venv's site-packages, which is
# why NOTHING about it appears in ExecStart/Environment/WorkingDirectory and a
# `-m module` ExecStart has no .py to scan.
#
# Returns the /opt repo path, or "" — and the "" is TWO different claims, which
# is the whole point of this function: INSPECTED=1 means we read site-packages
# and there is genuinely no repo there (silent, correctly untracked);
# INSPECTED=0 means we could not look at all (disclose as N). Before 2026-09-02
# the leg could not look at any of them and disclosed EVERY venv unit, which on
# this fleet meant naming the same four nomadnet units on every run forever —
# a line that never changes is furniture, and furniture is how a real finding
# gets scrolled past.
function editable_repo(s,   iv, venv, cmd, line, sp, found) {
  INSPECTED = 1
  iv = venv_interp(s)
  if (iv == "") return ""                       # no venv: nothing to inspect
  # This path is interpolated into a shell command. Anything outside a
  # conservative charset is refused rather than quoted-and-hoped.
  if (iv !~ /^[A-Za-z0-9_.\/@+-]+$/) { INSPECTED = 0; return "" }
  venv = iv; sub(/\/bin\/python[0-9.]*$/, "", venv)
  sp = ""
  cmd = "ls -d '" venv "'/lib/python*/site-packages 2>/dev/null | head -1"
  if ((cmd | getline line) > 0) sp = line
  close(cmd)
  if (sp == "") { INSPECTED = 0; return "" }    # could not look
  found = ""
  cmd = "grep -rhoE '/opt/[A-Za-z0-9_.-]+' '" sp "'/*.pth '" sp "'/__editable__* 2>/dev/null"
  # Same prefix precedence as the direct resolver — /opt/meshforge is a prefix
  # of /opt/meshforge-maps, so maps must win first (honest_failure_modes #5:
  # two consumers of one rule share ONE ordering).
  while ((cmd | getline line) > 0) {
    if (index(line, "/opt/meshforge-maps")) { found = "/opt/meshforge-maps"; break }
    if (index(line, "/opt/meshanchor"))     { found = "/opt/meshanchor";     break }
    if (index(line, "/opt/meshforge"))      { found = "/opt/meshforge";      break }
  }
  close(cmd)
  return found
}


# Resolve, per `systemctl show` record, the repo whose code the unit's process
# actually LOADS — never the unit's NAME.
function decide(   hay, rp, i, n, parts, s, line, H, HC, iv) {
  if (id == "") return
  INSPECTED = 1
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
  # Last resort: an editable install inside the unit's own venv.
  if (rp == "") rp = editable_repo(ex)
  if      (rp == "/opt/meshforge-maps") { H = HTMM; HC = HCMM }
  else if (rp == "/opt/meshanchor")     { H = HTMA; HC = HCMA }
  else if (rp == "/opt/meshforge")      { H = HTMF; HC = HCMF }
  else {
    # BLIND-SPOT WITNESS (2026-09-02, queued by the audit that produced this
    # file). No repo resolved. For almost every unit that is CORRECT and stays
    # silent — a packaged binary, a system service, an external app. But a repo
    # `pip install -e`'d into a venv OUTSIDE /opt loads repo code through a
    # .pth in site-packages: nothing lands in ExecStart/Environment/
    # WorkingDirectory, and a `-m module` ExecStart has no .py file to scan.
    # Such a unit is dropped here — output byte-identical to "correctly not
    # repo code". That is how a detector loses coverage without anyone seeing
    # it, and it is the same shape as the name-dispatch bug this file replaced:
    # untracked-by-design and blind must not be the same answer.
    #
    # Nothing on this fleet has that shape today, so this is expected to emit
    # NOTHING — it exists so the class announces itself the first time it
    # appears (honest_failure_modes #9: every swallow leaves a witness). It is
    # a disclosure, never a fault: we cannot tell from `systemctl show` alone
    # whether such a unit loads repo code, and "cannot tell" is the claim.
    if (INSPECTED == 0 && (iv = venv_interp(ex)) != "") print "N " id " " iv
    return
  }
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
