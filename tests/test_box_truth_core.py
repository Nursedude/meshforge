"""box_truth/v1 shared core — validator, fingerprint, framing, tripwire,
transport taxonomy (NOC read grant session 2, 2026-10-14).

Every test here is owed by a named security-pass finding
(.claude/audits/noc_read_grant_security_pass_2026_10_14.md): B3 (the pasted
key is structured input), B4 (identity = fingerprint), B7b (allowlist +
tripwire), S1 (outcome taxonomy), S6 (frame marker).
"""
import base64
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402


def _blob(keytype=b"ssh-ed25519", key=b"\x11" * 32):
    return (struct.pack(">I", len(keytype)) + keytype
            + struct.pack(">I", len(key)) + key)


def _line(comment="noc:center1", blob=None, keytype="ssh-ed25519"):
    b64 = base64.b64encode(blob if blob is not None else _blob()).decode()
    return f"{keytype} {b64} {comment}".rstrip()


# ── B3: the validator ────────────────────────────────────────────────────

def test_valid_key_parses_and_fingerprints():
    k = bt.parse_noc_pubkey(_line())
    digest = base64.b64encode(hashlib.sha256(_blob()).digest()).decode().rstrip("=")
    assert k.fingerprint == "SHA256:" + digest
    assert k.comment == "noc:center1"
    assert k.keytype == "ssh-ed25519"


def test_trailing_newline_from_a_paste_is_tolerated():
    assert bt.parse_noc_pubkey(_line() + "\n").comment == "noc:center1"


@pytest.mark.parametrize("text,why", [
    (_line() + "\n" + _line(), "line"),                      # two-line paste
    (_line() + "\r", "line"),                                # stray CR
    ('command="bash" ' + _line(), "option"),                 # option-prefixed
    ("restrict " + _line(), "option"),
    (_line(comment="noc:a b"), "comment"),                   # space in comment
    (_line(comment="noc:a,b"), "comment"),
    (_line(comment='noc:a"b'), "comment"),
    (_line(comment="laptop"), "comment"),                    # not noc:
    (_line(comment=""), "comment"),                          # comment required
    (_line(comment="noc:" + "a" * 65), "comment"),
    ("ssh-rsa " + base64.b64encode(_blob(b"ssh-rsa")).decode() + " noc:x", "ssh-ed25519"),
    (_line(blob=_blob(b"ssh-rsa")), "wire type"),            # type lies about blob
    (_line(blob=_blob(key=b"\x11" * 31)), "32"),             # short key
    (_line(blob=_blob() + b"x"), "trailing"),
    ("ssh-ed25519 !!!notbase64 noc:x", "base64"),
    ("", "empty"),
])
def test_validator_refuses_with_a_reason(text, why):
    with pytest.raises(bt.KeyRejected) as ei:
        bt.parse_noc_pubkey(text)
    assert why in str(ei.value)


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_fingerprint_matches_openssh(tmp_path):
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                    "noc:test", "-f", str(key)], check=True, timeout=30)
    pub = (tmp_path / "k.pub").read_text()
    out = subprocess.run(["ssh-keygen", "-lf", str(tmp_path / "k.pub")],
                         capture_output=True, text=True, timeout=30).stdout
    assert bt.parse_noc_pubkey(pub).fingerprint == out.split()[1]


# ── B1/B4: the grant line ────────────────────────────────────────────────

def test_grant_line_binds_fingerprint_closes_stdin_and_reparses():
    k = bt.parse_noc_pubkey(_line())
    line = bt.build_grant_line(k, "/opt/meshforge/scripts/box_truth_export.py")
    assert line.startswith('restrict,command="/usr/bin/python3 -I '
                           '/opt/meshforge/scripts/box_truth_export.py '
                           f'--grant {k.fingerprint} </dev/null" ')
    entry = bt.parse_authorized_keys_line(line)
    assert entry.fingerprint == k.fingerprint
    assert entry.grant_fp == k.fingerprint
    assert entry.is_grant
    assert "\n" not in line


def test_grant_line_from_is_opt_in_and_validated():
    k = bt.parse_noc_pubkey(_line())
    line = bt.build_grant_line(k, "/opt/x/box_truth_export.py",
                               from_="192.0.2.0/24,::1")
    assert line.startswith('restrict,from="192.0.2.0/24,::1",command=')
    with pytest.raises(bt.KeyRejected):
        bt.build_grant_line(k, "/opt/x/box_truth_export.py", from_='1.2.3.4" x')


def test_grant_line_refuses_a_relative_or_odd_export_path():
    k = bt.parse_noc_pubkey(_line())
    for bad in ("scripts/box_truth_export.py", '/opt/a"b/box_truth_export.py',
                "/opt/x/other.py"):
        with pytest.raises(bt.KeyRejected):
            bt.build_grant_line(k, bad)


# ── authorized_keys line parsing ─────────────────────────────────────────

def test_plain_line_is_not_a_grant():
    e = bt.parse_authorized_keys_line(_line(comment="wh6gxz@moc3"))
    assert not e.is_grant and e.grant_fp is None
    assert e.fingerprint.startswith("SHA256:")


def test_quoted_option_with_spaces_and_commas_parses():
    line = ('restrict,port-forwarding,permitlisten="localhost:2222",'
            'command="echo a, b \\"c\\"" ' + _line(comment="x"))
    e = bt.parse_authorized_keys_line(line)
    assert e.keytype == "ssh-ed25519" and not e.is_grant


def test_comment_and_blank_lines_parse_to_none():
    assert bt.parse_authorized_keys_line("# hello") is None
    assert bt.parse_authorized_keys_line("   ") is None


def test_unparseable_line_is_none_not_a_crash():
    assert bt.parse_authorized_keys_line("garbage") is None


# ── S6: framing ──────────────────────────────────────────────────────────

def test_frame_is_ascii_and_parses_after_shell_noise():
    doc = {"schema": bt.SCHEMA, "sections": {"raw_mini": {"r": "café ☕"}}}
    framed = bt.frame_document(doc)
    framed.encode("ascii")                       # LC_ALL=C client can't break it
    noisy = "Welcome to bash!\nmotd line\n" + framed
    assert bt.parse_framed(noisy)["sections"]["raw_mini"]["r"] == "café ☕"


def test_parse_framed_refuses_missing_marker_and_garbage():
    with pytest.raises(ValueError):
        bt.parse_framed('{"schema": "box_truth/v1"}')
    with pytest.raises(ValueError):
        bt.parse_framed(bt.FRAME + "\nnot json\n")
    with pytest.raises(ValueError):
        bt.parse_framed(bt.FRAME + "\n[1, 2]\n")


def test_parse_framed_refuses_unknown_major():
    with pytest.raises(ValueError) as ei:
        bt.parse_framed(bt.FRAME + "\n" + json.dumps({"schema": "box_truth/v2"}))
    assert "box_truth/v2" in str(ei.value)


# ── B7b: allowlist + tripwire ────────────────────────────────────────────

def test_sections_allowlist_is_the_ten_named():
    assert bt.SECTIONS == ("slo", "status", "raw_watchdog", "deployment",
                           "raw_mini", "radio_probe", "services", "schedules",
                           "clock", "ingress")


@pytest.mark.parametrize("planted", [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "psk=hunter2", "https://x/?token=abc", "password: hunter2",
])
def test_tripwire_fires_on_planted_secret(planted):
    hits = bt.secret_tripwire({"raw_mini": {"deep": [{"x": planted}]}})
    assert hits and hits[0][0] == "raw_mini"


def test_tripwire_scans_base64_schedules_decoded():
    b64 = base64.b64encode(b"0 * * * * curl https://x/?token=abc\n").decode()
    hits = bt.secret_tripwire({"schedules": {"crontab_b64": b64}})
    assert hits and hits[0][0] == "schedules"


def test_tripwire_clean_doc_is_clean():
    assert bt.secret_tripwire({"raw_mini": {"rules": 76, "note": "ok"}}) == []


# ── S1: transport taxonomy ───────────────────────────────────────────────

def test_outcomes_are_a_closed_vocabulary():
    assert set(bt.TRANSPORT_OUTCOMES) == {
        "ok", "refused_grant", "identity_mismatch", "identity_unpinned",
        "unreachable", "export_broken", "rate_limited"}


@pytest.mark.parametrize("rc,stderr,outcome", [
    (255, "wh6gxz@moc3: Permission denied (publickey,password).", "refused_grant"),
    (255, "Host key verification failed.", "identity_mismatch"),
    (255, "ssh: connect to host moc3 port 22: Connection timed out", "unreachable"),
    (255, "ssh: Could not resolve hostname moc3: Name or service not known", "unreachable"),
    (255, "ssh: connect to host x port 22: No route to host", "unreachable"),
    (255, "kex_exchange_identification: Connection closed by remote host", "unreachable"),
    (2, "python3: can't open file '/opt/meshforge/scripts/box_truth_export.py'", "export_broken"),
    (1, "Traceback (most recent call last):", "export_broken"),
])
def test_classify_failure_shapes(rc, stderr, outcome):
    assert bt.classify_transport(rc, "", stderr) == outcome


def test_classify_ok_needs_a_frame():
    good = bt.frame_document({"schema": bt.SCHEMA, "sections": {}})
    assert bt.classify_transport(0, good, "") == "ok"
    assert bt.classify_transport(0, "motd only\n", "") == "export_broken"


def test_classify_rate_refusal_doc():
    doc = bt.frame_document({"schema": bt.SCHEMA, "refused": "rate", "fp": "SHA256:x"})
    assert bt.classify_transport(0, doc, "") == "rate_limited"


def test_classify_timeout_and_overflow():
    assert bt.classify_transport(None, "", "", timed_out=True) == "unreachable"
    assert bt.classify_transport(0, "x", "", overflow=True) == "export_broken"


def test_f3_built_line_with_from_is_a_grant():
    k = bt.parse_noc_pubkey(_line())
    line = bt.build_grant_line(k, "/opt/meshforge/scripts/box_truth_export.py", from_="192.0.2.0/24")
    e = bt.parse_authorized_keys_line(line)
    assert e.is_grant and not e.foreign


def test_f3_grant_fp_must_match_the_key_blob():
    k = bt.parse_noc_pubkey(_line())
    other = bt.parse_noc_pubkey(_line(blob=_blob(key=b"\x22" * 32)))
    line = bt.build_grant_line(k, "/opt/meshforge/scripts/box_truth_export.py")
    swapped = line.replace(k.blob_b64, other.blob_b64)
    e = bt.parse_authorized_keys_line(swapped)
    assert not e.is_grant and e.foreign


def test_b4_deep_nesting_is_a_valueerror_not_a_crash():
    with pytest.raises(ValueError):
        bt.parse_framed(bt.FRAME + "\n" + "[" * 100000 + "]" * 100000)
    assert bt.classify_transport(0, bt.FRAME + "\n" + "[" * 100000, "") == "export_broken"


# ── review A F6: secrets stored as JSON keys/values ─────────────────────

@pytest.mark.parametrize("section", [
    {"password": "hunter2"},
    {"psk": "AQ=="},
    {"ntfy_token": "tk_abcdef123456"},
    {"cfg": {"api_key": "x9"}},
    {"url": "mqtt://user:pass@broker.example:1883"},
    {"hdr": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc"},
    {"k": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv"},
])
def test_f6_structured_secrets_trip(section):
    assert bt.secret_tripwire({"raw_mini": section}), section


def test_f6_curl_userinfo_in_b64_crontab_trips():
    b64 = base64.b64encode(b"0 * * * * curl -u op:Secr3t https://x\n").decode()
    assert bt.secret_tripwire({"schedules": {"crontab_b64": b64}})


@pytest.mark.parametrize("section", [
    {"auth": {"refusals": 0, "store": "ok", "sessions": 1}},   # map status block (measured)
    {"token_set": True, "password_auth": False},               # flags, not secrets
    {"psk": ""},                                                # empty = nothing to leak
    {"url": "https://example.org/path"},
    {"reason": "rnsd RPC token mismatch fixed"},
])
def test_f6_benign_shapes_do_not_withhold(section):
    assert bt.secret_tripwire({"raw_mini": section}) == []
