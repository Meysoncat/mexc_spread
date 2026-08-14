"""Routes: spread (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/spread/symbols')
def spread_tracked_symbols() -> dict:
    """Список символов с данными в spread buffer."""
    symbols = bm.sb_get_tracked_symbols()
    return {'ok': True, 'symbols': symbols, 'count': len(symbols)}


@bm.app.get('/api/rest-trades/status')
def rest_trades_status() -> dict:
    """REST trades poller status + trade_buffer coverage."""
    return {'ok': True, 'status': bm._rest_trades_poller.status()}


@bm.app.get('/api/trades/symbols')
def trades_tracked_symbols() -> dict:
    """Символы с данными сделок в trade_buffer (плотность/имбаланс/VWAP)."""
    from mexc_monitor import trade_buffer as tb
    symbols = tb.get_tracked_symbols()
    return {'ok': True, 'symbols': symbols, 'count': len(symbols)}


@bm.app.get('/api/trades/stats')
def trades_stats(symbol: str=Query(..., min_length=2, max_length=40), period_sec: float=Query(60.0, ge=1.0, le=600.0)) -> dict:
    """Агрегаты сделок за период: count, buy/sell split, объёмы, VWAP, имбаланс."""
    from mexc_monitor import trade_buffer as tb
    st = tb.get_stats(symbol.strip(), period_sec=period_sec)
    if st is None:
        return {'ok': True, 'symbol': symbol.strip().upper(), 'stats': None}
    return {'ok': True, 'symbol': st.symbol, 'stats': {'period_sec': st.period_sec, 'count': st.count, 'buy_count': st.buy_count, 'sell_count': st.sell_count, 'volume_base': st.volume_base, 'volume_quote': st.volume_quote, 'buy_volume_quote': st.buy_volume_quote, 'sell_volume_quote': st.sell_volume_quote, 'vwap': st.vwap, 'buy_sell_ratio': st.buy_sell_ratio, 'trades_per_min': st.trades_per_min, 'latest_ms': st.latest_ms}}


@bm.app.get('/api/spread/history')
def spread_history(symbol: str=Query(..., min_length=2, max_length=40), last_n: int | None=Query(None, ge=1, le=10000), since_ms: int | None=Query(None, description='Unix ms нижняя граница'), max_points: int=Query(1000, ge=10, le=5000)) -> dict:
    """История спреда из in-memory ring buffer."""
    ticks = bm.sb_get_history(symbol.strip(), last_n=last_n, since_ms=since_ms, max_points=max_points)
    return {'ok': True, 'symbol': symbol.strip().upper(), 'count': len(ticks), 'ticks': [bm._tick_to_dict(t) for t in ticks]}


@bm.app.get('/api/spread/latest')
def spread_latest(symbol: str=Query(..., min_length=2, max_length=40)) -> dict:
    """Последний тик спреда."""
    tick = bm.sb_get_latest(symbol.strip())
    if tick is None:
        return {'ok': True, 'symbol': symbol.strip().upper(), 'tick': None}
    return {'ok': True, 'symbol': symbol.strip().upper(), 'tick': bm._tick_to_dict(tick)}


@bm.app.get('/api/spread/stats')
def spread_stats(symbol: str=Query(..., min_length=2, max_length=40), period_sec: float=Query(300.0, ge=10, le=3600), threshold_bps: float | None=Query(None, ge=0)) -> dict:
    """Статистика спреда за период."""
    stats = bm.sb_get_stats(symbol.strip(), period_sec=period_sec, threshold_bps=threshold_bps)
    if stats is None:
        return {'ok': True, 'symbol': symbol.strip().upper(), 'stats': None}
    return {'ok': True, 'symbol': symbol.strip().upper(), 'stats': {'period_sec': stats.period_sec, 'ticks_count': stats.ticks_count, 'avg_spread_bps': stats.avg_spread_bps, 'min_spread_bps': stats.min_spread_bps, 'max_spread_bps': stats.max_spread_bps, 'std_spread_bps': stats.std_spread_bps, 'current_spread_bps': stats.current_spread_bps, 'current_bid': stats.current_bid, 'current_ask': stats.current_ask, 'current_mid': stats.current_mid, 'pct_above_threshold': stats.pct_above_threshold}}


@bm.app.get('/api/spread/stream')
async def spread_stream(request: Request, symbol: str=Query(..., min_length=2, max_length=40)) -> StreamingResponse:
    """
    SSE (Server-Sent Events) поток обновлений спреда в реальном времени.
    Клиент подключается и получает с��бытия при каждом изменении bid/ask.
    """
    import queue
    sym = symbol.strip().upper()
    q: queue.Queue[bm.SpreadTick | None] = queue.Queue(maxsize=500)

    def on_tick(_symbol: str, tick: bm.SpreadTick) -> None:
        try:
            q.put_nowait(tick)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(tick)
            except queue.Full:
                pass
    bm.sb_subscribe(sym, on_tick)

    async def event_generator():
        last_event = bm.time.monotonic()
        try:
            latest = bm.sb_get_latest(sym)
            if latest:
                data = bm.json.dumps(bm._tick_to_dict(latest))
                yield f'data: {data}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    tick = q.get_nowait()
                except queue.Empty:
                    if bm.time.monotonic() - last_event > 15.0:
                        last_event = bm.time.monotonic()
                        yield ': keepalive\n\n'
                    await bm.asyncio.sleep(0.05)
                    continue
                if tick is None:
                    break
                last_event = bm.time.monotonic()
                data = bm.json.dumps(bm._tick_to_dict(tick))
                yield f'data: {data}\n\n'
        finally:
            bm.sb_unsubscribe(sym, on_tick)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})
