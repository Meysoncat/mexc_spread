from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import quote

import httpx

from mexc_monitor.client import MexcApiError
from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.http_utils import RequestPacer, get_with_retry
from mexc_monitor.klines import validate_symbol

MarketOb = Literal["spot", "futures"]

_SPOT_LIMITS = (5, 10, 20, 50, 100, 500, 1000, 5000)


def _clamp_spot_limit(n: int) -> int:
    if n <= 0:
        return 100
    for x in _SPOT_LIMITS:
        if n <= x:
            return x
    return _SPOT_LIMITS[-1]


def _clamp_futures_limit(n: int) -> int:
    return max(5, min(n, 1000))


def _level_spot(entry: Any) -> tuple[float, float] | None:
    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
        return None
    try:
        p = float(entry[0])
        q = float(entry[1])
    except (TypeError, ValueError):
        return None
    if p <= 0 or q < 0:
        return None
    return p, q


def _level_futures(entry: Any) -> tuple[float, float] | None:
    """[price, order_count, qty] — для объёма используем qty (контракты)."""
    if not isinstance(entry, (list, tuple)) or len(entry) < 3:
        return None
    try:
        p = float(entry[0])
        q = float(entry[2])
    except (TypeError, ValueError):
        return None
    if p <= 0 or q < 0:
        return None
    return p, q


def _normalize_side(levels: list[tuple[float, float]]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for price, qty in levels:
        out.append(
            {
                "price": price,
                "qty": qty,
                "notional": price * qty,
            },
        )
    return out


def _with_best_mid(payload: dict[str, Any]) -> dict[str, Any]:
    bids = payload.get("bids")
    asks = payload.get("asks")
    bb = bids[0]["price"] if isinstance(bids, list) and bids else None
    ba = asks[0]["price"] if isinstance(asks, list) and asks else None
    mid = None
    if bb is not None and ba is not None:
        mid = (float(bb) + float(ba)) / 2.0
    payload["best_bid"] = bb
    payload["best_ask"] = ba
    payload["mid"] = mid
    return payload


def fetch_spot_orderbook(
    symbol: str,
    *,
    limit: int = 100,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cfg = settings or DEFAULT_SETTINGS
    sym = validate_symbol(symbol).upper()
    lim = _clamp_spot_limit(limit)
    url = cfg.spot_depth_url
    params = {"symbol": sym, "limit": lim}
    p = RequestPacer(cfg.http_min_request_interval_sec)

    def _parse(r: httpx.Response) -> dict[str, Any]:
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from spot depth") from e
        if not isinstance(data, dict):
            raise MexcApiError(f"Unexpected spot depth type: {type(data)}")
        bids_raw = data.get("bids")
        asks_raw = data.get("asks")
        if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
            raise MexcApiError("spot depth missing bids/asks")
        bid_levels: list[tuple[float, float]] = []
        for x in bids_raw:
            lv = _level_spot(x)
            if lv is not None:
                bid_levels.append(lv)
        ask_levels: list[tuple[float, float]] = []
        for x in asks_raw:
            lv = _level_spot(x)
            if lv is not None:
                ask_levels.append(lv)
        last_id = data.get("lastUpdateId")
        return _with_best_mid(
            {
                "market": "spot",
                "symbol": sym,
                "limit": lim,
                "last_update_id": last_id,
                "version": None,
                "timestamp_ms": None,
                "bids": _normalize_side(bid_levels),
                "asks": _normalize_side(ask_levels),
            },
        )

    if client is not None:
        return _parse(get_with_retry(client, cfg, url, pacer=p, params=params))

    with httpx.Client(timeout=cfg.timeout_sec) as c:
        return _parse(get_with_retry(c, cfg, url, pacer=p, params=params))


def fetch_futures_orderbook(
    symbol: str,
    *,
    limit: int = 100,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cfg = settings or DEFAULT_SETTINGS
    sym = validate_symbol(symbol).upper()
    encoded = quote(sym, safe="")
    lim = _clamp_futures_limit(limit)
    url = f"{cfg.contract_depth_url(encoded)}?limit={lim}"
    p = RequestPacer(cfg.http_min_request_interval_sec)

    def _parse(r: httpx.Response) -> dict[str, Any]:
        r.raise_for_status()
        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from contract depth") from e
        if not isinstance(payload, dict):
            raise MexcApiError(f"Unexpected contract depth root: {type(payload)}")
        if not payload.get("success"):
            code = payload.get("code")
            raise MexcApiError(f"contract depth success=false code={code}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MexcApiError("contract depth missing data")
        bids_raw = data.get("bids")
        asks_raw = data.get("asks")
        if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
            raise MexcApiError("contract depth missing bids/asks")
        bid_levels: list[tuple[float, float]] = []
        for x in bids_raw:
            lv = _level_futures(x)
            if lv is not None:
                bid_levels.append(lv)
        ask_levels: list[tuple[float, float]] = []
        for x in asks_raw:
            lv = _level_futures(x)
            if lv is not None:
                ask_levels.append(lv)
        return _with_best_mid(
            {
                "market": "futures",
                "symbol": sym,
                "limit": lim,
                "last_update_id": None,
                "version": data.get("version"),
                "timestamp_ms": data.get("timestamp"),
                "bids": _normalize_side(bid_levels),
                "asks": _normalize_side(ask_levels),
            },
        )

    if client is not None:
        return _parse(get_with_retry(client, cfg, url, pacer=p))

    with httpx.Client(timeout=cfg.timeout_sec) as c:
        return _parse(get_with_retry(c, cfg, url, pacer=p))


def fetch_orderbook_depth(
    market: MarketOb,
    symbol: str,
    *,
    limit: int = 100,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if market == "spot":
        return fetch_spot_orderbook(symbol, limit=limit, settings=settings, client=client)
    return fetch_futures_orderbook(symbol, limit=limit, settings=settings, client=client)
