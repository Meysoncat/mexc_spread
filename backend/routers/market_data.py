"""Routes: market_data (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/klines')
def klines(market: str=Query('spot', description='spot или futures'), symbol: str=Query(..., min_length=3, max_length=40, description='Тикер как в снимке (BTCUSDT или BTC_USDT)'), interval: str=Query('1h', description='Интервал: 5m, 15m, 1h, 4h, 1d'), limit: int | None=Query(None, ge=1, le=1000, description='Макс. число свечей (мини-графики; на споте по умолчанию 500 без параметра)')) -> dict:
    m = market if market in ('spot', 'futures') else 'spot'
    try:
        candles = bm.fetch_klines_for_market(m, symbol, interval=interval, limit=limit)
    except bm.MexcApiError as e:
        return {'ok': False, 'error': str(e), 'market': m, 'symbol': symbol.strip(), 'interval': interval, 'candles': []}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'market': m, 'symbol': symbol.strip(), 'interval': interval, 'candles': []}
    return {'ok': True, 'market': m, 'symbol': symbol.strip(), 'interval': interval, 'count': len(candles), 'candles': candles}


@bm.app.get('/api/klines/batch')
def klines_batch(market: str=Query('spot', description='spot или futures'), symbols: str=Query(..., description='Символы через запятую (макс. 50)'), interval: str=Query('1h', description='Интервал: 5m, 15m, 1h, 4h, 1d'), limit: int=Query(96, ge=1, le=500, description='Макс. свечей на символ'), exchange: str=Query('mexc', description='mexc, asterdex или lighter')) -> dict:
    """
    Batch-загрузка klines для не��кольких символов одним запросом.
    Использует in-memory кэш (TTL 60s по умолчанию) и параллельные запросы.
    Поддерживает все биржи: mexc, asterdex, lighter.
    """
    ex = (exchange or '').strip().lower()
    if ex not in bm._SUPPORTED_EXCHANGES:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={'ok': False, 'error': f'Unknown exchange: {exchange}', 'supported': bm._SUPPORTED_EXCHANGES})
    m = market if market in ('spot', 'futures') else 'spot'
    sym_list = [s.strip() for s in symbols.split(',') if s.strip()]
    sym_list = sym_list[:50]
    if not sym_list:
        return {'ok': True, 'market': m, 'interval': interval, 'results': {}, 'count': 0}
    lim = max(1, min(limit, 500))
    results: dict[str, list[dict]] = {}
    uncached_symbols: list[str] = []
    now = bm.time.monotonic()
    for sym in sym_list:
        key = bm._klines_cache_key(f'{ex}:{m}', sym, interval, lim)
        if bm._KLINES_CACHE_TTL_SEC > 0:
            with bm._klines_cache_lock:
                cached = bm._klines_cache.get(key)
                if cached is not None and cached[0] > now:
                    results[sym] = cached[1]
                    continue
        uncached_symbols.append(sym)
    if uncached_symbols:

        def _fetch_one(sym: str) -> tuple[str, list[dict]]:
            return (sym, bm._fetch_klines_for_exchange(ex, m, sym, interval, lim))
        with bm._cf.ThreadPoolExecutor(max_workers=min(8, len(uncached_symbols))) as executor:
            future_map = {executor.submit(_fetch_one, sym): sym for sym in uncached_symbols}
            for future in bm._cf.as_completed(future_map):
                sym = future_map[future]
                try:
                    _, candles = future.result()
                    results[sym] = candles
                    if bm._KLINES_CACHE_TTL_SEC > 0 and candles:
                        key = bm._klines_cache_key(f'{ex}:{m}', sym, interval, lim)
                        with bm._klines_cache_lock:
                            bm._klines_cache[key] = (bm.time.monotonic() + bm._KLINES_CACHE_TTL_SEC, candles)
                except Exception:
                    results[sym] = []
    return {'ok': True, 'market': m, 'exchange': ex, 'interval': interval, 'count': len(results), 'results': results}


@bm.app.get('/api/depth')
def orderbook_depth(market: str=Query('spot', description='spot или futures'), symbol: str=Query(..., min_length=3, max_length=40), limit: int=Query(100, ge=5, le=1000), nocache: bool=Query(False, description='Пропустить кратковременный кэш стакана на сервере')) -> dict:
    m = market if market in ('spot', 'futures') else 'spot'
    sym = symbol.strip()
    lim = int(limit)
    key = (m, sym.upper(), lim)
    now = bm.time.monotonic()
    if not nocache and bm._DEPTH_CACHE_TTL_SEC > 0:
        with bm._depth_cache_lock:
            hit = bm._depth_cache.get(key)
            if hit is not None:
                exp, cached = hit
                if exp > now:
                    out = dict(cached)
                    out['cache_hit'] = True
                    return out
    if m == 'futures':
        ws_book = bm.get_fresh_futures_depth_book(sym, max_age_sec=8.0)
        if ws_book and ws_book.get('bids') and ws_book.get('asks'):
            out = dict(ws_book)
            out['ok'] = True
            out['cache_hit'] = False
            if bm._DEPTH_CACHE_TTL_SEC > 0:
                with bm._depth_cache_lock:
                    bm._depth_cache[key] = (now + bm._DEPTH_CACHE_TTL_SEC, dict(out))
            return out
    try:
        data = bm.fetch_orderbook_depth(m, sym, limit=lim)
    except (bm.MexcApiError, Exception) as e:
        err_str = str(e)
        if '403' in err_str or 'Forbidden' in err_str or isinstance(e, bm.MexcApiError):
            try:
                data = bm._fetch_binance_depth(m, sym, limit=lim)
            except Exception as e2:
                return {'ok': False, 'error': f'MEXC: {err_str}. Binance: {e2}', 'market': m, 'symbol': sym, 'limit': lim, 'bids': [], 'asks': [], 'cache_hit': False}
        else:
            bm.logger.exception('depth market=%s symbol=%s', m, sym)
            return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'market': m, 'symbol': sym, 'limit': lim, 'bids': [], 'asks': [], 'cache_hit': False}
    except Exception as e:
        bm.logger.exception('depth market=%s symbol=%s', m, sym)
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'market': m, 'symbol': sym, 'limit': lim, 'bids': [], 'asks': [], 'cache_hit': False}
    out: dict = {'ok': True, **data, 'cache_hit': False}
    if out.get('ok') and out.get('bids') and out.get('asks'):
        from mexc_monitor.vwap import compute_depth_summary
        ref_notional = float(bm.DEFAULT_SETTINGS.exec_reference_quote_notional)
        vwap_summary = compute_depth_summary(out['bids'], out['asks'], reference_notional=ref_notional)
        out['vwap'] = {'vwap_buy_price': vwap_summary['vwap_buy_price'], 'vwap_sell_price': vwap_summary['vwap_sell_price'], 'slippage_buy_bps': vwap_summary['slippage_buy_bps'], 'slippage_sell_bps': vwap_summary['slippage_sell_bps'], 'executable_buy_notional': vwap_summary['executable_buy_notional'], 'executable_sell_notional': vwap_summary['executable_sell_notional'], 'depth_levels': vwap_summary['depth_levels']}
    if bm._DEPTH_CACHE_TTL_SEC > 0 and out.get('ok'):
        with bm._depth_cache_lock:
            bm._depth_cache[key] = (now + bm._DEPTH_CACHE_TTL_SEC, dict(out))
    return out
