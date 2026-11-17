"""URL safety guard: SSRF protection, scheme restrictions, size limits.

Blocks requests to private/loopback/link-local/reserved addresses and unsafe
schemes. Does NOT bypass authentication, CAPTCHAs, paywalls or anti-bot
mechanisms — blocked or restricted pages are simply reported as failed.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal",
                 "instance-data", "internal"}


class UnsafeURLError(ValueError):
    pass


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified
            or addr.is_loopback)


def validate_url(url: str, max_length: int = 2048) -> str:
    """Validate an external URL. Returns the cleaned URL or raises UnsafeURLError."""
    if not url or len(url) > max_length:
        raise UnsafeURLError("URL missing or too long")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Scheme not allowed: {parts.scheme or '(none)'}")
    if not parts.hostname:
        raise UnsafeURLError("URL has no hostname")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URL are not allowed")
    host = parts.hostname.lower()
    if host in BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError(f"Host not allowed: {host}")
    # Literal IPs must be public; hostnames are validated via resolution below.
    try:
        ipaddress.ip_address(host)
        if _is_private_ip(host):
            raise UnsafeURLError(f"Private/reserved address not allowed: {host}")
    except ValueError:
        pass  # hostname, not a literal IP
    # Resolve hostname and check every resolved address (DNS-based SSRF guard)
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Cannot resolve host {host}: {e}")
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            raise UnsafeURLError(f"Host {host} resolves to private address {ip}")
    port = parts.port
    if port is not None and not (0 < port < 65536):
        raise UnsafeURLError(f"Invalid port: {port}")
    return url


def is_safe_url(url: str) -> bool:
    try:
        validate_url(url)
        return True
    except UnsafeURLError:
        return False
