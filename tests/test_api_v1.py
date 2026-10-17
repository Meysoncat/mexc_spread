"""API v1: REST, SSE, WebSocket, ключ доступа, скринер — без сети (статичный хаб)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_setups import StaticHub, pq, sq, view_of  # noqa: E402

from backend import api_v1  # noqa: E402
from backend.screener import Broadcaster, build_runtime, matches, parse_filters, set_runtime  # noqa: E402
from mexc_monitor.setups.config import SetupsConfig  # noqa: E402


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.delenv("SCREENER_API_KEY", raising=False)
    cfg = SetupsConfig()
    cfg.history_db = str(tmp_path / "setups.sqlite")
    hub = StaticHub(
        view_of(
            pq("mexc", "BTC", 99.99, 100.0, vol=1e9),
            pq("binance", "BTC", 100.30, 100.31, vol=2e9, funding=0.0001),
            pq("bybit", "ETH", 3000.0, 3000.3, funding=0.001, qty=5),
            pq("hyperliquid", "ETH", 2999.9, 3000.2, funding=-0.00002, interval=1.0, qty=5),
            spots=[sq("bybit", "ETH", 2999.0, 2999.5)],
        )
    )
    runtime = build_runtime(cfg, hub)
    runtime.store.start()
    now = time.time()
    runtime.engine.tick(now - 2)
    runtime.engine.tick(now)
    set_runtime(runtime)
    yield runtime
    set_runtime(None)
    runtime.store.stop()


@pytest.fixture()
def client(rt):
    from backend.main import app

    return TestClient(app)  # без контекст-менеджера: startup (сеть) не запускается


def test_list_setups_and_filters(client):
    r = client.get("/api/v1/setups")
    assert r.status_code == 200
    body = r.json()
    types = {s["type"] for s in body["setups"]}
    assert {"cross_perp", "funding_arb"} <= types
    assert body["schema"] == "setup/v1" and body["seq"] >= body["count"]
    only = client.get("/api/v1/setups", params={"type": "funding_arb", "base": "eth"}).json()
    assert only["count"] == 1 and only["setups"][0]["legs"][0]["venue"] == "hyperliquid"
    assert client.get("/api/v1/setups", params={"venue": "okx"}).json()["count"] == 0
    scores = [s["score"] for s in body["setups"]]
    assert scores == sorted(scores, reverse=True)


def test_get_setup_by_id_with_special_chars(client):
    sid = "cross_perp:BTC:mexc>binance"
    r = client.get(f"/api/v1/setups/{sid}")
    assert r.status_code == 200 and r.json()["id"] == sid
    assert client.get("/api/v1/setups/nope:X").status_code == 404


def test_schema_health_screener_market(client):
    schema = client.get("/api/v1/schema/setup").json()
    assert schema["setup"]["$id"] == "setup/v1" and "funding_arb" in schema["setup"]["properties"]["type"]["enum"]
    h = client.get("/api/v1/health").json()
    assert h["engine"]["ticks"] == 2 and not h["engine"]["last_error"]
    assert h["venues_live"] == []  # у статичного хаба нет реальных источников
    rows = client.get("/api/v1/screener").json()["rows"]
    btc = next(r for r in rows if r["base"] == "BTC")
    assert btc["best_bid"]["venue"] == "binance" and btc["cross_spread_bps"] > 25 and btc["active_setups"] >= 1
    eth = next(r for r in rows if r["base"] == "ETH")
    assert eth["funding_spread_apr_pct"] > 100 and eth["max_basis_bps"] is not None
    m = client.get("/api/v1/market/eth").json()
    assert {p["venue"] for p in m["perps"]} == {"bybit", "hyperliquid"} and m["spots"][0]["venue"] == "bybit"
    assert client.get("/api/v1/market/NOPE").status_code == 404


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("SCREENER_API_KEY", "s3cret")
    assert client.get("/api/v1/setups").status_code == 401
    assert client.get("/api/v1/setups", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/setups", params={"api_key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/setups", params={"api_key": "wrong"}).status_code == 401


def test_history_and_stats(client, rt):
    rt.engine.hub._view = view_of()
    rt.engine.tick(time.time() + 10)
    rt.store.stop()
    rt.store._thread.join(timeout=5)
    hist = client.get("/api/v1/setups/history").json()
    assert hist["count"] >= 1 and hist["events"][0]["event"] == "closed"
    stats = client.get("/api/v1/setups/stats").json()
    assert stats["since_start"]["cross_perp"]["closed"] >= 1
    rt.store.start()


def _read_sse(chunks, n_events):
    events, buf = [], ""
    for chunk in chunks:
        buf += chunk if isinstance(chunk, str) else chunk.decode()
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            if block.startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
            events.append((fields.get("event"), fields.get("id"), json.loads(fields["data"])))
            if len(events) >= n_events:
                return events
    return events


def _run_stream(rt, **params):
    """Вызывает генератор SSE напрямую (TestClient буферизует бесконечный поток)."""
    from starlette.requests import Request

    headers = [(b"last-event-id", params.pop("last_event_id").encode())] if "last_event_id" in params else []

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b""}
    req = Request(scope, receive)

    async def go(n, emit=None):
        resp = await api_v1.setups_stream(req, **{"since": None, "type": None, "category": None, "base": None, "venue": None, "direction": None, "min_score": None, **params})
        out = []
        it = resp.body_iterator
        first = await it.__anext__()
        out.append(first)
        if emit:
            emit()
        while len(_read_sse(out, n)) < n:
            out.append(await asyncio.wait_for(it.__anext__(), 5))
        await it.aclose()
        return _read_sse(out, n)

    return go


def test_sse_snapshot_then_live_events(rt):
    go = _run_stream(rt)

    def emit():
        rt.engine.hub._view = view_of()
        rt.engine.tick(time.time() + 10)

    events = asyncio.run(go(2, emit))
    assert events[0][0] == "snapshot" and len(events[0][2]["setups"]) >= 2
    assert events[1][0] == "closed" and int(events[1][1]) == events[1][2]["seq"]


def test_sse_resume_with_last_event_id(rt):
    seq = rt.registry.seq
    go = _run_stream(rt, last_event_id=str(seq - 1))
    events = asyncio.run(go(1))
    assert events[0][0] == "opened" and events[0][2]["seq"] == seq


def test_websocket_snapshot_and_ping(client):
    with client.websocket_connect("/api/v1/setups/ws?type=cross_perp") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and all(s["type"] == "cross_perp" for s in snap["setups"])
        ws.send_text('{"op":"ping"}')
        assert ws.receive_json() == {"type": "pong"}


def test_websocket_rejects_bad_key(client, monkeypatch):
    monkeypatch.setenv("SCREENER_API_KEY", "k")
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/setups/ws") as ws:
            ws.receive_json()


def test_filters_always_pass_closed_events():
    f = parse_filters(min_score=90)
    setup = {"type": "cross_perp", "category": "arbitrage", "base": "BTC", "legs": [{"venue": "mexc"}], "status": "closed", "score": 10}
    assert matches(setup, f)
    assert not matches({**setup, "status": "active"}, f)


def test_broadcaster_overflow_marks_subscriber():
    async def go():
        b = Broadcaster(queue_size=2)
        sub = b.add({})
        ev = {"seq": 1, "event": "opened", "setup": {"type": "cross_perp", "category": "arbitrage", "base": "BTC", "legs": [], "status": "active"}}
        for _ in range(4):
            b.publish(ev)
        await asyncio.sleep(0.01)
        return sub

    sub = asyncio.run(go())
    assert sub.overflowed
