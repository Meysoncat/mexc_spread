"""MetaScalp HTTP REST client.

Discovers the running MetaScalp instance by scanning ports 17845–17855,
then proxies requests to the local API.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

import httpx

from .models import (
    MetaScalpBalance,
    MetaScalpClusterSnapshot,
    MetaScalpConnection,
    MetaScalpOrder,
    MetaScalpOrderbookSnapshot,
    MetaScalpPosition,
    MetaScalpSignalLevel,
)

logger = logging.getLogger(__name__)

METASCALP_PORTS = range(17845, 17856)
DEFAULT_TIMEOUT = 5.0
# How long to remember a *failed* discovery before retrying the port scan.
# Without this, every call to connections()/positions()/orders() re-scans
# all 11 ports (2s timeout each → up to 22s per call) whenever MetaScalp
# isn't running — which made /api/portfolio-risk/status hang for minutes
# via the portfolio risk manager's per-engine status polling. A success is
# cached indefinitely (until the next request fails on that base_url).
DISCOVERY_NEGATIVE_TTL_SEC = 60.0

# Discovery only ever talks to loopback, where a closed port is refused
# immediately. A full HTTP request per port cost up to 2s each (22s for the
# whole scan) whenever the port was filtered rather than refused, so we probe
# the TCP port first with a short timeout and only speak HTTP to open ports.
DISCOVERY_PROBE_TIMEOUT_SEC = 0.25


def loopback_port_is_open(port: int) -> bool:
    """Cheap TCP probe: True if something is listening on 127.0.0.1:port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(DISCOVERY_PROBE_TIMEOUT_SEC)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


class MetaScalpClient:
    """HTTP client for MetaScalp local API."""

    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self._base_url = base_url
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        # Discovery cache. We remember the resolved base_url (or None if the
        # last scan failed) and when we last checked, so we don't re-scan
        # METASCALP_PORTS on every request when MetaScalp isn't running.
        # `_resolved_base` is None both before the first scan and after a
        # failed scan; `_resolved_at` tells those two states apart.
        self._resolved_base: str | None = None
        self._resolved_at: float = 0.0
        self._has_resolved: bool = False
        self._resolve_lock = threading.Lock()

    def _resolve_base_url(self) -> str | None:
        """Scan MetaScalp ports and return the first responding one.

        Results are cached: a successful discovery is remembered indefinitely,
        a failed discovery is remembered for DISCOVERY_NEGATIVE_TTL_SEC so we
        don't burn 22 seconds re-scanning on every call when MetaScalp is down.
        """
        # Fast path under the lock-free read: if we have a recent enough
        # answer, return it without scanning.
        now = time.monotonic()
        if self._has_resolved:
            cached = self._resolved_base
            # Success cache: keep forever (next failed request invalidates it).
            if cached is not None:
                return cached
            # Negative cache: only valid for DISCOVERY_NEGATIVE_TTL_SEC.
            if now - self._resolved_at < DISCOVERY_NEGATIVE_TTL_SEC:
                return None

        # Serialize scans so concurrent callers don't each walk all 11 ports.
        with self._resolve_lock:
            # Re-check inside the lock — another thread may have just scanned.
            now = time.monotonic()
            if self._has_resolved:
                cached = self._resolved_base
                if cached is not None:
                    return cached
                if now - self._resolved_at < DISCOVERY_NEGATIVE_TTL_SEC:
                    return None

            if self._base_url is not None:
                # Caller pinned a base_url at construction — trust it forever.
                self._resolved_base = self._base_url
                self._resolved_at = now
                self._has_resolved = True
                return self._base_url

            for port in METASCALP_PORTS:
                if not loopback_port_is_open(port):
                    continue
                url = f"http://127.0.0.1:{port}"
                try:
                    r = self._client.get(f"{url}/ping", timeout=2.0)
                    if r.status_code == 200:
                        self._resolved_base = url
                        self._resolved_at = now
                        self._has_resolved = True
                        logger.info("MetaScalp found at %s", url)
                        return url
                except Exception:
                    continue
            # Cache the negative result so we don't re-scan immediately.
            self._resolved_base = None
            self._resolved_at = now
            self._has_resolved = True
            logger.warning(
                "MetaScalp not found on ports %s; retrying in %.0fs",
                list(METASCALP_PORTS), DISCOVERY_NEGATIVE_TTL_SEC,
            )
            return None

    def _invalidate_base_url(self) -> None:
        """Drop the cached base_url — call when a request to it fails.

        A previously-healthy MetaScalp instance that has since gone down
        should be re-discovered, not stuck pointing at a dead URL forever.
        We keep _has_resolved=True so the next call re-scans immediately
        (no negative-TTL wait).
        """
        with self._resolve_lock:
            self._resolved_base = None
            self._resolved_at = 0.0
            self._has_resolved = False

    def _get(self, path: str) -> dict[str, Any]:
        base = self._resolve_base_url()
        if not base:
            return {"ok": False, "error": "MetaScalp not running"}
        try:
            r = self._client.get(f"{base}{path}")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            # Server responded with an HTTP error — it's alive, don't invalidate.
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            # Transport error (connection refused, timeout) — MetaScalp likely
            # went down since discovery. Drop the cached base_url so the next
            # call re-scans instead of pointing at a dead URL forever.
            self._invalidate_base_url()
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        base = self._resolve_base_url()
        if not base:
            return {"ok": False, "error": "MetaScalp not running"}
        try:
            r = self._client.post(f"{base}{path}", json=json)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            self._invalidate_base_url()
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _delete(self, path: str) -> dict[str, Any]:
        base = self._resolve_base_url()
        if not base:
            return {"ok": False, "error": "MetaScalp not running"}
        try:
            r = self._client.delete(f"{base}{path}")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            self._invalidate_base_url()
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── Discovery ──────────────────────────────────────────────────────────

    def ping(self) -> dict[str, Any]:
        """Find MetaScalp and return version info."""
        base = self._resolve_base_url()
        if not base:
            return {"ok": False, "error": "MetaScalp not running"}
        return {"ok": True, "base_url": base}

    def connections(self) -> list[MetaScalpConnection]:
        """List active exchange connections."""
        data = self._get("/api/connections")
        if not data.get("ok"):
            return []
        rows = data.get("connections", data)  # fallback if root is array
        if isinstance(rows, list):
            return [MetaScalpConnection(
                id=str(r.get("Id", r.get("id", ""))),
                name=r.get("Name", r.get("name", "")),
                exchange=r.get("Exchange", r.get("exchange", "")),
                status=r.get("Status", r.get("status", "")),
            ) for r in rows]
        return []

    # ── Market data ────────────────────────────────────────────────────────

    def orderbook_snapshot(self, conn_id: str, ticker: str) -> MetaScalpOrderbookSnapshot | None:
        """Fetch fresh order book snapshot."""
        data = self._get(f"/api/connections/{conn_id}/orderbook-snapshot?Ticker={ticker}")
        if not data.get("ok"):
            return None
        raw = data.get("data", data)
        asks = [
            MetaScalpOrderbookLevel(price=a.get("Price", 0), size=a.get("Size", 0), type="Ask")
            for a in raw.get("Asks", raw.get("asks", []))
        ]
        bids = [
            MetaScalpOrderbookLevel(price=b.get("Price", 0), size=b.get("Size", 0), type="Bid")
            for b in raw.get("Bids", raw.get("bids", []))
        ]
        return MetaScalpOrderbookSnapshot(
            ticker=ticker,
            asks=asks,
            bids=bids,
            best_ask=raw.get("BestAsk", 0),
            best_bid=raw.get("BestBid", 0),
        )

    def cluster_snapshot(self, conn_id: str, ticker: str) -> MetaScalpClusterSnapshot | None:
        """Fetch cluster (volume profile) snapshot."""
        data = self._get(f"/api/connections/{conn_id}/cluster-snapshot?Ticker={ticker}")
        if not data.get("ok"):
            return None
        raw = data.get("data", data)
        return MetaScalpClusterSnapshot(
            ticker=ticker,
            rows=raw.get("Rows", raw.get("rows", [])),
        )

    # ── Account data ───────────────────────────────────────────────────────

    def balance(self, conn_id: str) -> list[MetaScalpBalance]:
        data = self._get(f"/api/connections/{conn_id}/balance")
        if not data.get("ok"):
            return []
        raw = data.get("data", data)
        balances = raw.get("Balances", raw.get("balances", []))
        return [
            MetaScalpBalance(
                coin=b.get("Coin", b.get("coin", "")),
                total=float(b.get("Total", b.get("total", 0))),
                free=float(b.get("Free", b.get("free", 0))),
                locked=float(b.get("Locked", b.get("locked", 0))),
            )
            for b in balances
        ]

    def orders(self, conn_id: str, ticker: str | None = None) -> list[MetaScalpOrder]:
        path = f"/api/connections/{conn_id}/orders"
        if ticker:
            path += f"?Ticker={ticker}"
        data = self._get(path)
        if not data.get("ok"):
            return []
        raw = data.get("data", data)
        orders = raw.get("Orders", raw.get("orders", []))
        return [
            MetaScalpOrder(
                order_id=str(o.get("OrderId", o.get("orderId", ""))),
                ticker=o.get("Ticker", o.get("ticker", "")),
                side=o.get("Side", o.get("side", "")),
                type=o.get("Type", o.get("type", "")),
                price=float(o.get("Price", o.get("price", 0))),
                filled_price=float(o.get("FilledPrice", o.get("filledPrice", 0))),
                size=float(o.get("Size", o.get("size", 0))),
                filled_size=float(o.get("FilledSize", o.get("filledSize", 0))),
                fee=float(o.get("Fee", o.get("fee", 0))),
                fee_currency=o.get("FeeCurrency", o.get("feeCurrency", "")),
                status=o.get("Status", o.get("status", "")),
                time=o.get("Time", o.get("time", "")),
            )
            for o in orders
        ]

    def positions(self, conn_id: str) -> list[MetaScalpPosition]:
        data = self._get(f"/api/connections/{conn_id}/positions")
        if not data.get("ok"):
            return []
        raw = data.get("data", data)
        positions = raw.get("Positions", raw.get("positions", []))
        return [
            MetaScalpPosition(
                position_id=str(p.get("PositionId", p.get("positionId", ""))),
                ticker=p.get("Ticker", p.get("ticker", "")),
                side=p.get("Side", p.get("side", "")),
                size=float(p.get("Size", p.get("size", 0))),
                avg_price=float(p.get("AvgPrice", p.get("avgPrice", 0))),
                avg_price_fix=float(p.get("AvgPriceFix", p.get("avgPriceFix", 0))),
                avg_price_dyn=float(p.get("AvgPriceDyn", p.get("avgPriceDyn", 0))),
                status=p.get("Status", p.get("status", "")),
            )
            for p in positions
        ]

    # ── Trading ────────────────────────────────────────────────────────────

    def place_order(
        self,
        conn_id: str,
        ticker: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Ticker": ticker,
            "Side": side,
            "Type": order_type,
            "Size": size,
        }
        if price is not None:
            payload["Price"] = price
        return self._post(f"/api/connections/{conn_id}/orders", json=payload)

    def cancel_order(self, conn_id: str, order_id: str) -> dict[str, Any]:
        return self._post(f"/api/connections/{conn_id}/orders/cancel", json={"OrderId": order_id})

    def cancel_all_orders(self, conn_id: str, ticker: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if ticker:
            payload["Ticker"] = ticker
        return self._post(f"/api/connections/{conn_id}/orders/cancel-all", json=payload)

    # ── Signal levels ──────────────────────────────────────────────────────

    def signal_levels(self, conn_id: str, ticker: str) -> list[MetaScalpSignalLevel]:
        data = self._get(f"/api/connections/{conn_id}/signal-levels?Ticker={ticker}")
        if not data.get("ok"):
            return []
        raw = data.get("data", data)
        levels = raw.get("SignalLevels", raw.get("signalLevels", []))
        return [
            MetaScalpSignalLevel(
                id=str(sl.get("Id", sl.get("id", ""))),
                connection_id=str(sl.get("ConnectionId", sl.get("connectionId", conn_id))),
                ticker=sl.get("Ticker", sl.get("ticker", ticker)),
                price=float(sl.get("Price", sl.get("price", 0))),
                is_triggered=bool(sl.get("IsTriggered", sl.get("isTriggered", False))),
                trigger_time=sl.get("TriggerTime", sl.get("triggerTime", "")),
                trigger_rule=sl.get("TriggerRule", sl.get("triggerRule", "")),
            )
            for sl in levels
        ]

    def place_signal_level(self, conn_id: str, ticker: str, price: float, rule: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"Ticker": ticker, "Price": price}
        if rule:
            payload["TriggerRule"] = rule
        return self._post(f"/api/connections/{conn_id}/signal-levels", json=payload)

    def remove_signal_level(self, conn_id: str, level_id: str) -> dict[str, Any]:
        return self._delete(f"/api/connections/{conn_id}/signal-levels/{level_id}")

    def remove_all_signal_levels(self, conn_id: str, ticker: str) -> dict[str, Any]:
        return self._delete(f"/api/connections/{conn_id}/signal-levels?Ticker={ticker}")

    def remove_triggered_signal_levels(self) -> dict[str, Any]:
        return self._delete("/api/signal-levels/triggered")
