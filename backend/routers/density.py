"""Routes: density (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/density/walls')
def density_walls(symbol: str=Query('BTCUSDT', description='Символ'), market: str=Query('spot', description='spot или futures'), multiplier: float=Query(5.0, ge=1.5, le=50.0, description='Порог в разах от медианы'), min_notional: float=Query(10000, ge=0, description='Мин. нотация в USDT')) -> dict:
    """Поиск стен в стакане — уровни с аномально крупными ордерами."""
    from mexc_monitor.density import detect_walls, wall_to_dict
    try:
        data = bm._get_depth_snapshot(market, symbol, limit=100)
    except Exception as e:
        return {'ok': False, 'error': str(e), 'walls': []}
    walls = detect_walls(data.get('bids', []), data.get('asks', []), multiplier=multiplier, min_notional_usdt=min_notional)
    return {'ok': True, 'symbol': symbol.strip().upper(), 'market': market, 'multiplier': multiplier, 'walls': [wall_to_dict(w) for w in walls], 'count': len(walls), 'source': data.get('source')}


@bm.app.get('/api/density/stats')
def density_stats(symbol: str=Query('BTCUSDT', description='Символ'), market: str=Query('spot', description='spot или futures')) -> dict:
    """Статистика плотности стакана."""
    from mexc_monitor.density import compute_density_stats, stats_to_dict
    try:
        data = bm._get_depth_snapshot(market, symbol, limit=100)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    stats = compute_density_stats(data.get('bids', []), data.get('asks', []))
    return {'ok': True, 'symbol': symbol.strip().upper(), 'market': market, 'source': data.get('source'), **stats_to_dict(stats)}


@bm.app.get('/api/density/compare')
def density_compare(symbol: str=Query('BTCUSDT', description='Символ'), exchanges: str=Query('mexc,binance,bybit', description='Через запятую')) -> dict:
    """Сравнение ликвидности стакана между биржами."""
    import concurrent.futures
    from mexc_monitor.density import compute_density_stats, stats_to_dict
    ex_list = [e.strip().lower() for e in exchanges.split(',') if e.strip()]
    if not ex_list:
        return {'ok': False, 'error': 'No exchanges', 'results': {}}

    def _fetch_one(ex: str) -> tuple[str, dict]:
        try:
            if ex == 'mexc':
                data = bm.fetch_orderbook_depth('spot', symbol, limit=50)
            else:
                data = bm._fetch_binance_depth('spot', symbol, limit=50)
            stats = compute_density_stats(data.get('bids', []), data.get('asks', []))
            return (ex, {'ok': True, **stats_to_dict(stats)})
        except Exception as e:
            return (ex, {'ok': False, 'error': str(e)})
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ex_list)) as pool:
        futures = {pool.submit(_fetch_one, ex): ex for ex in ex_list}
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                ex, payload = future.result(timeout=12)
                results[ex] = payload
            except Exception as e:
                ex = futures[future]
                results[ex] = {'ok': False, 'error': str(e)}
    return {'ok': True, 'symbol': symbol.strip().upper(), 'results': results, 'exchanges': ex_list}


@bm.app.get('/api/density/history')
def density_history(symbol: str=Query('BTCUSDT', description='Символ'), since_ms: int | None=Query(None, description='Unix ms нижняя граница'), max_points: int=Query(500, ge=10, le=5000)) -> dict:
    """История плотности стакана из in-memory буфера."""
    from mexc_monitor.density_buffer import get_history, snapshot_to_dict
    snapshots = get_history(symbol.strip().upper(), since_ms=since_ms, max_points=max_points)
    return {'ok': True, 'symbol': symbol.strip().upper(), 'count': len(snapshots), 'snapshots': [snapshot_to_dict(s) for s in snapshots]}


@bm.app.get('/api/density/changes')
def density_changes(symbol: str=Query('BTCUSDT', description='Символ'), since_ms: int | None=Query(None, description='Unix ms нижняя граница'), limit: int=Query(100, ge=1, le=1000)) -> dict:
    """История изменений стен (появление/исчезновение/рост/уменьшение)."""
    from mexc_monitor.density_buffer import get_wall_changes, wall_change_to_dict
    changes = get_wall_changes(symbol.strip().upper(), since_ms=since_ms, limit=limit)
    return {'ok': True, 'symbol': symbol.strip().upper(), 'count': len(changes), 'changes': [wall_change_to_dict(c) for c in changes]}


@bm.app.get('/api/density/overview')
def density_overview(exchange: str=Query('mexc', description='Биржа'), market: str=Query('futures', description='spot или futures'), limit: int=Query(50, ge=5, le=200, description='Количество символов'), min_volume: float=Query(0, description='Мин. объём 24h (USDT)')) -> dict:
    """Обзор плотности по топ символам — таблица для Density Monitor."""
    import concurrent.futures
    from mexc_monitor.density import detect_walls, compute_density_stats, wall_to_dict, stats_to_dict
    if exchange == 'mexc':
        raw = bm._get_snapshot_payload(f'mexc:{market}', bypass_cache=False, builder=lambda: bm._build_snapshot_payload(market))
    else:
        raw = bm._get_snapshot_payload(f'{exchange}:{market}', bypass_cache=False, builder=lambda: bm._build_exchange_snapshot_payload(exchange, market), ttl=bm._snapshot_ttl_for(exchange))
    if not raw.get('ok') or not raw.get('rows'):
        err = raw.get('error', 'No data')
        if exchange == 'bybit' and ('403' in str(err) or 'CloudFront' in str(err)):
            err = 'Bybit REST геоблокирован (CloudFront 403): список символов и объёмы недоступны. WS-книга работает — настройте прокси (см. docs/PROXY_XRAY.md или страницу «Сеть / Прокси»).'
        return {'ok': False, 'error': err, 'symbols': []}
    rows = raw['rows']
    if min_volume > 0:
        rows = [r for r in rows if (r.get('volume_24h_quote') or 0) >= min_volume]
    rows = rows[:limit]
    _row_symbols = [r.get('symbol', '') for r in rows]
    if exchange == 'mexc' and market in ('futures', 'perp'):
        try:
            bm.touch_futures_depth_book_watchlist(_row_symbols)
            bm.reconcile_futures_depth_book_ws(bm.DEFAULT_SETTINGS)
        except Exception:
            pass
    elif exchange in ('okx', 'bybit') and market in ('futures', 'perp'):
        try:
            bm.touch_l2_depth_watchlist(exchange, _row_symbols)
            bm.reconcile_l2_depth(exchange)
        except Exception:
            pass

    def _fetch_density(row: dict) -> dict:
        sym = row.get('symbol', '')
        try:
            depth = None
            if market in ('futures', 'perp'):
                if exchange == 'mexc':
                    depth = bm.get_fresh_futures_depth_book(sym, max_age_sec=8.0)
                elif exchange in ('okx', 'bybit'):
                    depth = bm.get_fresh_l2_depth_book(exchange, sym, max_age_sec=8.0)
            if not depth:
                depth = bm._fetch_binance_depth(market, sym, limit=50)
            bids = depth.get('bids', [])
            asks = depth.get('asks', [])
            stats = compute_density_stats(bids, asks)
            walls = detect_walls(bids, asks, multiplier=5, min_notional_usdt=50000)
            wall_dicts = [wall_to_dict(w) for w in walls]
            return {'symbol': sym, 'mid': row.get('mid', 0), 'spread_bps': row.get('spread_bps'), 'volume_24h_quote': row.get('volume_24h_quote', 0), 'density': stats_to_dict(stats), 'source': depth.get('source'), 'walls': wall_dicts[:5], 'wall_count': len(wall_dicts), 'largest_wall': wall_dicts[0] if wall_dicts else None}
        except Exception:
            return {'symbol': sym, 'mid': row.get('mid', 0), 'spread_bps': row.get('spread_bps'), 'volume_24h_quote': row.get('volume_24h_quote', 0), 'density': None, 'walls': [], 'wall_count': 0, 'largest_wall': None}
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_density, r): r for r in rows}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            try:
                results.append(future.result(timeout=25))
            except Exception:
                pass
    results.sort(key=lambda r: (r.get('largest_wall') or {}).get('notional_usdt', 0), reverse=True)
    return {'ok': True, 'exchange': exchange, 'market': market, 'count': len(results), 'symbols': results}


@bm.app.get('/api/density/heatmap')
def density_heatmap(symbol: str=Query('BTCUSDT', description='Символ'), market: str=Query('spot', description='spot или futures'), levels: int=Query(50, ge=10, le=200, description='Количество уровней с каждой стороны')) -> dict:
    """Данные стакана для тепловой карты плотности.

    Возвращает ценовые уровни с нотацией — фронтенд накапливает
    снимки во времени и рендерит heatmap (X=время, Y=цена, цвет=нотация).
    """
    try:
        data = bm._get_depth_snapshot(market, symbol, limit=levels)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    bids_raw = data.get('bids', [])
    asks_raw = data.get('asks', [])

    def _parse(levels_raw):
        out = []
        for lv in levels_raw:
            if isinstance(lv, (list, tuple)) and len(lv) >= 2:
                try:
                    p, q = (float(lv[0]), float(lv[1]))
                    if p > 0 and q >= 0:
                        out.append({'price': p, 'qty': q, 'notional': round(p * q, 2)})
                except (TypeError, ValueError):
                    continue
            elif isinstance(lv, dict):
                try:
                    p = float(lv.get('price', 0))
                    q = float(lv.get('qty', 0))
                    if p > 0 and q >= 0:
                        out.append({'price': p, 'qty': q, 'notional': round(p * q, 2)})
                except (TypeError, ValueError):
                    continue
        return out
    bids = _parse(bids_raw)
    asks = _parse(asks_raw)
    best_bid = bids[0]['price'] if bids else 0
    best_ask = asks[0]['price'] if asks else 0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0
    return {'ok': True, 'symbol': symbol.strip().upper(), 'market': market, 'timestamp_ms': int(bm.time.time() * 1000), 'mid': mid, 'best_bid': best_bid, 'best_ask': best_ask, 'bids': bids, 'asks': asks, 'source': data.get('source')}


@bm.app.get('/api/density/watcher/status')
def density_watcher_status() -> dict:
    """Статус DensityWatcher."""
    return {'ok': True, 'running': bm._density_watcher._running, 'symbols': bm._density_watcher._symbols, 'poll_interval_sec': bm._density_watcher._poll_interval}


@bm.app.post('/api/density/watcher/start')
def density_watcher_start(symbols: str=Query('BTCUSDT,ETHUSDT', description='Символы через запятую'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Запустить DensityWatcher с указанными символами."""
    sym_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    bm._density_watcher.update_symbols(sym_list)
    bm._density_watcher.start()
    return {'ok': True, 'symbols': sym_list, 'message': 'DensityWatcher started'}


@bm.app.post('/api/density/watcher/stop')
def density_watcher_stop(_: None=Depends(bm._require_admin_token)) -> dict:
    """Остановить DensityWatcher."""
    bm._density_watcher.stop()
    return {'ok': True, 'message': 'DensityWatcher stopped'}
