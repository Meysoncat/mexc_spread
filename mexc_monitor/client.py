from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

import httpx

from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.futures_rows import futures_ticker_item_to_row, parse_float
from mexc_monitor.http_utils import RequestPacer, get_with_retry, mexc_httpx_client
from mexc_monitor.metrics import compute_mid_spread
from mexc_monitor.models import BookTickerRow


class MexcApiError(RuntimeError):
    pass


def _normalize_book_ticker_item(item: dict[str, Any]) -> BookTickerRow | None:
    symbol = item.get("symbol")
    if not symbol or not isinstance(symbol, str):
        return None
    bid = parse_float(item.get("bidPrice"))
    ask = parse_float(item.get("askPrice"))
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None

    bid_qty = parse_float(item.get("bidQty")) or 0.0
    ask_qty = parse_float(item.get("askQty")) or 0.0
    mid, spread_abs, spread_bps = compute_mid_spread(bid, ask)
    return BookTickerRow(
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        mid=mid,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
        volume_24h_base=0.0,
        volume_24h_quote=0.0,
        funding_rate=None,
        observed_at=None,
    )


def fetch_all_book_tickers(
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
    pacer: RequestPacer | None = None,
) -> list[BookTickerRow]:
    """
    Public endpoint: all symbols if symbol param omitted.
    """
    cfg = settings or DEFAULT_SETTINGS
    url = cfg.book_ticker_url
    p = pacer if pacer is not None else RequestPacer(cfg.http_min_request_interval_sec)

    def _do_request(c: httpx.Client) -> list[BookTickerRow]:
        r = get_with_retry(c, cfg, url, pacer=p)
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from bookTicker") from e

        if isinstance(data, dict):
            rows = [_normalize_book_ticker_item(data)]
        elif isinstance(data, list):
            rows = [_normalize_book_ticker_item(x) for x in data if isinstance(x, dict)]
        else:
            raise MexcApiError(f"Unexpected bookTicker payload type: {type(data)}")

        return [row for row in rows if row is not None]

    if client is not None:
        return _do_request(client)

    with mexc_httpx_client(cfg) as c:
        return _do_request(c)


def fetch_24hr_volume_map(
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
    pacer: RequestPacer | None = None,
) -> dict[str, tuple[float, float]]:
    """
    GET /api/v3/ticker/24hr without symbol → all pairs.
    Weight(IP): 25 per MEXC docs — reuse the same httpx.Client as bookTicker when possible.
    """
    cfg = settings or DEFAULT_SETTINGS
    url = cfg.ticker_24hr_url
    p = pacer if pacer is not None else RequestPacer(cfg.http_min_request_interval_sec)

    def _do_parse(c: httpx.Client) -> dict[str, tuple[float, float]]:
        r = get_with_retry(c, cfg, url, pacer=p)
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from ticker/24hr") from e

        if isinstance(data, dict):
            items: list[dict[str, Any]] = [data]
        elif isinstance(data, list):
            items = [x for x in data if isinstance(x, dict)]
        else:
            raise MexcApiError(f"Unexpected ticker/24hr payload type: {type(data)}")

        out: dict[str, tuple[float, float]] = {}
        for it in items:
            sym = it.get("symbol")
            if not sym or not isinstance(sym, str):
                continue
            vb = parse_float(it.get("volume"))
            vq = parse_float(it.get("quoteVolume"))
            out[sym] = (vb or 0.0, vq or 0.0)
        return out

    if client is not None:
        return _do_parse(client)

    with mexc_httpx_client(cfg) as c:
        return _do_parse(c)


def fetch_merged_snapshot_rows(
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[BookTickerRow]:
    """bookTicker + 24hr in one session (two requests, one TCP connection if possible)."""
    cfg = settings or DEFAULT_SETTINGS

    from mexc_monitor.ws_spot_orderbook import apply_spot_depth_top_to_rows, ensure_spot_orderbook_ws_started

    ensure_spot_orderbook_ws_started(cfg)

    def _both(c: httpx.Client) -> list[BookTickerRow]:
        p = RequestPacer(cfg.http_min_request_interval_sec)
        books = fetch_all_book_tickers(cfg, client=c, pacer=p)
        vol_map = fetch_24hr_volume_map(cfg, client=c, pacer=p)
        merged: list[BookTickerRow] = []
        for row in books:
            vb, vq = vol_map.get(row.symbol, (0.0, 0.0))
            merged.append(
                replace(
                    row,
                    volume_24h_base=vb,
                    volume_24h_quote=vq,
                )
            )
        # Подмена L1 из WS bookTicker (если включено и есть свежие данные)
        merged = apply_spot_depth_top_to_rows(merged, cfg)
        return merged

    if client is not None:
        return _both(client)

    with mexc_httpx_client(cfg) as c:
        return _both(c)


def fetch_futures_snapshot_rows(
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[BookTickerRow]:
    """
    По умолчанию: GET /api/v1/contract/ticker.
    При futures_ticker_source=websocket — push all tickers с wss (см. MEXC), иначе REST fallback.
    """
    cfg = settings or DEFAULT_SETTINGS

    from mexc_monitor.ws_futures import (
        ensure_started_from_settings,
        futures_ws_handshake_403,
        start_futures_tickers_ws,
        try_get_cached_rows,
    )
    from mexc_monitor.ws_futures_orderbook import (
        apply_futures_depth_top_to_rows,
        ensure_futures_orderbook_ws_started,
    )

    ensure_started_from_settings(cfg)
    ensure_futures_orderbook_ws_started(cfg)
    if cfg.futures_ticker_source == "websocket":
        cached = try_get_cached_rows(max_age_sec=cfg.futures_ws_stale_after_sec)
        if cached is None:
            boot_until = time.monotonic() + float(cfg.futures_ws_bootstrap_wait_sec)
            while time.monotonic() < boot_until:
                cached = try_get_cached_rows(
                    max_age_sec=max(30.0, cfg.futures_ws_stale_after_sec * 3),
                )
                if cached is not None:
                    break
                time.sleep(0.35)
        if cached is not None:
            rows = apply_futures_depth_top_to_rows(cached, cfg)
            from mexc_monitor.futures_l1_qty import enrich_futures_bid_ask_qty_from_rest_depth

            if client is not None:
                return enrich_futures_bid_ask_qty_from_rest_depth(rows, cfg, client)
            with mexc_httpx_client(cfg) as c:
                return enrich_futures_bid_ask_qty_from_rest_depth(rows, cfg, c)
        tail = (
            " WebSocket handshake возвращает 403 (Akamai) — ваш IP не пускают на contract.mexc.com. "
            "Нужен VPN или другая сеть."
            if futures_ws_handshake_403(cfg.futures_ws_url)
            else ""
        )
        raise MexcApiError(
            f"Режим futures_ticker_source=websocket: за {cfg.futures_ws_bootstrap_wait_sec:.0f} с не пришёл "
            f"push.tickers.{tail} Параметр futures_ws_bootstrap_wait_sec можно увеличить (до 120).",
        )

    url = cfg.contract_ticker_url
    p = RequestPacer(cfg.http_min_request_interval_sec)

    def _do_request(c: httpx.Client) -> list[BookTickerRow]:
        r = get_with_retry(c, cfg, url, pacer=p)
        if r.status_code == 403:
            # У части сетей REST contract/ticker режут; WSS с браузерными заголовками иногда проходит.
            start_futures_tickers_ws(
                cfg.futures_ws_url,
                connect_timeout=max(15.0, float(cfg.timeout_sec)),
            )
            deadline = time.monotonic() + float(cfg.futures_ws_bootstrap_wait_sec)
            while time.monotonic() < deadline:
                cached = try_get_cached_rows(max_age_sec=120.0)
                if cached is not None:
                    rows = apply_futures_depth_top_to_rows(cached, cfg)
                    from mexc_monitor.futures_l1_qty import enrich_futures_bid_ask_qty_from_rest_depth

                    return enrich_futures_bid_ask_qty_from_rest_depth(rows, cfg, c)
                time.sleep(0.35)
            cdn = (
                " Сервер отвечает 403 и на WSS handshake (Akamai) — блокировка IP, а не ключи API. "
                "Смените сеть/VPN."
                if futures_ws_handshake_403(cfg.futures_ws_url)
                else ""
            )
            raise MexcApiError(
                "contract/ticker: HTTP 403 и за "
                f"{cfg.futures_ws_bootstrap_wait_sec:.0f} с не пришёл push.tickers по WebSocket.{cdn} "
                'В config/external_apis.json: "futures_ws_bootstrap_wait_sec" (10–120), '
                '"futures_ticker_source": "websocket" — только WSS без первого REST. '
                "Окружение: MEXC_FUTURES_WS_BOOTSTRAP_WAIT_SEC.",
            )
        r.raise_for_status()
        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise MexcApiError("Invalid JSON from contract/ticker") from e

        if not isinstance(payload, dict):
            raise MexcApiError(f"Unexpected contract/ticker root type: {type(payload)}")
        if not payload.get("success"):
            code = payload.get("code")
            raise MexcApiError(f"contract/ticker success=false code={code}")

        data = payload.get("data")
        if data is None:
            raise MexcApiError("contract/ticker missing data")
        if isinstance(data, dict):
            items: list[Any] = [data]
        elif isinstance(data, list):
            items = data
        else:
            raise MexcApiError(f"Unexpected contract/ticker data type: {type(data)}")

        rows = [futures_ticker_item_to_row(x) for x in items if isinstance(x, dict)]
        rows = [row for row in rows if row is not None]
        rows = apply_futures_depth_top_to_rows(rows, cfg)
        from mexc_monitor.futures_l1_qty import enrich_futures_bid_ask_qty_from_rest_depth

        return enrich_futures_bid_ask_qty_from_rest_depth(rows, cfg, c)

    if client is not None:
        return _do_request(client)

    with mexc_httpx_client(cfg) as c:
        return _do_request(c)
