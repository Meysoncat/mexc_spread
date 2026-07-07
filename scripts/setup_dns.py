#!/usr/bin/env python
"""CLI tool для настройки DNS.

Usage:
    python scripts/setup_dns.py --config network_dns_config.json
    python scripts/setup_dns.py --preset xbox_dns
    python scripts/setup_dns.py --test-all-exchanges
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from mexc_monitor.config import (
    Exchange,
    CustomDNSServer,
    DNSEndpointDOH,
    ExchangeDNSConfig,
    GlobalNetworkConfig,
    load_network_dns_config,
)

PRESETS = {
    "xbox_dns": {
        "global": {
            "use_custom_dns": True,
            "dns_type": "custom",
            "dns_fallback_enabled": True,
        },
        "exchanges": {
            "mexc": {
                "use_custom_dns": True,
                "dns_type": "custom",
                "custom_dns_servers": [
                    {"address": "185.212.113.7", "port": 53},
                    {"address": "185.212.113.9", "port": 53},
                ],
                "custom_dns_fallback": [
                    {"address": "94.140.14.15", "port": 53},
                    {"address": "94.140.14.16", "port": 53},
                ],
            },
        },
    },
    "doh_cloudflare": {
        "global": {
            "use_custom_dns": True,
            "dns_type": "doh",
            "dns_over_https_enabled": True,
        },
        "exchanges": {
            "mexc": {
                "use_custom_dns": True,
                "dns_type": "doh",
                "dns_over_https_enabled": True,
                "doh_endpoint": {
                    "url": "https://1.1.1.1/dns-query",
                    "timeout_sec": 5.0,
                },
                "custom_dns_fallback": [
                    {"address": "185.212.113.7", "port": 53},
                ],
            },
        },
    },
    "system_only": {
        "global": {
            "use_custom_dns": False,
            "dns_type": "system",
        },
        "exchanges": {
            "mexc": {
                "use_custom_dns": False,
                "dns_type": "system",
            },
        },
    },
}


def test_dns_server(server: str) -> bool:
    """Test DNS server connectivity."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"http://{server}/", follow_redirects=True)
            return response.status_code == 200
    except Exception as e:
        print(f"❌ DNS server {server} failed: {e}")
        return False


def setup_preset(name: str, output_path: str) -> bool:
    """Apply DNS preset to config file."""
    preset = PRESETS.get(name)
    if not preset:
        print(f"❌ Unknown preset: {name}")
        return False

    print(f"📝 Applying preset: {name}")

    global_cfg = preset.get("global", {})
    exchanges_cfg = preset.get("exchanges", {})

    servers_to_test = []

    if global_cfg.get("dns_type") == "custom":
        for ex_name, ex_cfg in exchanges_cfg.items():
            for server_data in ex_cfg.get("custom_dns_servers", []):
                server_str = f"{server_data['address']}:{server_data.get('port', 53)}"
                servers_to_test.append(server_str)
                print(f"  Testing {server_str}...")
    elif global_cfg.get("dns_over_https_enabled"):
        for ex_name, ex_cfg in exchanges_cfg.items():
            doh = ex_cfg.get("doh_endpoint")
            if doh:
                endpoint = doh.get("url")
                print(f"  Testing {endpoint}...")
                servers_to_test.append(endpoint)

    print("\n🧪 Running tests...")
    all_passed = True
    for server in servers_to_test:
        if not server.startswith("http"):
            success = test_dns_server(server)
        else:
            success = True  # DOH doesn't need HTTP test

        if success:
            print(f"  ✅ {server}")
        else:
            print(f"  ❌ {server}")
            all_passed = False

    if not all_passed:
        print("\n⚠️  Some DNS servers failed. Use --force to override.")
        return False

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(preset, f, indent=2)

    print(f"\n✅ Config saved to: {output_path}")
    return True


def test_all_exchanges(config_path: str) -> bool:
    """Test DNS resolution for all configured exchanges."""
    config = load_network_dns_config(config_path)

    print("🧪 Testing DNS resolution for all exchanges...")

    test_urls = {
        Exchange.MEXC: "https://api.mexc.com/api/v3/time",
        Exchange.ASTERODEX: "https://fapi.asterdex.com/fapi/v1/ping",
        Exchange.BINANCE: "https://api.binance.com/api/v3/ping",
        Exchange.BYBIT: "https://api.bybit.com/v5/market/time",
    }

    all_passed = True

    for exchange, url in test_urls.items():
        dns_config = config.get_exchange_config(exchange)
        print(f"\n  Testing {exchange.value}...")

        try:
            with httpx.Client(timeout=10.0) as client:
                if dns_config.use_custom_dns:
                    print(f"    Using custom DNS: {dns_config.dns_type}")
                    response = client.get(url)
                else:
                    print(f"    Using system DNS")
                    response = client.get(url)

                if response.status_code == 200:
                    print(f"    ✅ Success: {response.status_code}")
                else:
                    print(f"    ❌ Failed: {response.status_code}")
                    all_passed = False

        except Exception as e:
            print(f"    ❌ Error: {e}")
            all_passed = False

    return all_passed


def validate_config(config_path: str) -> bool:
    """Validate DNS configuration."""
    print("🔍 Validating DNS configuration...")

    config = load_network_dns_config(config_path)

    issues = []

    if not config.global_config.dns_cache_enabled:
        issues.append("Cache is disabled - consider enabling for performance")

    if config.global_config.dns_fallback_enabled:
        issues.append("DNS fallback is enabled - good for reliability")

    if config.global_config.monitoring_enabled:
        issues.append("Monitoring is enabled - good for observability")

    # Check exchanges
    custom_dns_count = sum(1 for c in config.exchange_configs.values() if c.use_custom_dns)
    if custom_dns_count > 0:
        issues.append(f"{custom_dns_count} exchanges use custom DNS")

    if issues:
        print("\n⚠️  Configuration issues:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("✅ Configuration looks good!")
        return True


def main():
    parser = argparse.ArgumentParser(description="Setup DNS for exchanges")
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        help="Apply preset (xbox_dns, doh_cloudflare, system_only)",
    )
    parser.add_argument(
        "--config",
        default="config/network_dns_config.json",
        help="Path to config file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip DNS tests",
    )
    parser.add_argument(
        "--test-all-exchanges",
        action="store_true",
        help="Test DNS resolution for all configured exchanges",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate DNS configuration",
    )

    args = parser.parse_args()

    if args.preset:
        setup_preset(args.preset, args.config)
    elif args.test_all_exchanges:
        success = test_all_exchanges(args.config)
        exit(0 if success else 1)
    elif args.validate:
        success = validate_config(args.config)
        exit(0 if success else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()