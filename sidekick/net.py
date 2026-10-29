"""Try IPv4 addresses before IPv6 for every outgoing connection.

Python connects to a host's addresses one at a time, IPv6 first. On networks
where IPv6 is advertised but doesn't actually route, each IPv6 attempt hangs
~21 s before falling back, so a call to OpenRouter took ~43 s instead of ~1 s.
Browsers and curl race both families, which is why they never notice.
IPv6 stays available as a fallback. Set SIDEKICK_PREFER_IPV6=1 to keep the
system's order.
"""

from __future__ import annotations

import os
import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    return sorted(_original_getaddrinfo(*args, **kwargs), key=lambda ai: ai[0] != socket.AF_INET)


def prefer_ipv4() -> None:
    if not os.getenv("SIDEKICK_PREFER_IPV6"):
        socket.getaddrinfo = _ipv4_first
