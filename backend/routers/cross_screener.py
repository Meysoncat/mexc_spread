"""Routes: cross-exchange screener (MEXC × AsterDEX basis opportunities)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/cross-screener/opportunities')
def cross_screener_opportunities(limit: int = Query(50, ge=1, le=500)) -> dict:
    """Current ranked cross-venue basis opportunities (default MEXC×Aster)."""
    opps = bm._cross_screener_engine.get_opportunities(limit=limit)
    status = bm._cross_screener_engine.get_status()
    return {
        'ok': True,
        'opportunities': opps,
        'opportunity_count': len(opps),
        'scanned_at': status['scanned_at'],
        'pairs': status['pairs'],
        'config': bm._cross_screener_engine.get_config(),
    }


@bm.app.get('/api/cross-screener/stream')
def cross_screener_stream(request: Request) -> StreamingResponse:
    """SSE: push ranked cross-venue opportunities on every scan."""
    import queue as _queue
    q = bm._cross_screener_engine.subscribe()

    async def event_generator():
        last_event = bm.time.monotonic()
        try:
            status = bm._cross_screener_engine.get_status()
            yield ('data: ' + bm.json.dumps({'scanned_at': status['scanned_at'], 'opportunity_count': status['opportunity_count'], 'opportunities': status['opportunities']}) + '\n\n')
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = q.get_nowait()
                except _queue.Empty:
                    if bm.time.monotonic() - last_event > 15.0:
                        last_event = bm.time.monotonic()
                        yield ': keepalive\n\n'
                    await bm.asyncio.sleep(0.25)
                    continue
                last_event = bm.time.monotonic()
                yield ('data: ' + bm.json.dumps(payload) + '\n\n')
        finally:
            bm._cross_screener_engine.unsubscribe(q)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})


@bm.app.get('/api/cross-screener/status')
def cross_screener_status() -> dict:
    """Engine status: pairs, reject reasons, last error."""
    return {'ok': True, **bm._cross_screener_engine.get_status()}


@bm.app.get('/api/cross-screener/history')
def cross_screener_history(limit: int = Query(100, ge=1, le=1000), symbol: str | None = Query(None), only_open: bool = Query(False)) -> dict:
    """Persistent log of cross-venue opportunities (enter/exit events)."""
    events = bm._cross_screener_engine.get_history(limit=limit, symbol=symbol, only_open=only_open)
    return {'ok': True, 'count': len(events), 'events': events}


@bm.app.get('/api/cross-screener/config')
def cross_screener_config_get() -> dict:
    """Current cross-screener thresholds and scorer weights."""
    return {'ok': True, 'config': bm._cross_screener_engine.get_config()}


@bm.app.patch('/api/cross-screener/config')
def cross_screener_config_update(patch: dict = Body(...), _: None = Depends(bm._require_admin_token)) -> dict:
    """Hot-update cross-screener thresholds/weights (admin only)."""
    try:
        new_cfg = bm._cross_screener_engine.update_config(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'ok': True, 'config': new_cfg}
