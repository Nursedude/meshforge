# NOC read grant + `box_truth/v1` — frontier design-level security pass

> **Date:** 2026-10-14 ~01:10–02:00 HST · **Model:** Fable 5.1 (non-author:
> the spec was grilled and written by an Opus 5.5 session on 10-13; this
> session wrote no spec text before this pass) · **Gate:** 10 (security),
> queued in `review_provenance.md` "QUEUED 2026-10-13 … NOC read grant".
> **Subject:** `.claude/plans/noc_read_grant_2026_10_13.md` (design only,
> nothing built). **Verdict:** the trust path is sound as a SHAPE —
> `restrict,command=` behaves as the spec assumes (measured below) — but the
> spec is NOT build-ready: **8 blocking amendments (B1–B8)** must land in the
> spec before session 2 writes code, plus 12 should-fix gaps (S1–S12). All
> are design changes; none says "do not build".

## How this pass measured (not asserted)

| Leg | Method | Result |
|---|---|---|
| sshd option semantics | **Live loopback drill on VolcanoAI's own sshd**: a scratch ed25519 key appended to the operator's `authorized_keys` as `restrict,from="127.0.0.1,::1",command="<probe.sh>"`, ten client shapes from localhost, file restored from a pre-drill copy. Script: session scratchpad `restrict_drill.sh` (tmpfs, gone on reboot; the transcript is summarised in "Drill results"). | `authorized_keys` sha256 before == after (`06d6dd28…`), `0` drill lines remain. VERIFIED. |
| effective sshd config | `sudo sshd -T` on VolcanoAI (OpenSSH 10.0p2) | `restrict`/`command=`/`from=` honoured; `permituserenvironment no`; `acceptenv LANG LC_* COLORTERM NO_COLOR`; `authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2`; `strictmodes yes`; `exposeauthinfo no`; `passwordauthentication yes` |
| fleet `authorized_keys` shape | read-only ssh fan-out, 9 boxes (awk over blobs; no key material printed) | `authorized_keys2` present on **0/9**; option-bearing lines **0/9**; duplicate key blob **moc3 ×1** (the spec's Q16 line), others 0; OpenSSH 10.0 ×8, 9.2 (moc4) ×1 |
| what the export would leak | regex scan (`secret_scan.py`, scratchpad) over all 10 `box_truth/v1` source sections on VolcanoAI: `watchdog.json`, `mini_dudeai_state.json`, `deployment.json`, `crontab -l`, `cron_verdicts.log`, `rns_ingress_ledger.json`, `/fleet/slo`, `/api/status` | **0** PSK / token / password / PEM / ssh-key strings. Present: full cron **command lines** (×~40), `/home/<user>/` paths ×38 (MF014 operator values), **1 LAN IP** (mini: `TCPInterface[moc3-gateway/192.168.x.y:4242]`), public far-target IPs (wan_path reasons), ntfy *cron names* (no topic URL) |
| fleet crontab credentials | same fan-out: non-comment lines matching `https?://` or `token|secret|passw|bearer|api_key` | **0 and 0 on 9/9** |
| center page injection surface | `web/fleet.html` | `innerHTML` ×25 sites, `esc()` helper exists (line 435) and the sampled box renderer (576–590) escapes every peer string; **not every site audited** (S9) |
| session cookie | `web_auth.py:261` | `HttpOnly; SameSite=Strict` |
| BIC-3 login scope | `web_auth.py` docstring + `_map_auth_endpoints.py:216` | viewing (`/api/status`, `/fleet/slo`) stays LAN-trust, **no login**; only writes need an operator session |

### Drill results (VERIFIED on VolcanoAI, OpenSSH 10.0p2, 2026-10-14 ~01:40 HST)

| Client shape | What the forced command saw |
|---|---|
| `ssh … bash` (plain exec) | probe ran; `SSH_ORIGINAL_COMMAND=bash`; no shell |
| `ssh -t … true` (pty) | probe ran, `SSH_TTY` unset, stdin not a tty |
| `ssh -s … sftp` (subsystem) | **probe ran** with `SSH_ORIGINAL_COMMAND=/usr/lib/openssh/sftp-server` — the forced command replaces the subsystem too |
| `-o SetEnv=LC_EVIL=pwned -o SetEnv=PYTHONPATH=/tmp/x -o SetEnv=BASH_ENV=/tmp/y` | **`LC_EVIL=pwned` reached the environment** (AcceptEnv `LC_*`); `PYTHONPATH`, `BASH_ENV` did NOT |
| `printf 'client-stdin-payload…' \| ssh … x` | **`STDIN_BYTES=31`** — client stdin reaches the forced command |
| `ssh -A … x` (agent forwarding) | probe ran, `SSH_AUTH_SOCK` unset (refused by `restrict`) |
| `ssh -N -L 19999:127.0.0.1:5000 …` then `curl :19999` | `http=000` — forward refused |
| `ssh -L 19998:… … 'sleep 2'` | probe ran (`SSH_ORIGINAL_COMMAND=sleep 2`), forward `http=000` |
| `ssh … 'PATH=/evil bash -c id'` | probe ran; the string arrived **verbatim in `SSH_ORIGINAL_COMMAND`, never executed** |
| connect to the LAN address with `from="127.0.0.1,::1"` | `Permission denied (publickey,password)` — **indistinguishable on the client from a revoked key** |
| parent of the probe | `sshd-session: wh6gxz@notty` (bash `-c` exec'd it); env carried the CLIENT's `LANG`/`LC_ALL`/`LANGUAGE` |

## Findings

Severity: **B** = blocking (the spec must change before session 2 code),
**S** = should-fix (design gap, not an escape), **N** = note. Each names the
check that would catch its absence — every finding is owed a test or an
acceptance leg, not prose.

### B1 — The export must consume neither `SSH_ORIGINAL_COMMAND` nor stdin
Measured: both reach the forced command, for exec, pty, subsystem and
`-L`-with-command requests alike. The spec says only "no other command".
**Amend:** the `command=` string is
`/opt/<app>/scripts/box_truth_export.py --grant <fp> </dev/null` (the
redirection is run by the user's shell, so stdin is closed before Python
starts); the export never dispatches on `SSH_ORIGINAL_COMMAND` — it records
the first 120 bytes of it in the read witness as DATA. **Acceptance 5
widens** from "`bash` returns JSON" to: exec `bash`, `-t`, `-s sftp`, a stdin
payload, `-A`, and `-L` each return the same JSON and nothing else; the
forward answers `000`.

### B2 — Client-influenced environment reaches the export (`AcceptEnv LC_*`)
Measured: `LC_EVIL=pwned` landed; the client's `LC_ALL` is honoured. Not an
escape — but Python's text I/O follows the locale, so `LC_ALL=C` from a
client turns a non-ASCII rule reason into a `UnicodeDecodeError` in the
export → the center reads DARK on a healthy box, triggered by the reader.
**Amend:** `command=` invokes `python3 -I` (isolated: ignores `PYTHON*`,
user site); the export opens every file with `encoding="utf-8"` and
`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`; the grant CLI
reads `sshd -T` at grant time and shows `acceptenv` + `permituserenvironment`
in the plan (if `permituserenvironment` is ever `yes`, the TUI says so — the
`environment=` option then becomes live for whoever edits the line).
**Test:** export under `LC_ALL=C` with a non-ASCII reason in a fixture mini
state → valid JSON.

### B3 — The pasted public key is structured input that becomes an `authorized_keys` line
A paste containing a newline yields a SECOND line carrying no options — an
unrestricted grant. A paste beginning with `command="bash" ` prepended by the
TUI's own `restrict,command=…` yields a line sshd silently ignores → the grant
"succeeds" in the TUI and the center reads `refused`. **Amend:** the ONE
validator (shared module, used by TUI, CLI and center-side push) accepts
exactly one line `<type> <base64> [comment]` with `type == ssh-ed25519`,
decodes the blob and checks its wire type string and 32-byte length, requires
the comment to match `^noc:[A-Za-z0-9._-]{1,64}$`, and refuses `\r`, `\n`,
`"`, `,`, and any leading option token. The TUI displays the **SHA256
fingerprint computed from the blob**, and the typed confirm is the
fingerprint's last 8 characters — not the box name, which binds nothing.
**Tests:** two-line paste refused; option-prefixed paste refused; RSA refused;
comment with a space refused; the written line re-parses with the same
validator (read-after-write).

### B4 — Identity is the fingerprint, bound INTO the command — never the comment
The comment is chosen by the center (Q5 `noc:<center-host>`); two centers can
share one; a center can paste another's. Q13's witness keys on the comment
and Q7's "last read" would attribute reads to the wrong center. sshd does
not tell the forced command which key authenticated (`exposeauthinfo no`).
**Amend:** `command="… --grant SHA256:<fp>"` so the export knows its caller
without trusting env or comment; the witness row is
`{ts, fp, src: $SSH_CONNECTION[0], orig_cmd[:120], bytes, ms}`; list/revoke
address grants by fingerprint; the comment is display-only.

### B5 — Revoke semantics: by blob, across BOTH `AuthorizedKeysFile` paths, atomic, and `ControlMaster`-aware
(a) `sshd -T`: `authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2`
— 0/9 boxes have the second file today, so a revoke that sweeps only the
first leaves a hole that a later hand-added file re-opens. **Amend:** revoke
removes every line whose key blob equals the target's in **both** files,
refuses loudly (unless `--force`) when a matching line LACKS our
`command=…box_truth_export` marker (that was an interactive grant — the
operator decides), reports the count removed (moc3's duplicate measured today:
1). (b) Write = temp file in `~/.ssh` + fsync + rename, mode `0600`
preserved (`utils.paths.atomic_write_text_preserving`, not the plain
variant), refuse a symlinked `authorized_keys` (lstat), check `~/.ssh` is
`0700` (StrictModes would otherwise silently disable the whole file).
(c) **The kiai stanza carries `ControlMaster auto` + `ControlPersist 10m`**:
a revoked grant keeps answering for up to 10 min over the live multiplexed
master, and acceptance 3 ("next fetch refused") fails intermittently.
**Amend:** the NOC reader passes `-o ControlMaster=no -o ControlPath=none`;
the acceptance names the control.

### B6 — The center-side push calls the TARGET's grant CLI; it never edits `authorized_keys` remotely
Q4/Q18 allow a push over the operator's existing access. If the push writes
the line itself there are two validators and two writers to drift (hfm #5),
and B3/B5 have to be right twice. **Amend:** the push is exactly
`ssh <target> /opt/<app>/scripts/box_truth_grant.py add --pubkey '<line>'
--center <name>` (argv, validated on the target), the target name typed, and
the push **refuses when the registry's `expect_hostkey` for that target is
missing or does not match** what the connection presented (the 09-11 class:
a reassigned front hands the grant to the wrong box, which then reads as
moc3). Same rule for the bulk CLI used in the Q16 migration.

### B7 — The center's private key is the whole trust; say what bounds a leak
The key is unencrypted (cron reads it), `from=` is off (Q6), so possession =
read `box_truth/v1` on every granted box from anywhere that reaches `:22`.
Measured blast radius today: **no credentials** in any section on this box
and **no URL/token-shaped cron lines on 9/9 boxes** — but every cron command
line, `/home/<user>/` paths, one LAN IP, and the fleet's declared roles and
service states ride the document (topology + operator values, MF014/MF015
class). **Amend, three parts:** (a) key hygiene pinned by test: lives in
`~/.ssh/<app>_noc_ed25519` mode `0600`, never under `~/.claude/ssh`, and
`fleet_backup.sh` is asserted NOT to sweep `~/.ssh` (BELIEVED today from
reading its include logic — the test makes it VERIFIED); (b) **`box_truth/v1`
is an ALLOWLIST of the ten named sections** with a producer-side tripwire
test that fails when any section carries `ssh-… AAAA`, `-----BEGIN`,
`psk`, `token=`, `password` (the denylist-defaults-to-exposed lesson, 09-19:
a new section needs a row, not a scan); (c) **rotation is a first-class CLI
verb** — `box_truth_grant rotate` = new key, re-grant every target, verify a
read with the new key, revoke the old — so the bulk CLI (Q18) exists for
revoke too, not only for migration. A leak with no rotation path is a leak
you keep.

### B8 — `from=` off-by-default: agree, and replace it with controls that survive a renumbering
Measured: `from=` works and fails exactly like a revoke (`Permission denied
(publickey,password)`), so a center renumbering (09-12) would read as
"grant refused" — the fix named in the error would be wrong. Keep it off.
**Amend, what replaces it:** (1) a per-fingerprint rate limit inside the
export, from the witness file (refuse above e.g. 6 reads/60 s with a tiny
JSON refusal doc `{"schema":"box_truth/v1","refused":"rate","fp":…}`), which
also bounds a leaked key's cost on a Pi Zero; (2) "last read from `<src>`"
as a first-class column in the target TUI (Q7) and in the witness, so a read
from an unexpected address is VISIBLE; (3) B7(c) rotation. `from=` stays an
opt-in flag for a center with a static address.

### S1 — The spool must distinguish `refused_grant` / `identity_mismatch` / `unreachable` / `export_broken`
Today `fetch_box` discards stderr and folds every failure into all-None →
"no response". Q11 promises "grant missing or refused" with the fix named.
Measured stderr shapes: `Permission denied (publickey,…)` (key refused OR
`from=` mismatch — say "refused (key or source)"), `Host key verification
failed.`, connect timeout, and rc 0 with non-JSON stdout (export crashed, or
shell-startup noise — S6). **Amend:** the spool doc carries `transport:
{outcome, rc, stderr_head}`; the collector maps outcomes to distinct cell
reasons; acceptance 3 reads `refused_grant`.

### S2 — Host identity on the pull path comes from the registry, not `known_hosts` drift
`BatchMode=yes` already fails on an unknown or changed host key (good) but
reads as "no response". **Amend:** the reader uses a NOC `known_hosts`
generated from `fleet_naming.json` `expect_hostkey` (the 09-11 cure, stamped
by `fleet_hostkey_stamp.py`) with `StrictHostKeyChecking=yes`, and reports
`identity_mismatch` as its own outcome. A box answering with the wrong
identity must never be rendered as the box it was addressed as.

### S3 — The acceptance's "control that can fail" currently cannot fail
Q16 keeps the operator and Claude keys on the targets for interactive use.
A read via `ssh moc3` that succeeds therefore proves nothing about the NOC
grant unless the reader is pinned to the NOC key: `-i <noc key> -o
IdentitiesOnly=yes -o IdentityAgent=none`. Without `IdentityAgent=none`, an
agent holding the Claude key satisfies the target and acceptance 3 passes
after a revoke for the wrong reason. **Amend:** pin all three options in the
reader; acceptance 3 = revoke → read with the pinned reader → `refused_grant`;
acceptance 5 = `ssh -i <noc key> -o IdentitiesOnly=yes -o IdentityAgent=none
moc3 bash` → JSON.

### S4 — The export's localhost curls ride LAN trust, not the BIC-3 login — pin that it stays a VIEW read
Measured: `/api/status` and `/fleet/slo` need no session (BIC-3 gates writes
only). Fine today. If a later BIC puts a section's source behind the operator
session, a loopback curl inside the export becomes a login bypass granted to
every center. **Amend:** a test asserts every URL the export reads is in the
map's unauthenticated view set; a section that moves behind login is read
from its file/socket instead, never via a loopback cookie.

### S5 — Dual-app box (Q9): one witness path, one grant per fingerprint
Two thin entries (`/opt/meshforge/…`, `/opt/meshanchor/…`) means two possible
`command=` strings for one center. **Amend:** the witness lives at an
app-independent path (`$XDG_STATE_HOME/box_truth/reads.jsonl`), and the
grant CLI refuses a second line for a fingerprint already granted under the
other app's command (list shows which app's entry answers).

### S6 — Shell-startup output and a non-shell login shell
The forced command runs via the user's login shell (measured: bash exec'd the
probe). A `.bashrc` that echoes in the non-interactive branch prefixes the
JSON → parse failure → DARK; `SHELL=/usr/sbin/nologin` means the forced
command never runs. **Amend:** the export frames its document with a first
line marker (`__BOX_TRUTH_V1__`, the reader parses after it — today's
section-marker pattern), and the grant CLI checks `getent passwd` for a real
shell and refuses otherwise (hfm #4: reader/writer wire together).

### S7 — Resource bound on the target
Each read runs two curls, `systemctl list-unit-files`, `crontab -l` and an
awk; no serialisation, no limit. N centers at `*/2`, a misconfigured `* * * *
*`, or a leaked key keeps a Pi Zero busy. **Amend:** `flock` on the witness
file so concurrent exports serialise, plus the B8 rate limit.

### S8 — Size and time bound on the center
The HTTP leg has `MAX_BODY_BYTES` (8 MiB) and a deadline; `fetch_box` uses
`capture_output` unbounded. A buggy or compromised box can stream GBs into
the center's memory through ssh. **Amend:** read stdout through a pipe with
the same cap, kill on overflow, same deadline; outcome `export_broken`.

### S9 — The center page's injection surface widens to map-less boxes
`fleet.html` has 25 `innerHTML` sites; `esc()` exists and the sampled box
renderer escapes every peer string, and the session cookie is `HttpOnly;
SameSite=Strict` — so a compromised box cannot steal a BIC-3 session via the
NOC page from what was sampled. **Not fully audited** (25 sites). **Amend:**
a test that every interpolated peer value inside an `innerHTML` assignment
passes through `esc(` (a grep-shaped guard, MF-lint style), before the grant
adds map-less boxes to the set that can inject.

### S10 — The acting carve-out's preconditions, measured before the write
Grant/revoke is an `authorized_keys` write — the second carve-out (Q8). Plan
first (the exact line, the fingerprint, the file, the count of existing
grants), typed confirm = fingerprint tail (B3), ONE CLI, logged (touch log +
witness). **The CLI measures before writing:** `~/.ssh` `0700`, file not a
symlink, `sshd -T` present and readable (else the plan says UNKNOWN, never
assumes), login shell real (S6), the export script exists and is executable
at the absolute path it will embed, and read-after-write re-parses the file.

### S11 — "A fresh install carries zero grants" needs a guard, not a promise
**Amend:** a regression-guard test (the MF007 pattern) that no installer,
`update.sh`, provisioning or fleet script writes `authorized_keys` outside
the grant CLI; acceptance 6 then has a check of record.

### S12 — Q16 migration order and the measured moc3 duplicate
The duplicate blob on moc3 is measured (×1); which key it is was not
printed by design. **Amend:** migration per box = grant NOC key → verify a
read with the pinned reader (S3) → repoint the writer → only then remove the
duplicate via `revoke --blob` semantics (B5). The old interactive lines stay
(Q16). Record each box as a touch-log line.

### Notes
- **N1** `restrict` measured: no pty, no agent, no port forwarding, no
  X11, no `~/.ssh/rc`; the forced command runs for EVERY session request
  including the sftp subsystem. Nothing the spec forgot is still permitted
  by `restrict` alone except what B1/B2 name (stdin, `SSH_ORIGINAL_COMMAND`,
  `AcceptEnv` vars).
- **N2** `passwordauthentication yes` on VolcanoAI's sshd (seen in the
  drill's refusal string). Outside this design and pre-existing, but a NOC
  center is now a box worth brute-forcing; worth its own row, not this one.
- **N3** The symlink question from the queue row: the export reads fixed
  target-owned paths and takes zero reader input, so a symlink into a secret
  can only be planted by the target's own user — not an escape vector. The
  B7(b) tripwire covers the "someone pointed a section at a secret" case.
- **N4** The export MUST NOT read the radio (`#17` contention) — today's
  `_REMOTE_CMD` is `/proc`-only for radio state; keep that invariant as a
  test when the shell string becomes Python.

## Amended acceptance (replaces Q14's six lines once the spec is updated)

1. MA `/fleet` reads 8/8, moc3 `resolution_method: ssh_spool`.
2. Writer stopped → moc3 DARK within `SPOOL_STALE_S` (≤ 6 min); `cron_verdict` pages.
3. Revoke on moc3 → the PINNED reader (`-i <noc> -o IdentitiesOnly=yes -o IdentityAgent=none -o ControlMaster=no`) → `transport.outcome == refused_grant`, the cell says "grant refused on moc3: `box_truth_grant add …`".
4. moc3's TUI lists the grant: fingerprint, comment, last read **time and source address**.
5. The forced command: `bash`, `-t`, `-s sftp`, a stdin payload, `-A`, `-L` → same JSON, forward `000`; `LC_ALL=C` client → still valid JSON.
6. Fresh install: zero grants, and the installer-write guard test is green.
7. Two-line / option-prefixed / RSA paste → refused with the reason.
8. Rotation drill: `rotate` on the center → every target reads with the new key, old key refused.
9. Identity: a target answering with a different host key → `identity_mismatch`, never data.

## What this pass did NOT do
- No code, no spec edits beyond the amendment section the companion commit
  adds; no fleet writes except the loopback drill on this box's own
  `authorized_keys` (restored byte-identical, 0 lines remain — touch-log line
  in `review_provenance.md`).
- Did not audit MeshAnchor's center side (session 3 scope); S1–S3 apply there
  byte-identically.
- Did not print or compare key material on any box.
