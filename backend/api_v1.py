"""API v1 для внешних систем: сетапы (REST, SSE, WebSocket) и данные скринера.

Контракт: docs/INTEGRATION.md и GET /api/v1/schema/setup. Если задан
SCREENER_API_KEY, каждый запрос должен нести его в заголовке ``X-API-Key``
или в параметре ``api_key`` (EventSource в браузере заголовки не умеет).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import statistics
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from backend.screener import ScreenerRuntime, get_runtime, matches, parse_filters
from backend.setup_schema import EVENT_SCHEMA, SETUP_SCHEMA

HEARTBEAT_SEC = 15.0


def _api_key() -> str:
    return os.environ.get("SCREENER_API_KEY", "").strip()


def _check_key(provided: str | None) -> bool:
    key = _api_key()
    return not key or (provided is not None and secrets.compare_digest(provided.strip(), key))


def require_key(request: Request) -> None:
    if not _check_key(request.headers.get("x-api-key") or request.query_params.get("api_key")):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


router = APIRouter(prefix="/api/v1", tags=["setups"], dependencies=[Depends(require_key)])


def runtime() -> ScreenerRuntime:
    rt = get_runtime()
    if rt is None:
        raise HTTPException(status_code=503, detail="screener is not running (SCREENER_ENABLED=0?)")
    return rt


def _sorted(setups: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "edge":
        return sorted(setups, key=lambda s: s.get("edge_bps") if s.get("edge_bps") is not None else float("-inf"), reverse=True)
    if sort == "opened":
        return sorted(setups, key=lambda s: s["opened_at"], reverse=True)
    return sorted(setups, key=lambda s: s.get("score") or 0.0, reverse=True)


# ─────────────────────────────────────────────────────────── setups


@router.get("/setups")
def list_setups(
    type: str | None = Query(None, description="Через запятую: cross_perp,funding_arb,..."),
    category: str | None = Query(None, description="arbitrage | directional"),
    base: str | None = Query(None, description="Через запятую: BTC,ETH"),
    venue: str | None = Query(None, description="Хотя бы одна нога на этой бирже"),
    direction: str | None = Query(None, description="long | short"),
    min_score: float | None = Query(None, ge=0, le=100),
    sort: str = Query("score", pattern="^(score|edge|opened)$"),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    rt = runtime()
    f = parse_filters(type=type, category=category, base=base, venue=venue, direction=direction, min_score=min_score)
    seq, setups = rt.registry.snapshot()
    setups = [s for s in setups if matches(s, f)]
    return {"schema": "setup/v1", "seq": seq, "count": len(setups), "setups": _sorted(setups, sort)[:limit]}


@router.get("/setups/history")
def setups_history(
    type: str | None = None,
    base: str | None = None,
    event: str = Query("closed", pattern="^(opened|updated|closed|all)$"),
    since: float | None = Query(None, description="Unix-время, сек"),
    limit: int = Query(200, ge=1, le=5000),
) -> dict[str, Any]:
    rt = runtime()
    if rt.store is None:
        raise HTTPException(status_code=503, detail="history store is unavailable")
    rows = rt.store.history(event=None if event == "all" else event, setup_type=type, base=base, since_epoch=since, limit=limit)
    return {"count": len(rows), "events": rows}


@router.get("/setups/stats")
def setups_stats(period_h: float = Query(24.0, gt=0, le=24 * 90)) -> dict[str, Any]:
    """Исходы идей по типам: в памяти с запуска и из журнала за период."""
    rt = runtime()
    out: dict[str, Any] = {"since_start": rt.registry.stats()}
    if rt.store is not None:
        out["period_h"] = period_h
        out["period"] = rt.store.outcome_stats(time.time() - period_h * 3600)
    return out


@router.get("/setups/stream")
async def setups_stream(
    request: Request,
    since: int | None = Query(None, description="Дочитать события после этого seq (как Last-Event-ID)"),
    type: str | None = None,
    category: str | None = None,
    base: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
) -> StreamingResponse:
    """SSE: ``snapshot`` (все активные) или дочитка по Last-Event-ID, дальше opened/updated/closed."""
    rt = runtime()
    f = parse_filters(type=type, category=category, base=base, venue=venue, direction=direction, min_score=min_score)
    last_id = request.headers.get("last-event-id")
    resume = int(last_id) if last_id and last_id.isdigit() else since

    async def gen():
        sub = rt.broadcaster.add(f)  # подписка до снимка — между ними ничего не теряется
        try:
            cursor, first = _initial(rt, f, resume)
            for name, data, seq in first:
                yield _sse(name, data, seq)
            while True:
                try:
                    ev = await asyncio.wait_for(sub.queue.get(), HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": ping\n\n"
                    continue
                if ev is None:
                    yield _sse("overflow", {"detail": "client too slow; reconnect with Last-Event-ID"}, None)
                    return
                if ev["seq"] <= cursor:
                    continue
                cursor = ev["seq"]
                yield _sse(ev["event"], ev, ev["seq"])
        finally:
            rt.broadcaster.remove(sub)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


def _initial(rt: ScreenerRuntime, f: dict[str, Any], resume: int | None) -> tuple[int, list[tuple[str, Any, int | None]]]:
    """Что отправить сразу: пропущенные события (если ещё в буфере) или снимок."""
    if resume is not None:
        events, complete = rt.registry.events_since(resume)
        if complete:
            seq = max([resume, *(e["seq"] for e in events)])
            return seq, [(e["event"], e, e["seq"]) for e in events if matches(e["setup"], f)]
    seq, setups = rt.registry.snapshot()
    setups = [s for s in setups if matches(s, f)]
    return seq, [("snapshot", {"seq": seq, "setups": setups}, seq)]


def _sse(event: str, data: Any, seq: int | None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"


@router.get("/setups/{setup_id:path}")
def get_setup(setup_id: str) -> dict[str, Any]:
    rt = runtime()
    s = rt.registry.get(setup_id)
    if s is None:
        raise HTTPException(status_code=404, detail="setup is not active")
    return s.to_dict()


# ─────────────────────────────────────────────────────────── schema / health


@router.get("/schema/setup")
def schema_setup() -> dict[str, Any]:
    return {"setup": SETUP_SCHEMA, "event": EVENT_SCHEMA}


@router.get("/health")
def health() -> dict[str, Any]:
    rt = get_runtime()
    if rt is None:
        return {"ok": False, "detail": "screener is not running"}
    st = rt.engine.status()
    fresh = st["last_tick_epoch"] is not None and time.time() - st["last_tick_epoch"] < 10
    hub = rt.hub.health_report()
    live = sorted(name for name, v in hub["venues"].items() if v["live"])
    return {
        "ok": fresh and not st["last_error"] and bool(live),
        "venues_live": live,
        "engine": st,
        "hub": hub,
        "subscribers": len(rt.broadcaster),
    }


# ─────────────────────────────────────────────────────────── screener


@router.get("/screener")
def screener(
    min_venues: int = Query(2, ge=1, le=20),
    min_volume_usd: float = Query(0.0, ge=0),
    sort: str = Query("cross_spread_bps", pattern="^(cross_spread_bps|funding_spread_apr_pct|volume_24h_usd|max_basis_bps|base)$"),
    limit: int = Query(300, ge=1, le=3000),
) -> dict[str, Any]:
    """Сводка по каждой монете через все биржи: лучший bid/ask, funding-разброс, базис, объёмы."""
    rt = runtime()
    view = rt.engine.view
    if view is None:
        return {"ts": None, "count": 0, "rows": []}
    by_base: dict[str, int] = {}
    for s in rt.registry.active():
        by_base[s.candidate.base] = by_base.get(s.candidate.base, 0) + 1
    rows = []
    for base, quotes in view.perps.items():
        qs = [q for q in quotes if q.age_sec <= 15]
        if len(qs) < min_venues:
            continue
        med = statistics.median(q.mid for q in qs)
        qs = [q for q in qs if med > 0 and abs(q.mid / med - 1) <= 0.2]
        if len(qs) < min_venues:
            continue
        volume = sum(q.volume_24h_usd or 0.0 for q in qs)
        if volume < min_volume_usd:
            continue
        bid = max(qs, key=lambda q: q.bid)
        ask = min(qs, key=lambda q: q.ask)
        mid = (bid.bid + ask.ask) / 2
        fund = [q for q in qs if q.funding_per_hour is not None]
        f_lo = min(fund, key=lambda q: q.funding_per_hour) if fund else None
        f_hi = max(fund, key=lambda q: q.funding_per_hour) if fund else None
        spots = [s for s in view.spots.get(base, []) if s.age_sec <= 15]
        max_basis = None
        if spots:
            spot_mid = statistics.median(s.mid for s in spots)
            if spot_mid > 0:
                max_basis = max((q.mid / spot_mid - 1) * 1e4 for q in qs)
        rows.append(
            {
                "base": base,
                "venues": len(qs),
                "best_bid": {"venue": bid.venue, "price": bid.bid},
                "best_ask": {"venue": ask.venue, "price": ask.ask},
                "cross_spread_bps": round((bid.bid - ask.ask) / mid * 1e4, 2) if mid > 0 and bid.venue != ask.venue else 0.0,
                "funding_low": {"venue": f_lo.venue, "apr_pct": round(f_lo.funding_per_hour * 8760 * 100, 2)} if f_lo else None,
                "funding_high": {"venue": f_hi.venue, "apr_pct": round(f_hi.funding_per_hour * 8760 * 100, 2)} if f_hi else None,
                "funding_spread_apr_pct": round((f_hi.funding_per_hour - f_lo.funding_per_hour) * 8760 * 100, 2) if f_lo and f_hi and f_lo is not f_hi else 0.0,
                "volume_24h_usd": round(volume, 0),
                "open_interest_usd": round(sum(q.open_interest_usd or 0.0 for q in qs), 0),
                "spot_venues": len(spots),
                "max_basis_bps": round(max_basis, 2) if max_basis is not None else None,
                "active_setups": by_base.get(base, 0),
            }
        )
    key = sort
    rows.sort(key=lambda r: r[key] if r[key] is not None else float("-inf"), reverse=key != "base")
    return {"ts": view.ts, "count": len(rows), "rows": rows[:limit]}


@router.get("/market/{base}")
def market_base(base: str) -> dict[str, Any]:
    """Все котировки монеты: perp по биржам (с funding/OI) и спот."""
    rt = runtime()
    view = rt.engine.view
    if view is None:
        raise HTTPException(status_code=503, detail="no market data yet")
    b = base.upper()
    perps = [
        {
            "venue": q.venue,
            "symbol": q.native,
            "bid": q.bid,
            "ask": q.ask,
            "bid_qty": q.bid_qty,
            "ask_qty": q.ask_qty,
            "spread_bps": round(q.spread_bps, 3),
            "age_sec": round(q.age_sec, 2),
            "indicative": q.indicative,
            "funding_rate": q.funding_rate,
            "funding_interval_h": q.funding_interval_h,
            "funding_apr_pct": round(q.funding_per_hour * 8760 * 100, 3) if q.funding_per_hour is not None else None,
            "next_funding_ms": q.next_funding_ms,
            "open_interest_usd": q.open_interest_usd,
            "volume_24h_usd": q.volume_24h_usd,
        }
        for q in sorted(view.perps.get(b, []), key=lambda q: q.venue)
    ]
    spots = [
        {"venue": s.venue, "symbol": s.native, "bid": s.bid, "ask": s.ask, "bid_qty": s.bid_qty, "ask_qty": s.ask_qty, "age_sec": round(s.age_sec, 2)}
        for s in sorted(view.spots.get(b, []), key=lambda s: s.venue)
    ]
    if not perps and not spots:
        raise HTTPException(status_code=404, detail=f"no quotes for {b}")
    return {"base": b, "ts": view.ts, "perps": perps, "spots": spots}


# ─────────────────────────────────────────────────────────── websocket


ws_router = APIRouter(prefix="/api/v1")


@ws_router.websocket("/setups/ws")
async def setups_ws(websocket: WebSocket) -> None:
    """WebSocket: {"type":"snapshot"} или события дочитки, затем {"type":"event", ...}.

    Параметры — как у /setups/stream (since, type, base, ...). Клиент может
    слать {"op":"ping"} — ответ {"type":"pong"}.
    """
    qp = websocket.query_params
    if not _check_key(websocket.headers.get("x-api-key") or qp.get("api_key")):
        await websocket.close(code=4401)
        return
    rt = get_runtime()
    if rt is None:
        await websocket.close(code=1013)
        return
    try:
        min_score = float(qp["min_score"]) if qp.get("min_score") else None
    except ValueError:
        min_score = None
    f = parse_filters(type=qp.get("type"), category=qp.get("category"), base=qp.get("base"), venue=qp.get("venue"), direction=qp.get("direction"), min_score=min_score)
    since = int(qp["since"]) if (qp.get("since") or "").isdigit() else None
    await websocket.accept()
    sub = rt.broadcaster.add(f)

    async def reader() -> None:
        while True:
            msg = await websocket.receive_text()
            try:
                op = json.loads(msg).get("op")
            except (ValueError, AttributeError):
                continue
            if op == "ping":
                await websocket.send_text('{"type":"pong"}')

    reader_task = asyncio.create_task(reader())
    try:
        cursor, first = _initial(rt, f, since)
        for name, data, _ in first:
            payload = {"type": "snapshot", **data} if name == "snapshot" else {"type": "event", **data}
            await websocket.send_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        while True:
            get = asyncio.ensure_future(sub.queue.get())
            done, _ = await asyncio.wait({get, reader_task}, timeout=HEARTBEAT_SEC, return_when=asyncio.FIRST_COMPLETED)
            if reader_task in done:
                get.cancel()
                return
            if get not in done:
                get.cancel()
                await websocket.send_text('{"type":"heartbeat"}')
                continue
            ev = get.result()
            if ev is None:
                await websocket.send_text('{"type":"overflow","detail":"client too slow; reconnect with since=<last seq>"}')
                await websocket.close(code=1013)
                return
            if ev["seq"] <= cursor:
                continue
            cursor = ev["seq"]
            await websocket.send_text(json.dumps({"type": "event", **ev}, separators=(",", ":"), ensure_ascii=False))
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        reader_task.cancel()
        rt.broadcaster.remove(sub)
