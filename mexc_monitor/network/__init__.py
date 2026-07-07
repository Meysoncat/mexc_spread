"""Network and DNS modules for MEXC Spread Monitor.

This package provides:
- Custom DNS resolution with fallback support
- DNS-over-HTTPS (DOH) support
- DNS caching and monitoring
- HTTP transport with DNS resolution
- Dashboard UI for DNS metrics
"""

from __future__ import annotations

__version__ = "1.0.0"

from mexc_monitor.network.dns_resolver import (
    CustomDNSResolver,
    DNSCache,
    DNSMetrics,
    DNSResolution,
)

__all__ = [
    "CustomDNSResolver",
    "DNSCache",
    "DNSMetrics",
    "DNSResolution",
]