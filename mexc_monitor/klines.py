from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import quote

import httpx

from mexc_monitor.client import MexcApiError
from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.http_utils import RequestPacer, get_with_retry

MarketKline = Literal["spot", "futures"]
UiInterval = Literal["5m", "15m", "1h", "4h", "1d"]

_UI_TO_SPOT: dict[str, str] = {
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "4h": "4h",
    "1d": "1d",
}

_UI_TO_FUTURES: dict[str, str] = {
    "5m": "Min5",
    "15m": "Min15",
    "1h": "Min60",
    "4h": "Hour4",
    "1d": "Day1",
}

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9_]{3,40}$")


def normalize_ui_interval(raw: str) -> UiInterval:
    k = raw.strip().lower()
    if k in ("60m", "h1"):
        k = "1h"
    if k in _UI_TO_SPOT:
        return k  # type: ignore[return-value]
    return "1h"


def validate_symbol(symbol: str) -> str:
    s = symbol.strip()
    if not _SYMBOL_RE.match(s):
        raise MexcApiError("Недопустимый символ")
    return s


def _dedupe_sorted_candles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(key=lambda r: r["time"])
    out: list[dict[str, Any]] = []
    for r in rows:
        if out and out[-1]["time"] == r["time"]:
            out[-1] = r
        else:
            out.append(r)
    return out


def fetch_spot_klines(
    symbol: str,
    *,
    interval: UiInterval = "1h",
    limit: int = 500,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    cfg = settings or DEFAULT_SETTINGS
    sym = validate_symbol(symbol).upper()
    spot_iv = _UI_TO_SPOT.get(interval, "60m")
    lim = max(1, min(limit, 1000))
    url = cfg.spot_klines_url
    params = {"symbol": sym, "interval": spot_iv, "limit": lim}
    p = RequestPacer(cfg.http_min_request_interval_sec)

    def _parse(r: httpx.Response) -> list[dict[str, Any]]:
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from spot klines") from e
        if not isinstance(data, list):
            raise MexcApiError(f"Unexpected spot klines type: {type(data)}")
        candles: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                t_ms = int(row[0])
                candles.append(
                    {
                        "time": t_ms // 1000,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
            except (TypeError, ValueError):
                continue
        return _dedupe_sorted_candles(candles)

    if client is not None:
        return _parse(get_with_retry(client, cfg, url, pacer=p, params=params))

    with httpx.Client(timeout=cfg.timeout_sec) as c:
        return _parse(get_with_retry(c, cfg, url, pacer=p, params=params))


def fetch_futures_klines(
    symbol: str,
    *,
    interval: UiInterval = "1h",
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    cfg = settings or DEFAULT_SETTINGS
    sym = validate_symbol(symbol)
    if "_" not in sym:
        sym = sym.upper()
    else:
        parts = sym.split("_")
        sym = "_".join(p.upper() for p in parts if p)
    fut_iv = _UI_TO_FUTURES.get(interval, "Min60")
    path_sym = quote(sym, safe="")
    url = cfg.futures_kline_url(path_sym)
    params = {"interval": fut_iv}
    p = RequestPacer(cfg.http_min_request_interval_sec)

    def _parse(r: httpx.Response) -> list[dict[str, Any]]:
        r.raise_for_status()
        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from futures klines") from e
        if not isinstance(payload, dict):
            raise MexcApiError(f"Unexpected futures klines root: {type(payload)}")
        if not payload.get("success"):
            code = payload.get("code")
            raise MexcApiError(f"futures klines success=false code={code}")
        block = payload.get("data")
        if not isinstance(block, dict):
            raise MexcApiError("futures klines missing data object")
        times = block.get("time") or []
        opens = block.get("open") or []
        highs = block.get("high") or []
        lows = block.get("low") or []
        closes = block.get("close") or []
        vols = block.get("vol") or []
        if not isinstance(times, list) or not times:
            return []
        n = len(times)
        candles: list[dict[str, Any]] = []
        for i in range(n):
            try:
                candles.append(
                    {
                        "time": int(times[i]),
                        "open": float(opens[i] if i < len(opens) else 0),
                        "high": float(highs[i] if i < len(highs) else 0),
                        "low": float(lows[i] if i < len(lows) else 0),
                        "close": float(closes[i] if i < len(closes) else 0),
                        "volume": float(vols[i] if i < len(vols) else 0),
                    }
                )
            except (TypeError, ValueError, IndexError):
                continue
        return _dedupe_sorted_candles(candles)

    if client is not None:
        return _parse(get_with_retry(client, cfg, url, pacer=p, params=params))

    with httpx.Client(timeout=cfg.timeout_sec) as c:
        return _parse(get_with_retry(c, cfg, url, pacer=p, params=params))


def fetch_klines_for_market(
    market: MarketKline,
    symbol: str,
    *,
    interval: str = "1h",
    limit: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    ui = normalize_ui_interval(interval)
    spot_limit = limit if limit is not None else 500
    spot_limit = max(1, min(spot_limit, 1000))
    if market == "futures":
        rows = fetch_futures_klines(symbol, interval=ui, settings=settings, client=client)
        if limit is not None and len(rows) > limit:
            return rows[-limit:]
        return rows
    return fetch_spot_klines(
        symbol,
        interval=ui,
        limit=spot_limit,
        settings=settings,
        client=client,
    )
