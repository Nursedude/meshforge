"""Fleet naming — names-first resolution with an audited IP fallback.

The DHCP-reshuffle failure class (2026-06-24 moc5 lesson): fleet configs
hardcode IPs that unreserved DHCP reassigns after power events, so healthy
boxes read as down. This module is the ONE resolution layer between a fleet
*alias* and a connectable *target*, spanning both of the fleet's deliberate
environments (Arc 2, 2026-07-11): the main-LAN segment that keeps its
consumer-router DHCP dependency, and the hardened MikroTik/OpenWrt segment
with static DNS + reservations generated from the registry here.

Resolution order for an alias (each step observable in the result):
  1. ``<alias>.<domain>``  — the fleet namespace         (method ``dns``)
  2. bare ``<alias>``      — router DNS / hosts file      (method ``bare``)
  3. registry ip_fallback  — LOGGED as fallback, so drift is visible,
                             never silent                 (method ``ip_fallback``)
  4. honest ``unresolved`` — target None; never invent    (method ``unresolved``)
An entry that is already an IP literal is passed through untouched as
``ip_literal`` — the legacy debt ``scripts/fleet_naming_audit.py`` exists
to flag, never something this module "fixes" silently.

For methods ``dns``/``bare`` the *name* is the connect target (the OS
resolver re-resolves per request — caching an address here would recreate
the disease); the observed address rides along for audits only.

Honest-failure contract (kilo.registry house style, #80 class):
  * an unreadable/invalid registry loads as ``(None, errors)`` — never as
    an empty registry that reads "no names expected, all fine";
  * alias KEYS that look like IPs are refused loudly (an IP is a hint,
    never an identity);
  * ``ip_fallback`` values MUST be IP-shaped (a name there would hide an
    unresolvable alias behind a second unresolvable name).

Registry file: ``~/.config/meshforge/fleet_naming.json`` (operator values
live outside the repo, MF014); template: ``configs/fleet_naming.example.json``.
Membership SSOT stays ``fleet_hosts`` — this registry is the *addressing*
overlay. No caching, pure stdlib, standalone-safe (works with no registry:
dns → bare → unresolved).
"""
from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REGISTRY_BASENAME = "fleet_naming.json"

# Closed method vocabulary — consumers (audit, federation status surface)
# switch on it; grow deliberately.
METHODS = ("dns", "bare", "ip_fallback", "ip_literal", "unresolved")

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# resolver contract: (name) -> first address string, or None when the name
# does not resolve. Injectable for tests; the default is bounded only by
# the system resolver's own timeouts (documented, not re-implemented).
Resolver = Callable[[str], Optional[str]]


def registry_path() -> Path:
    from utils.paths import MeshForgePaths
    return MeshForgePaths.get_config_dir() / REGISTRY_BASENAME


def default_resolver(name: str) -> Optional[str]:
    try:
        infos = socket.getaddrinfo(name, None)
    except (socket.gaierror, OSError):
        return None
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr and sockaddr[0]:
            return str(sockaddr[0])
    return None


@dataclass
class FleetHost:
    alias: str
    ip_fallback: Optional[str] = None
    mac: Optional[str] = None
    expect_hostkey: Optional[str] = None
    notes: str = ""


@dataclass
class Registry:
    domain: Optional[str]
    hosts: Dict[str, FleetHost] = field(default_factory=dict)


@dataclass
class Resolution:
    """One alias resolved. ``target`` is what a connector should use (a
    NAME for dns/bare — the OS re-resolves per request; an IP only for
    ip_fallback/ip_literal); ``address`` is the observed IP at resolve
    time, for audit display and fallback_stale comparison only."""
    alias: str
    method: str                      # one of METHODS
    target: Optional[str]            # None only when unresolved
    fqdn_tried: Optional[str] = None
    address: Optional[str] = None
    error: Optional[str] = None


def _reject_duplicate_keys(pairs):
    out: Dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate JSON key {k!r} — the second copy "
                             f"would silently replace the first")
        out[k] = v
    return out


def _validate_host(alias: str, raw, errors: List[str]) -> Optional[FleetHost]:
    # Store the STRIPPED alias — an incidental trailing space in a hand-
    # edited key would otherwise register a host no lookup can ever find
    # (silently absent from resolution and the drift scan).
    alias = alias.strip()
    where = f"hosts[{alias!r}]"
    if not alias:
        errors.append("hosts: empty/whitespace alias key")
        return None
    if _IPV4_RE.match(alias):
        errors.append(f"{where}: alias looks like an IP address — DHCP "
                      f"reassigns those; key hosts by NAME (the moc5 lesson)")
        return None
    if raw is None:
        # {"moc9": null} is legal shorthand: membership without overlay data.
        return FleetHost(alias=alias)
    if not isinstance(raw, dict):
        errors.append(f"{where}: must be an object (or null)")
        return None
    fb = raw.get("ip_fallback")
    if fb is not None:
        if not isinstance(fb, str) or not _IPV4_RE.match(fb.strip()):
            errors.append(f"{where}: ip_fallback {fb!r} must be an IPv4 "
                          f"literal — a NAME here would hide an unresolvable "
                          f"alias behind a second unresolvable name")
            return None
        fb = fb.strip()
    mac = raw.get("mac")
    if mac is not None and not isinstance(mac, str):
        errors.append(f"{where}: mac must be a string")
        return None
    key = raw.get("expect_hostkey")
    if key is not None and not isinstance(key, str):
        errors.append(f"{where}: expect_hostkey must be a string")
        return None
    return FleetHost(alias=alias, ip_fallback=fb, mac=mac,
                     expect_hostkey=key, notes=str(raw.get("notes", "")))


def load_registry(path: Optional[str] = None
                  ) -> Tuple[Optional[Registry], List[str]]:
    """(registry, []) on success — including a legitimately empty hosts
    map; (None, errors) on unreadable/invalid. Never error→empty."""
    p = Path(path) if path else registry_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"),
                          object_pairs_hook=_reject_duplicate_keys)
    except FileNotFoundError:
        return None, [f"registry not found: {p} — copy "
                      f"configs/fleet_naming.example.json there and edit"]
    except (OSError, ValueError) as e:
        return None, [f"registry unreadable: {p}: {e}"]
    if not isinstance(data, dict) or "hosts" not in data:
        return None, [f"{p}: needs a top-level 'hosts' object"]
    if not isinstance(data["hosts"], dict):
        # {"hosts": null} is the truncated-author trap — an ERROR, never
        # zero-hosts-valid (the {"rules": null} lesson, #80).
        return None, [f"{p}: 'hosts' must be an object "
                      f"(got {type(data['hosts']).__name__})"]
    domain = data.get("domain")
    if domain is not None and (not isinstance(domain, str)
                               or not domain.strip()):
        return None, [f"{p}: 'domain' must be a non-empty string when set"]
    errors: List[str] = []
    hosts: Dict[str, FleetHost] = {}
    for alias, raw in data["hosts"].items():
        host = _validate_host(str(alias), raw, errors)
        if host is not None:
            hosts[host.alias] = host
    # Duplicate ip_fallback VALUES are an authoring error the author
    # cannot have meant (a copy-pasted stanza): the audit's ip→alias map
    # would silently last-win and one box's hardcoded IP would be
    # attributed to the other. Refuse loud (the kilo duplicate-anchor
    # precedent).
    claimed: Dict[str, str] = {}
    for h in hosts.values():
        if h.ip_fallback:
            if h.ip_fallback in claimed:
                errors.append(
                    f"duplicate ip_fallback {h.ip_fallback!r} claimed by "
                    f"both {claimed[h.ip_fallback]!r} and {h.alias!r} — "
                    f"one address cannot be two boxes")
            else:
                claimed[h.ip_fallback] = h.alias
    if errors:
        return None, errors
    return Registry(domain=domain.strip() if domain else None,
                    hosts=hosts), []


def resolve(alias: str, registry: Optional[Registry] = None, *,
            resolver: Optional[Resolver] = None) -> Resolution:
    """Resolve one alias per the module contract. Standalone-safe:
    ``registry=None`` degrades to dns(skipped, no domain) → bare →
    unresolved. Never raises; never invents a target."""
    alias = str(alias).strip()
    res = resolver or default_resolver
    if _IPV4_RE.match(alias):
        return Resolution(alias=alias, method="ip_literal", target=alias,
                          address=alias)
    fqdn = None
    if registry is not None and registry.domain:
        fqdn = f"{alias}.{registry.domain}"
        addr = res(fqdn)
        if addr:
            return Resolution(alias=alias, method="dns", target=fqdn,
                              fqdn_tried=fqdn, address=addr)
    addr = res(alias)
    if addr:
        return Resolution(alias=alias, method="bare", target=alias,
                          fqdn_tried=fqdn, address=addr)
    host = registry.hosts.get(alias) if registry is not None else None
    if host is not None and host.ip_fallback:
        return Resolution(
            alias=alias, method="ip_fallback", target=host.ip_fallback,
            fqdn_tried=fqdn, address=host.ip_fallback,
            error="name did not resolve — using registry ip_fallback "
                  "(refresh DNS/reservations; the audit flags this)")
    return Resolution(
        alias=alias, method="unresolved", target=None, fqdn_tried=fqdn,
        error=f"{alias!r} did not resolve"
              + (f" (tried {fqdn})" if fqdn else "")
              + " and the registry carries no ip_fallback")


def resolve_all(aliases: List[str], registry: Optional[Registry] = None, *,
                resolver: Optional[Resolver] = None) -> List[Resolution]:
    return [resolve(a, registry, resolver=resolver) for a in aliases]


def connect_target(entry: str, registry: Optional[Registry] = None, *,
                   resolver: Optional[Resolver] = None
                   ) -> Tuple[str, str]:
    """The collectors' one-liner: (target-to-connect-to, method).

    Behavior-preserving for legacy configs: an IP literal or an
    unresolved name passes through UNCHANGED (method labels it) — a
    connector failing on the original entry is more diagnosable than one
    failing on a target this layer invented. Only a registry-backed
    fallback ever substitutes a different target, and that substitution
    is labeled ``ip_fallback``.
    """
    r = resolve(entry, registry, resolver=resolver)
    if r.method == "unresolved":
        return entry, r.method
    return r.target or entry, r.method


_QUIET_WARNED = False


def load_registry_quiet(path: Optional[str] = None) -> Optional[Registry]:
    """Wiring helper for call sites where naming is OPTIONAL (map
    collectors): a missing/broken registry yields None (legacy IP behavior
    continues) — the audit script is where registry errors get LOUD.

    One honesty exception (#80): "never configured" and "configured but
    now CORRUPT" must not be the same silence — a registry that WAS
    working and silently stopped applying is the DHCP-reshuffle class
    reintroducing itself. A present-but-unloadable file logs one WARNING
    per process (a witness, not a page)."""
    global _QUIET_WARNED
    reg, errors = load_registry(path)
    if reg is None and errors and not any("not found" in e for e in errors) \
            and not _QUIET_WARNED:
        import logging
        logging.getLogger(__name__).warning(
            "fleet naming registry present but UNLOADABLE — collectors "
            "reverted to legacy raw-entry behavior: %s", errors[0])
        _QUIET_WARNED = True
    return reg
