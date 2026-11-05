"""What a box's apt lists can and cannot tell you — ONE definition, two consumers.

Consumers: the TUI platform screen (imports this) and the fleet sweep's
remote reporter in ``scripts/dep_advisory_check.py`` (embeds this module's
function SOURCE into the script it ships over ssh). Before 2026-11-05 each
measured list age its own way, and both could read a FAILED refresh as fresh
(honest_failure_modes #5: two consumers of one artifact share ONE derivation).

STDLIB ONLY, and every import lives inside the function: the reporter runs
these functions on boxes that have no MeshForge checkout on ``sys.path``.

Two quantities, never one:

* ``lists_age_h`` — hours since the newest LIST file changed. apt stamps each
  list with the server's ``Last-Modified``, so this is "the newest content we
  hold", which ages through a quiet archive too (Debian's main stable
  InRelease was dated 2026-09-12 on 11-05) — so it is SHOWN, and judges
  staleness only on a box with no update-stamp. ``partial/``, ``lock`` and
  ``auxfiles`` are excluded — a failed or locked update touches those.
* ``update_stamp_age_h`` — hours since ``apt.systemd.daily`` last SUCCEEDED
  an ``apt-get update`` (it touches ``/var/lib/apt/periodic/update-stamp``
  only on success). This is the ONLY judge of the "periodic update is not
  running" finding (r5 #4); an ABSENT stamp is itself that finding. It is
  the box's clock: a stamp in the future reads unknown, never fresh.
  Residual (r3 #3): apt exits 0 on a per-source ``W: Failed to fetch``, so
  one failing non-security source still refreshes the stamp.
* ``security_views`` — for each ``*-security`` suite, the ``Date:`` Debian
  signed into the ``InRelease`` this box holds. Compared with the archive's
  CURRENT Date (both are Debian's clock, never the box's), it says how far
  behind the security archive this box's "0 security pending" is.
"""
from __future__ import annotations

#: No SUCCESSFUL periodic ``apt-get update`` for this long means the periodic
#: update is not running — pending counts are a lower bound. Shared by the
#: fleet sweep and the TUI platform screen (one constant, hfm #5).
APT_LISTS_STALE_H = 7 * 24

#: Where apt.systemd.daily records its last SUCCESSFUL list update.
UPDATE_STAMP = "/var/lib/apt/periodic/update-stamp"


def update_stamp_age_h(stamp_path, now):
    """Hours since the update-stamp was touched, or None (absent, unreadable,
    or more than 10 min in the future — a clock that went backward)."""
    import os
    try:
        m = os.stat(stamp_path).st_mtime
    except OSError:
        return None
    if m - now > 600:
        return None
    return int(max(0.0, now - m) / 3600)


def apt_lists_age_h(lists_dir, now):
    """Hours since the newest list file under ``lists_dir`` changed, or None
    when there is no list file or the directory cannot be read."""
    import os
    try:
        names = os.listdir(lists_dir)
    except OSError:
        return None
    newest = None
    for name in names:
        if "_dists_" not in name:          # lock, partial/, auxfiles: not lists
            continue
        try:
            m = os.stat(os.path.join(lists_dir, name)).st_mtime
        except OSError:
            continue
        if m - now > 600:
            # A server Last-Modified ahead of this box's clock (box clock
            # behind, or one skewed third-party server). Skipped: list age is
            # DISPLAY ONLY since r5 — the stale finding judges the update-stamp
            # alone — so one odd file must not blind the box (r3 #8/r4 #3/r5 #4).
            continue
        newest = m if newest is None else max(newest, m)
    if newest is None:
        return None
    return int(max(0.0, now - newest) / 3600)


def parse_release_date(text):
    """Epoch seconds of the ``Date:`` field of a (In)Release text, or None.
    The PGP armour before the paragraph is skipped by reading every line.

    Only a COMPLETE, ZONED RFC 2822 date is a date (r5 #2/#3, measured by the
    reviewer): a power-loss cut inside the time parses "…21:4" as 21:04, and
    a cut inside the zone ("…21:43:53 U") yields a NAIVE datetime that
    ``timestamp()`` reads in the box's local zone — 10 h off on an HST box.
    Either would be a plausible WRONG instant, worse than unknown."""
    import email.utils
    import re
    for line in text.splitlines():
        if line.startswith("Date:"):
            value = line.split(":", 1)[1].strip()
            if not re.match(r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} "
                            r"\d{2}:\d{2}:\d{2} (UTC|GMT|[+-]\d{4})$", value):
                return None
            try:
                dt = email.utils.parsedate_to_datetime(value)
            except Exception:              # any parse failure is "unknown"
                return None
            return dt.timestamp() if dt.tzinfo is not None else None
    return None


def security_views(lists_dir):
    """[{"file", "suite", "date_ts"}] for every security-suite InRelease here.

    A view is a security view by its FILE NAME (``…_dists_<suite>-security_
    InRelease`` — the name apt itself derives from sources.list), never by
    the ``Suite:`` line inside it: a power-loss truncation can cut the file
    anywhere, including inside that line, and must never make the view
    vanish (r2 #4 → r3 #1 → r4 #5 — three patches of one class, closed by
    not reading the field). Contents only supply ``suite`` (display) and
    ``date_ts``; ``date_ts`` None = unreadable or unparseable, kept, so one
    bad suite cannot leave a fresh sibling to vouch for the box. An
    unreadable DIRECTORY raises OSError for the caller to witness."""
    import os
    views = []
    for name in sorted(os.listdir(lists_dir)):
        # Any dist path depth: `…_dists_<x>_<y>-security_InRelease` too (r5 #8).
        if "_dists_" not in name or not name.endswith("-security_InRelease"):
            continue
        try:
            with open(os.path.join(lists_dir, name), errors="replace") as fh:
                head = fh.read(16384)
        except OSError:
            head = ""
        suite = None
        for line in head.splitlines():
            if line.startswith("Suite:"):
                suite = line.split(":", 1)[1].strip() or None
                break
        views.append({"file": name, "suite": suite,
                      "date_ts": parse_release_date(head) if head else None})
    return views
