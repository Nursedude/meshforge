"""box_truth/v1 — the shared contract of the NOC read grant (2026-10-14).

One versioned JSON document a box writes about ITSELF, read by any operations
center (MeshForge or MeshAnchor) over a restricted ssh key the box granted.
Spec: ``.claude/plans/noc_read_grant_2026_10_13.md``; security pass (binding):
``.claude/audits/noc_read_grant_security_pass_2026_10_14.md``.

This module is the part BOTH apps must agree on byte-for-byte (session 3 adds
it to ``parity_check``), so it is pure stdlib and carries no MeshForge import:

* the document shape — ``SCHEMA``, the ``SECTIONS`` allowlist, the ``FRAME``
  marker (S6: a chatty ``.bashrc`` must not be able to break the parse);
* the pasted-key validator and fingerprint (B3, B4) — ONE validator for the
  TUI, the CLI and the center-side push, because a second copy is a second
  place for a newline to become an unrestricted ``authorized_keys`` line;
* the grant line and the ``authorized_keys`` line parser (B1, B5);
* the producer tripwire (B7b) and the transport taxonomy (S1).

Collection (what the box reads about itself) lives in
``utils.box_truth_collect``; ``authorized_keys`` writes in
``utils.box_truth_grants``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

SCHEMA = "box_truth/v1"
SCHEMA_MAJOR = "box_truth/v1"

#: First line of every export. The reader parses only what follows it, so
#: shell-startup output (motd, a .bashrc that echoes) cannot corrupt a read.
FRAME = "__BOX_TRUTH_V1__"

#: The ALLOWLIST (B7b). A new section needs a row here — never a scan that
#: lets everything not matching a denylist through (the 09-19 lesson).
SECTIONS = ("slo", "status", "raw_watchdog", "deployment", "raw_mini",
            "radio_probe", "services", "schedules", "clock", "ingress")

#: Closed vocabulary of how a read ended (S1). Consumers switch on it; grow
#: it deliberately and update every consumer in the same change.
#:   identity_unpinned — the registry carries no expect_hostkey for the
#:                       target, so the reader refuses to call any answer
#:                       the box's (S2 has nothing to check against).
#:   rate_limited      — the export refused (B8: > RATE_MAX reads / window).
TRANSPORT_OUTCOMES = ("ok", "refused_grant", "identity_mismatch",
                      "identity_unpinned", "unreachable", "export_broken",
                      "rate_limited")

#: B8 rate limit, per fingerprint, enforced inside the export.
RATE_MAX = 6
RATE_WINDOW_S = 60.0

#: The marker that makes an authorized_keys line OURS. Revoke refuses to
#: remove a matching line without it unless forced (B5).
EXPORT_BASENAME = "box_truth_export.py"
GRANT_ARG = "--grant"
PYTHON = "/usr/bin/python3"

KEYTYPE = "ssh-ed25519"
_COMMENT_RE = re.compile(r"^noc:[A-Za-z0-9._-]{1,64}$")
_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_EXPORT_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+/" + re.escape(EXPORT_BASENAME) + "$")
_FROM_RE = re.compile(r"^[0-9A-Fa-f.:/*?,]+$")
_KEYTYPES = ("ssh-ed25519", "ssh-rsa", "ssh-dss", "ecdsa-sha2-",
             "sk-ssh-ed25519@", "sk-ecdsa-sha2-")


class KeyRejected(ValueError):
    """A pasted key or grant parameter refused — the message IS the reason
    the operator sees, so it names what was wrong."""


@dataclass(frozen=True)
class NocKey:
    keytype: str
    blob_b64: str
    comment: str
    fingerprint: str

    def line(self) -> str:
        return f"{self.keytype} {self.blob_b64} {self.comment}"


def fingerprint_of_blob(blob: bytes) -> str:
    """OpenSSH's ``SHA256:`` fingerprint of a raw key blob."""
    return "SHA256:" + base64.b64encode(
        hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


def _read_string(buf: bytes, off: int) -> Tuple[bytes, int]:
    if off + 4 > len(buf):
        raise KeyRejected("key blob truncated")
    (n,) = struct.unpack(">I", buf[off:off + 4])
    end = off + 4 + n
    if end > len(buf):
        raise KeyRejected("key blob truncated")
    return buf[off + 4:end], end


def parse_noc_pubkey(text: str) -> NocKey:
    """Validate a pasted center public key (B3). Exactly one line
    ``ssh-ed25519 <base64> noc:<name>``; anything else is refused with the
    reason. Never returns a key it would not write."""
    if not isinstance(text, str) or not text.strip():
        raise KeyRejected("empty paste")
    if text.endswith("\n"):
        text = text[:-1]           # one trailing newline from a paste is fine
    if "\n" in text or "\r" in text:
        raise KeyRejected("more than one line pasted — a second line would "
                          "become an UNRESTRICTED grant")
    parts = text.strip().split(" ")
    if parts[0] != KEYTYPE:
        if parts[0].startswith(_KEYTYPES):
            raise KeyRejected(f"key type {parts[0][:20]!r} refused — only "
                              f"{KEYTYPE} is accepted")
        raise KeyRejected("leading option token refused — paste the bare "
                          "public key, the grant adds its own options")
    if len(parts) != 3:
        raise KeyRejected("expected exactly '<type> <base64> noc:<name>' — "
                          "the comment is required and may not contain spaces")
    _t, b64, comment = parts
    if not _COMMENT_RE.match(comment):
        raise KeyRejected(f"comment {comment[:70]!r} refused — must match "
                          "noc:[A-Za-z0-9._-]{1,64}")
    try:
        blob = base64.b64decode(b64.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise KeyRejected("key data is not valid base64") from None
    wire_type, off = _read_string(blob, 0)
    if wire_type != KEYTYPE.encode("ascii"):
        raise KeyRejected(f"blob wire type {wire_type[:20]!r} does not match "
                          f"{KEYTYPE}")
    key, off = _read_string(blob, off)
    if len(key) != 32:
        raise KeyRejected(f"ed25519 key must be 32 bytes, got {len(key)}")
    if off != len(blob):
        raise KeyRejected("trailing bytes after the key in the blob")
    return NocKey(KEYTYPE, b64, comment, fingerprint_of_blob(blob))


def export_command(export_path: str, fingerprint: str) -> str:
    """The forced command (B1, B2, B4): isolated interpreter, the grant's
    fingerprint bound in, stdin closed by the user's shell before Python."""
    if not _EXPORT_PATH_RE.match(export_path or ""):
        raise KeyRejected(f"export path {export_path!r} must be an absolute "
                          f"path ending in {EXPORT_BASENAME}")
    if not _FP_RE.match(fingerprint or ""):
        raise KeyRejected(f"fingerprint {fingerprint!r} malformed")
    return f"{PYTHON} -I {export_path} {GRANT_ARG} {fingerprint} </dev/null"


def build_grant_line(key: NocKey, export_path: str, *,
                     from_: Optional[str] = None) -> str:
    """The ``authorized_keys`` line a grant writes. ``from_`` is opt-in (B8)."""
    opts = ["restrict"]
    if from_ is not None:
        if not _FROM_RE.match(from_):
            raise KeyRejected(f"from= value {from_[:60]!r} refused — "
                              "addresses/CIDRs/patterns only")
        opts.append(f'from="{from_}"')
    opts.append(f'command="{export_command(export_path, key.fingerprint)}"')
    return ",".join(opts) + " " + key.line()


@dataclass(frozen=True)
class AuthorizedKey:
    options: str
    keytype: str
    blob_b64: str
    comment: str
    fingerprint: str
    #: the fingerprint bound into OUR forced command, or None (not a grant).
    #: Set ONLY when the options are exactly what build_grant_line writes and
    #: the bound fingerprint is this key's (review A F3).
    grant_fp: Optional[str]
    raw: str
    #: mentions our export but is NOT exactly our line — unrestricted, extra
    #: options, a different command, case-changed options, or a swapped key.
    #: Listed with a warning; never treated as a read-only grant.
    foreign: bool = False

    @property
    def is_grant(self) -> bool:
        return self.grant_fp is not None


def _split_options(line: str) -> Tuple[str, str]:
    """(options, rest) — options end at the first unquoted whitespace.

    Inside quotes OpenSSH treats ONLY ``\\"`` as an escape (auth-options.c);
    any other backslash is literal. Skipping every ``\\X`` let a crafted line
    read as a decoy key, and revoke then 'removed' it while the real key kept
    authenticating (review A F2, reproduced against a real sshd)."""
    in_q = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_q and line[i + 1:i + 2] == '"':
            i += 2
            continue
        if ch == '"':
            in_q = not in_q
        elif ch in " \t" and not in_q:
            return line[:i], line[i:].lstrip()
        i += 1
    return line, ""


#: EXACTLY the options build_grant_line writes (optional from=). Anything
#: else that mentions the export is `foreign` (review A F3).
_GRANT_OPTIONS = re.compile(
    r'^restrict,(?:from="[0-9A-Fa-f.:/*?,]+",)?command="' + re.escape(PYTHON)
    + r" -I /[A-Za-z0-9._/-]+/" + re.escape(EXPORT_BASENAME) + " " + re.escape(GRANT_ARG)
    + r' (SHA256:[A-Za-z0-9+/]{43}) </dev/null"$')


def parse_authorized_keys_line(line: str) -> Optional[AuthorizedKey]:
    """Parse one ``authorized_keys`` line, or None for blank / comment /
    unparseable. Never raises."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    options = ""
    if not s.startswith(_KEYTYPES):
        options, s = _split_options(s)
    parts = s.split(None, 2)
    if len(parts) < 2 or not parts[0].startswith(_KEYTYPES):
        return None
    try:
        blob = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    fp = fingerprint_of_blob(blob)
    m = _GRANT_OPTIONS.match(options)
    grant_fp = m.group(1) if m and m.group(1) == fp else None
    foreign = grant_fp is None and EXPORT_BASENAME in options
    return AuthorizedKey(options=options, keytype=parts[0], blob_b64=parts[1],
                         comment=parts[2] if len(parts) > 2 else "",
                         fingerprint=fp, grant_fp=grant_fp, raw=line.rstrip("\n"),
                         foreign=foreign)


# ── framing ──────────────────────────────────────────────────────────────

def frame_document(doc: dict) -> str:
    """Marker line + ASCII-only JSON (ensure_ascii: a client-forced
    ``LC_ALL=C`` can never make the export unwritable, B2)."""
    return FRAME + "\n" + json.dumps(doc, ensure_ascii=True,
                                     separators=(",", ":")) + "\n"


def parse_framed(text: str) -> dict:
    """The document after the frame marker. Raises ValueError on a missing
    marker, non-JSON, a non-object, or an unknown schema major (gate 2:
    refuse loudly with the version in the reason)."""
    idx = text.find(FRAME + "\n")
    if idx < 0:
        raise ValueError("no box_truth frame marker in output")
    body = text[idx + len(FRAME) + 1:].strip()
    try:
        doc = json.loads(body)
    except RecursionError:      # a peer's nesting must not crash the reader (review B 4)
        raise ValueError("box_truth document nested too deeply") from None
    if not isinstance(doc, dict):
        raise ValueError("box_truth document is not an object")
    schema = doc.get("schema")
    if schema != SCHEMA_MAJOR:
        raise ValueError(f"unknown box_truth schema {schema!r} "
                         f"(this reader speaks {SCHEMA_MAJOR})")
    return doc


# ── B7b: producer tripwire ───────────────────────────────────────────────

_SECRET_RES = (
    re.compile(r"(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-\S+)\s+AAAA"),
    re.compile(r"-----BEGIN"),
    re.compile(r"\bpsk\s*[=:]\s*\S", re.IGNORECASE),
    re.compile(r"token=\S", re.IGNORECASE),
    re.compile(r"passw(?:or)?d\s*[=:]\s*\S", re.IGNORECASE),
    # review A F6 (2026-10-14): shapes the first cut missed
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),  # URI userinfo
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:^|\s)(?:-u|--user)\s+[^\s:]+:\S+"),                     # curl -u user:pass
)

#: A JSON KEY that names a credential, holding a non-empty STRING, is a
#: secret whatever the value looks like — 8 of the 10 sections are JSON, and
#: `{"password": "x"}` never matched a `key=value` text pattern (review A F6).
#: Flags (`token_set: true`) and empty values carry nothing to leak.
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:pass(?:word|wd|phrase)?|psk|secret|token|api[_-]?key|"
    r"private[_-]?key|credentials?)(?:$|[_-])", re.IGNORECASE)


def _secret_keys(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (isinstance(k, str) and _SECRET_KEY.search(k)
                    and isinstance(v, str) and v.strip()):
                yield k
            yield from _secret_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _secret_keys(v)


def _strings(obj: Any, key: str = ""):
    if isinstance(obj, str):
        yield obj
        if key.endswith("_b64"):
            try:
                yield base64.b64decode(obj.encode("ascii"),
                                       validate=True).decode("utf-8", "replace")
            except (binascii.Error, ValueError, UnicodeEncodeError):
                pass
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v, str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, key)


def secret_tripwire(sections: dict) -> List[Tuple[str, str]]:
    """[(section, pattern)] for every section carrying a credential-shaped
    string. The export REPLACES a tripped section (fail closed); a test pins
    that a planted secret never leaves the box."""
    hits: List[Tuple[str, str]] = []
    for name, value in (sections or {}).items():
        key = next(_secret_keys(value), None)
        if key is not None:
            hits.append((name, f"credential-named key {key!r}"))
            continue
        for s in _strings(value):
            for rx in _SECRET_RES:
                if rx.search(s):
                    hits.append((name, rx.pattern))
                    break
            else:
                continue
            break
    return hits


# ── S1: transport taxonomy ───────────────────────────────────────────────

def classify_transport(rc: Optional[int], stdout: str, stderr: str, *,
                       timed_out: bool = False, overflow: bool = False) -> str:
    """Map one ssh read to a TRANSPORT_OUTCOMES member. The reader decides
    identity_mismatch from its own post-connect key check as well; this
    covers what ssh itself reports."""
    if timed_out:
        return "unreachable"
    if overflow:
        return "export_broken"
    err = stderr or ""
    if rc == 255:
        if "Host key verification failed" in err or "HOST IDENTIFICATION HAS CHANGED" in err:
            return "identity_mismatch"
        if "Permission denied" in err or "Too many authentication failures" in err:
            return "refused_grant"
        return "unreachable"
    if rc != 0:
        return "export_broken"
    try:
        doc = parse_framed(stdout or "")
    except ValueError:
        return "export_broken"
    if doc.get("refused") in ("rate", "busy"):     # transient: try again later
        return "rate_limited"
    if doc.get("refused"):
        return "export_broken"
    return "ok"
