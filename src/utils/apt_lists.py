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
  hold", which ages through a quiet archive too: it backs only the 7-day
  "periodic update is not running" finding. ``partial/``, ``lock`` and
  ``auxfiles`` are excluded — a failed or locked update touches those.
* ``security_views`` — for each ``*-security`` suite, the ``Date:`` Debian
  signed into the ``InRelease`` this box holds. Compared with the archive's
  CURRENT Date (both are Debian's clock, never the box's), it says how far
  behind the security archive this box's "0 security pending" is.
"""
from __future__ import annotations


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
        newest = m if newest is None else max(newest, m)
    if newest is None:
        return None
    return int(max(0.0, now - newest) / 3600)


def parse_release_date(text):
    """Epoch seconds of the ``Date:`` field of a (In)Release text, or None.
    The PGP armour before the paragraph is skipped by reading every line."""
    import email.utils
    for line in text.splitlines():
        if line.startswith("Date:"):
            try:
                return email.utils.parsedate_to_datetime(
                    line.split(":", 1)[1].strip()).timestamp()
            except Exception:              # any parse failure is "unknown"
                return None
    return None


def security_views(lists_dir):
    """[{"file", "suite", "date_ts"}] for every ``*-security`` InRelease here.
    ``date_ts`` None = the view exists but its Date could not be read — kept,
    never dropped, so one bad suite cannot leave a fresh sibling to vouch for
    the box. An unreadable directory raises OSError: the caller must render
    that as unobservable, not as "no security suite"."""
    import os
    views = []
    for name in sorted(os.listdir(lists_dir)):
        if not name.endswith("_InRelease") or "_dists_" not in name:
            continue
        try:
            with open(os.path.join(lists_dir, name), errors="replace") as fh:
                head = fh.read(16384)
        except OSError:
            views.append({"file": name, "suite": None, "date_ts": None})
            continue
        suite = None
        for line in head.splitlines():
            if line.startswith("Suite:"):
                suite = line.split(":", 1)[1].strip()
                break
        if not suite or not suite.endswith("-security"):
            continue
        views.append({"file": name, "suite": suite,
                      "date_ts": parse_release_date(head)})
    return views
