"""Routes: snapshot (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/snapshot')
def snapshot(request: Request, market: str=Query('spot', description='spot, futures или cross'), exchange: str=Query('mexc', description='Биржа: mexc, binance, bybit, okx, gateio, htx, bitget, asterdex, lighter, dydx, hyperliquid'), nocache: bool=Query(False, description='Пропустить серверный кэш снимка (принудительно сходить на биржу)')) -> Response:
    ex = (exchange or '').strip().lower()
    if ex not in bm._SUPPORTED_EXCHANGES:
        return JSONResponse(status_code=400, content={'ok': False, 'error': f'Unknown exchange: {exchange}', 'supported': bm._SUPPORTED_EXCHANGES})
    if ex == 'mexc':
        raw = (market or '').strip().lower()
        m = raw if raw in ('spot', 'futures', 'cross') else 'spot'
        if m != raw:
            bm.logger.warning('snapshot unknown market=%r normalized to %s', market, m)
        cache_key = f'mexc:{m}'
        bm._mark_snapshot_hot(cache_key, lambda: bm._build_snapshot_payload(m), bm._snapshot_ttl_for('mexc'))
        out = bm._get_snapshot_payload(cache_key, bypass_cache=nocache, builder=lambda: bm._build_snapshot_payload(m))
    else:
        raw_market = (market or '').strip().lower()
        if ex in bm._MULTI_MARKET_EXCHANGES:
            m = raw_market if raw_market in ('spot', 'futures') else None
        else:
            m = None
        cache_key = f"{ex}:{m or 'default'}"
        bm._mark_snapshot_hot(cache_key, lambda: bm._build_exchange_snapshot_payload(ex, m), bm._snapshot_ttl_for(ex))
        out = bm._get_snapshot_payload(cache_key, bypass_cache=nocache, builder=lambda: bm._build_exchange_snapshot_payload(ex, m), ttl=bm._snapshot_ttl_for(ex))
    if not out.get('ok'):
        bm.logger.warning('snapshot response not ok exchange=%s error=%s', ex, out.get('error'))
        return JSONResponse(content=out)
    etag = 'W/"{}"'.format(bm.hashlib.md5(f"{ex}:{out.get('market')}:{out.get('loaded_at')}:{out.get('count')}".encode()).hexdigest())
    if_none_match = request.headers.get('if-none-match', '')
    if etag in if_none_match:
        return Response(status_code=304, headers={'ETag': etag, 'Cache-Control': 'no-cache'})
    return JSONResponse(content=out, headers={'ETag': etag, 'Cache-Control': 'no-cache'})


@bm.app.get('/api/snapshot/multi')
def snapshot_multi(exchanges: str=Query('mexc,binance,bybit,okx,gateio,bitget', description='Через запятую'), market: str=Query('futures', description='spot или futures')) -> dict:
    """Параллельная загрузка снимков с нескольких бирж."""
    import concurrent.futures
    ex_list = [e.strip().lower() for e in exchanges.split(',') if e.strip()]
    ex_list = [e for e in ex_list if e in bm._SUPPORTED_EXCHANGES]
    if not ex_list:
        return {'ok': False, 'error': 'No valid exchanges', 'results': {}}

    def _fetch_one(ex: str) -> tuple[str, dict]:
        if ex == 'mexc':
            m = market if market in ('spot', 'futures') else 'futures'
            return (ex, bm._get_snapshot_payload(f'mexc:{m}', bypass_cache=False, builder=lambda: bm._build_snapshot_payload(m)))
        m = market if market in ('spot', 'futures') else None
        return (ex, bm._get_snapshot_payload(f"{ex}:{m or 'default'}", bypass_cache=False, builder=lambda: bm._build_exchange_snapshot_payload(ex, m), ttl=bm._snapshot_ttl_for(ex)))
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ex_list)) as pool:
        futures = {pool.submit(_fetch_one, ex): ex for ex in ex_list}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            try:
                ex, payload = future.result(timeout=25)
                results[ex] = payload
            except Exception as e:
                ex = futures[future]
                results[ex] = {'ok': False, 'error': str(e), 'rows': [], 'count': 0}
    return {'ok': True, 'results': results, 'exchanges': ex_list}


@bm.app.get('/api/snapshot/stream')
async def snapshot_stream(request: Request, market: str=Query('spot', description='spot, futures или cross'), exchange: str=Query('mexc'), interval_sec: float=Query(2.0, ge=0.5, le=60.0)) -> Response:
    """SSE-поток снимков: новое событие только когда снимок обновился.

    Заменяет поллинг с фронта: бэкенд сам проверяет кэш (который греется
    WS-фидами и префетчем) и пушит payload при смене loaded_at.

    Генератор асинхронный: ожидание — через ``asyncio.sleep``, а блокирующая
    сборка снимка уходит в threadpool. Синхронный вариант держал бы поток из
    пула anyio (по умолчанию 40) на всё время жизни соединения, и десяток
    открытых вкладок вешал бы весь API.
    """
    ex = (exchange or '').strip().lower()
    if ex not in bm._SUPPORTED_EXCHANGES:
        return JSONResponse(status_code=400, content={'ok': False, 'error': f'Unknown exchange: {exchange}'})
    raw_market = (market or '').strip().lower()
    if ex == 'mexc':
        m: str | None = raw_market if raw_market in ('spot', 'futures', 'cross') else 'spot'
        cache_key = f'mexc:{m}'
        builder = lambda: bm._build_snapshot_payload(m)
    else:
        if ex in bm._MULTI_MARKET_EXCHANGES:
            m = raw_market if raw_market in ('spot', 'futures') else None
        else:
            m = None
        cache_key = f"{ex}:{m or 'default'}"
        builder = lambda: bm._build_exchange_snapshot_payload(ex, m)
    ttl = bm._snapshot_ttl_for(ex)

    def _next_payload() -> dict:
        bm._mark_snapshot_hot(cache_key, builder, ttl)
        try:
            return bm._get_snapshot_payload(cache_key, bypass_cache=False, builder=builder, ttl=ttl)
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    async def event_generator():
        last_sent = ''
        last_keepalive = bm.time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            out = await run_in_threadpool(_next_payload)
            marker = f"{out.get('loaded_at')}:{out.get('count')}:{out.get('ok')}"
            if marker != last_sent:
                last_sent = marker
                last_keepalive = bm.time.monotonic()
                yield f'data: {bm.json.dumps(out)}\n\n'
            elif bm.time.monotonic() - last_keepalive > 15.0:
                last_keepalive = bm.time.monotonic()
                yield ': keepalive\n\n'
            await bm.asyncio.sleep(interval_sec)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})


@bm.app.get('/api/history/recent')
def history_recent(market: str=Query('spot', description='spot или futures'), symbol: str | None=Query(None, description='Точный символ как в снимке'), since: str | None=Query(None, description='ISO8601 нижняя граница observed_at (включительно)'), limit: int=Query(500, ge=1, le=5000)) -> dict:
    m = market if market in ('spot', 'futures') else 'spot'
    path = bm.resolve_history_db_path(bm.DEFAULT_SETTINGS)
    if not path.is_file():
        return {'ok': True, 'market': m, 'rows': [], 'count': 0, 'db_path': str(path)}
    rows = bm.query_recent(path, market=m, symbol=symbol, since_iso=since, limit=limit)
    return {'ok': True, 'market': m, 'rows': rows, 'count': len(rows), 'db_path': str(path)}
