"""Routes: screener (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/screener/opportunities')
def screener_opportunities(limit: int=Query(50, ge=1, le=500, description='Max opportunities to return')) -> dict:
    """Current ranked tradeable-spread opportunities (MEXC spot)."""
    opps = bm._screener_engine.get_opportunities(limit=limit)
    status = bm._screener_engine.get_status()
    return {'ok': True, 'opportunities': opps, 'opportunity_count': len(opps), 'scanned_at': status['scanned_at'], 'total_universe': status['total_universe'], 'config': bm._screener_engine.get_config()}


@bm.app.get('/api/screener/stream')
def screener_stream(request: Request) -> StreamingResponse:
    """SSE: push the ranked opportunities on every scan.

    Очередь наполняется из потока сканера, поэтому читаем её неблокирующе:
    ``q.get(timeout=...)`` внутри async-генератора вешал бы event loop
    целиком (до 15 с на каждое соединение).
    """
    import queue as _queue
    q = bm._screener_engine.subscribe()

    async def event_generator():
        last_event = bm.time.monotonic()
        try:
            status = bm._screener_engine.get_status()
            yield ('data: ' + bm.json.dumps({'scanned_at': status['scanned_at'], 'total_universe': status['total_universe'], 'opportunity_count': len(status['opportunities']), 'opportunities': status['opportunities']}) + '\n\n')
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
            bm._screener_engine.unsubscribe(q)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})


@bm.app.get('/api/screener/history')
def screener_history(limit: int=Query(100, ge=1, le=1000), symbol: str | None=Query(None), only_open: bool=Query(False)) -> dict:
    """Persistent log of coins 'found' by the screener (enter events)."""
    events = bm._screener_engine.get_history(limit=limit, symbol=symbol, only_open=only_open)
    return {'ok': True, 'count': len(events), 'events': events}


@bm.app.get('/api/screener/config')
def screener_config_get() -> dict:
    """Current screener thresholds and scorer weights."""
    return {'ok': True, 'config': bm._screener_engine.get_config()}


@bm.app.patch('/api/screener/config')
def screener_config_update(patch: dict=Body(...), _: None=Depends(bm._require_admin_token)) -> dict:
    """Hot-update screener thresholds/weights (admin only)."""
    try:
        new_cfg = bm._screener_engine.update_config(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'ok': True, 'config': new_cfg}
