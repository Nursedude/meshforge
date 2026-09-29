"""Who the NOC map trusts: client IP, browser Origin, and the dialled Host.

Split out of ``map_http_handler.py`` for the MF025 size cap (2026-09-28).
Pure functions, no handler state, so both gates — the HTTP read/write gate on
``MapRequestHandler`` and the live-updates WebSocket handshake — share ONE rule
set (honest_failure_modes #5). ``map_http_handler`` re-exports every name.
"""
import ipaddress
import re
from typing import List, Optional, Tuple

#: Origins trusted when no ``--cors-origins`` was configured (a standalone,
#: loopback-bound map). ``MapRequestHandler._DEFAULT_ORIGINS`` IS this list.
DEFAULT_ORIGINS = ['http://localhost', 'https://localhost']


def _origin_allowed(origin: str, allowed: Optional[List[str]]) -> bool:
    """Exact-or-/24 CORS origin match (tail-anchored).

    ``allowed`` holds the prefixes passed via ``--cors-origins``. Two shapes:
      * exact host  (``http://localhost``) — the request origin must equal it,
        optionally with a ``:port`` suffix and nothing else after.
      * IP /24 prefix (``http://192.0.2.`` — trailing dot) — the origin must
        complete the final octet with 1-3 digits (+ optional ``:port``).

    A bare ``origin.startswith(prefix)`` let ``http://192.0.2.evil.com`` and
    ``http://localhost.attacker.example`` pass the check (subdomain-suffix CORS
    bypass — an attacker page reads the whole NOC API cross-origin). Anchoring
    the tail with ``$`` closes that while preserving the /24 intent.
    """
    if not origin or not allowed:
        return False
    for prefix in allowed:
        if not prefix:
            continue
        esc = re.escape(prefix)
        # trailing-dot prefix completes an IP octet; otherwise exact host+port
        pat = esc + (r'\d{1,3}(?::\d+)?$' if prefix.endswith('.') else r'(?::\d+)?$')
        if re.match(pat, origin):
            return True
    return False


def _trusted_networks_from_origins(allowed: Optional[List[str]]):
    """Parse the CORS allow-list host parts into ``ip_network`` objects, used to
    gate state-changing / log-exposing endpoints by client IP on a ``0.0.0.0``
    bind. A ``.``-terminated prefix (``http://192.0.2.``) → the /24; a bare IP
    host → /32. Non-IP hosts (``localhost``) are skipped."""
    nets = []
    for prefix in allowed or []:
        if not prefix:
            continue
        host = prefix.split('://', 1)[-1].split(':', 1)[0].rstrip('.')
        cidr = host + '.0/24' if prefix.endswith('.') else host + '/32'
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue  # non-IP host (localhost) or malformed prefix
    return nets


#: Operator-declared extra networks the read gate trusts (2026-09-25). The gate
#: derives its LAN from `hostname -I`, so a workstation on ANOTHER of the
#: operator's LANs (a second building) was refused by every box not on it —
#: "forbidden" on the Fleet monitor. Local config, never repo (MF014).
TRUSTED_NETWORKS_FILE = "/etc/meshforge/trusted_networks"


def load_extra_trusted_networks(text: str) -> Tuple[List[str], List[str]]:
    """Parse the trusted-networks file: one private IPv4 /24 per line
    (``a.b.c.0/24``), ``#`` comments. Returns (origin prefixes the gate
    already understands — ``http://a.b.c.`` — and one reason per REFUSED line).

    Deliberately narrow: this widens who may read service journals, so only
    RFC 1918 /24s are accepted. A public network, a wider prefix, or a
    malformed line is refused LOUDLY (never silently skipped, never widened)."""
    origins: List[str] = []
    refused: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            net = ipaddress.ip_network(line, strict=True)
        except ValueError as e:
            refused.append(f"line {n}: not a network ({e})")
            continue
        if net.version != 4 or net.prefixlen != 24:
            refused.append(f"line {n}: only IPv4 /24 is accepted, got /{net.prefixlen}")
            continue
        if not net.is_private:
            refused.append(f"line {n}: not a private network")
            continue
        origins.append("http://" + str(net.network_address).rsplit(".", 1)[0] + ".")
    return origins, refused


def _client_ip_trusted(client_host: str, allowed: Optional[List[str]]) -> bool:
    """True if ``client_host`` is loopback or inside a configured LAN origin.

    With no ``--cors-origins`` configured (``allowed`` None/empty) only loopback
    is trusted — the secure default for a box that never opted a LAN in."""
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return any(ip in net for net in _trusted_networks_from_origins(allowed))


#: Name suffixes only the browser's LOCAL resolver can answer (mDNS, RFC 8375
#: home.arpa, ICANN-reserved .internal, AREDN's local.mesh). A public name is
#: attacker-controllable (DNS rebinding), so it never earns same-host trust.
_LOCAL_NAME_SUFFIXES = (".local", ".home.arpa", ".internal", ".local.mesh")
_LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


def _local_only_name(name: str) -> bool:
    """True for a hostname no public DNS can hand an attacker: one label
    (``moc``, resolved from /etc/hosts or the LAN's own DNS) or a reserved
    local suffix. IP literals are handled by the /24 rule, never here."""
    name = (name or "").lower().rstrip(".")
    labels = name.split(".")
    if not name or not all(_LABEL_RE.match(lbl) for lbl in labels):
        return False
    if len(labels) == 1:
        return not name.isdigit()
    return name.endswith(_LOCAL_NAME_SUFFIXES)


def _same_local_host(origin: str, request_host: Optional[str],
                     page_port: Optional[int]) -> bool:
    """The page and the socket were reached by the SAME local-only name: the
    map opened as ``http://moc:5000`` connects ``ws://moc:5001`` (the status
    endpoint builds that URL from the page's Host), so Origin names the host
    the browser itself dialled. Found 2026-09-25: the IP-prefix list refused
    every map opened by hostname (moc: IP origin 101, hostname origin 403)."""
    if not request_host or page_port is None:
        return False
    m = re.match(r'^http://([^/:]+):(\d+)$', origin or "")
    if not m or int(m.group(2)) != int(page_port):
        return False
    name = m.group(1).lower()
    dialled = request_host.rsplit(":", 1)[0].lower() if ":" in request_host else request_host.lower()
    return name == dialled and _local_only_name(name)


def ws_client_admitted(client_host: str, origin: str,
                       allowed: Optional[List[str]],
                       request_host: Optional[str] = None,
                       page_port: Optional[int] = None) -> bool:
    """The live-updates WebSocket admits exactly who the HTTP read gate does.

    It pushes the same node/message data the page already polls, so the
    audience is the same: client IP loopback or inside the allow-list (incl.
    /etc/meshforge/trusted_networks), AND a browser Origin the CORS rule
    accepts — a trusted LAN browser visiting a hostile page must not be able
    to open it. Operator 2026-09-25: fleet = "continuity and flow"; a
    standalone map bound to loopback stays loopback (the bind follows the map).
    The Origin may also be the map page itself reached by a local-only name
    (``_same_local_host``) — the IP gate still applies first.
    """
    if not _client_ip_trusted(client_host, allowed):
        return False
    return browser_origin_allowed(origin, allowed, request_host, page_port)


def _host_header_trusted(host: Optional[str]) -> bool:
    """The name the client dialled is one DNS rebinding cannot hand an attacker.

    Rebinding: a LAN browser opens ``http://evil.example:5000``, the attacker
    re-points ``evil.example`` at this box, and the page's SAME-ORIGIN fetch
    arrives from the victim's trusted LAN address — the IP gate admits it. The
    one thing the attacker cannot change is the Host the browser sends: their
    own public name. So a trusted read also needs Host to be an IP literal, or
    a local-only name (``_local_only_name``: single label, ``.local``,
    ``.home.arpa``, ``.internal``, ``.local.mesh``) — the same rule the
    WebSocket's same-host Origin check uses (Fable review 2026-09-28, F2).

    No Host header at all = not a browser (HTTP/1.0 tooling) → trusted; an
    EMPTY or malformed one is refused.
    """
    if host is None:
        return True
    host = host.strip()
    m = re.match(r'^\[([0-9A-Fa-f:.]+)\](?::\d+)?$', host)       # [v6]:port
    if m:
        name = m.group(1)
    else:
        name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return _local_only_name(name)


def browser_origin_allowed(origin: str, allowed: Optional[List[str]],
                           request_host: Optional[str] = None,
                           page_port: Optional[int] = None) -> bool:
    """A browser Origin the map trusts: the CORS rule, or the map page itself
    reached by a local-only name. ONE rule for the WebSocket handshake and the
    state-changing POSTs (``_reject_cross_site_write``), so they cannot drift."""
    origins = allowed if allowed else DEFAULT_ORIGINS + ['http://127.0.0.1']
    return (_origin_allowed(origin, origins)
            or _same_local_host(origin, request_host, page_port))
