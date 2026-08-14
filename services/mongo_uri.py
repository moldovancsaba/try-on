"""Resolve `mongodb+srv://` URIs to an explicit seed list before connecting.

PyMongo can do this itself, but its SRV lookup uses the system resolver, which fails
on macOS hosts whose DNS is served by a VPN or a local stub that does not answer SRV
or TXT queries. Resolving here — against the nameservers scutil reports, with public
fallbacks — keeps the worker connectable on networks where the stock path times out.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _parse_nameservers_from_scutil() -> list[str]:
    """Return the macOS resolver's nameservers, in order, deduplicated.

    Empty on failure or on non-macOS hosts, where `scutil` does not exist; callers
    are expected to fall back to public resolvers.
    """
    try:
        output = subprocess.check_output(["scutil", "--dns"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    nameservers: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = re.search(r"nameserver\[[0-9]+\] : ([^\s]+)", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value and value not in seen:
            nameservers.append(value)
            seen.add(value)
    return nameservers


@lru_cache(maxsize=8)
def _build_srv_resolver():
    """Return a DNS resolver configured from scutil, falling back to 1.1.1.1 / 8.8.8.8.

    Cached because the nameserver list is read once per process; restart the worker
    after a network change rather than expecting it to pick up new resolvers.
    """
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    nameservers = _parse_nameservers_from_scutil() or ["1.1.1.1", "8.8.8.8"]
    resolver.nameservers = nameservers
    resolver.timeout = 5.0
    resolver.lifetime = 10.0
    return resolver


@lru_cache(maxsize=32)
def normalize_mongodb_uri(uri: str) -> str:
    """Return `uri` with any SRV record expanded into a plain `mongodb://` seed list.

    Non-SRV URIs are returned untouched, as is an SRV URI whose lookup yields no
    hosts. Options from the domain's TXT record are merged into the query string
    without overriding options the caller already set, and `tls=true` is defaulted on
    (Atlas requires it). Credentials are preserved verbatim.

    Raises whatever dns.resolver raises if the SRV lookup itself fails — that is a
    hard connection error, not something to paper over with the original URI.
    """
    raw = (uri or "").strip()
    if not raw.startswith("mongodb+srv://"):
        return raw

    import dns.resolver

    parsed = urlsplit(raw)
    srv_host = parsed.hostname
    if not srv_host:
        return raw

    resolver = _build_srv_resolver()
    srv_name = f"_mongodb._tcp.{srv_host}"
    srv_answers = resolver.resolve(srv_name, "SRV")
    hosts = sorted(f"{answer.target.to_text(omit_final_dot=True)}:{answer.port}" for answer in srv_answers)
    if not hosts:
        return raw

    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.setdefault("tls", "true")
    try:
        txt_answers = resolver.resolve(srv_host, "TXT")
        for answer in txt_answers:
            txt_value = b"".join(answer.strings).decode("utf-8")
            for key, value in parse_qsl(txt_value, keep_blank_values=True):
                query_items.setdefault(key, value)
    except dns.resolver.NoAnswer:
        pass

    netloc = ",".join(hosts)
    if parsed.username is not None:
        credentials = parsed.username
        if parsed.password is not None:
            credentials += f":{parsed.password}"
        netloc = f"{credentials}@{netloc}"

    return urlunsplit(("mongodb", netloc, parsed.path, urlencode(query_items), parsed.fragment))
