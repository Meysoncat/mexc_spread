"""Routes: metascalp (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/metascalp/ping')
def metascalp_ping() -> dict:
    """Проверка доступности MetaScalp."""
    return bm._metascalp_client.ping()


@bm.app.get('/api/metascalp/status')
def metascalp_status() -> dict:
    """Статус инфраструктуры MetaScalp (кэш, poller, bridge)."""
    return {'ok': True, 'poller': bm._metascalp_poller.status(), 'bridge_running': bm._metascalp_ws_bridge.is_running()}


@bm.app.get('/api/metascalp/connections')
def metascalp_connections() -> list[dict]:
    """Список активных подключений MetaScalp."""
    conns = bm._metascalp_client.connections()
    return [{'id': c.id, 'name': c.name, 'exchange': c.exchange, 'status': c.status} for c in conns]


@bm.app.get('/api/metascalp/connections/{conn_id}/balance')
def metascalp_balance(conn_id: str) -> dict:
    """Баланс подключения (с кэшем)."""
    cached = bm._metascalp_cache.get_balance(conn_id)
    if cached is not None:
        return {'ok': True, 'connection_id': conn_id, 'balances': cached, 'cached': True}
    balances = bm._metascalp_client.balance(conn_id)
    data = [b.__dict__ for b in balances]
    bm._metascalp_cache.set_balance(conn_id, data)
    return {'ok': True, 'connection_id': conn_id, 'balances': data, 'cached': False}


@bm.app.get('/api/metascalp/connections/{conn_id}/orders')
def metascalp_orders(conn_id: str, ticker: str | None=None) -> dict:
    """Активные ордера (с кэшем)."""
    cached = bm._metascalp_cache.get_orders(conn_id)
    if cached is not None:
        orders = cached
        if ticker:
            orders = [o for o in orders if o.get('ticker') == ticker]
        return {'ok': True, 'connection_id': conn_id, 'orders': orders, 'cached': True}
    orders = bm._metascalp_client.orders(conn_id, ticker)
    data = [o.__dict__ for o in orders]
    bm._metascalp_cache.set_orders(conn_id, data)
    return {'ok': True, 'connection_id': conn_id, 'orders': data, 'cached': False}


@bm.app.get('/api/metascalp/connections/{conn_id}/positions')
def metascalp_positions(conn_id: str) -> dict:
    """Открытые позиции (с кэшем)."""
    cached = bm._metascalp_cache.get_positions(conn_id)
    if cached is not None:
        return {'ok': True, 'connection_id': conn_id, 'positions': cached, 'cached': True}
    positions = bm._metascalp_client.positions(conn_id)
    data = [p.__dict__ for p in positions]
    bm._metascalp_cache.set_positions(conn_id, data)
    return {'ok': True, 'connection_id': conn_id, 'positions': data, 'cached': False}


@bm.app.get('/api/metascalp/connections/{conn_id}/orderbook')
def metascalp_orderbook(conn_id: str, ticker: str) -> dict:
    """Снапшот стакана (короткий TTL кэш)."""
    cached = bm._metascalp_cache.get_orderbook(conn_id, ticker)
    if cached is not None:
        return {'ok': True, **cached, 'cached': True}
    ob = bm._metascalp_client.orderbook_snapshot(conn_id, ticker)
    if ob is None:
        return {'ok': False, 'error': 'Failed to fetch orderbook'}
    data = {'ticker': ob.ticker, 'best_ask': ob.best_ask, 'best_bid': ob.best_bid, 'asks': [a.__dict__ for a in ob.asks], 'bids': [b.__dict__ for b in ob.bids]}
    bm._metascalp_cache.set_orderbook(conn_id, ticker, data)
    return {'ok': True, **data, 'cached': False}


@bm.app.get('/api/metascalp/connections/{conn_id}/cluster')
def metascalp_cluster(conn_id: str, ticker: str) -> dict:
    """Кластерный снапшот (volume profile)."""
    cached = bm._metascalp_cache.get_cluster(conn_id, ticker)
    if cached is not None:
        return {'ok': True, **cached, 'cached': True}
    cluster = bm._metascalp_client.cluster_snapshot(conn_id, ticker)
    if cluster is None:
        return {'ok': False, 'error': 'Failed to fetch cluster snapshot'}
    data = {'ticker': cluster.ticker, 'rows': cluster.rows}
    bm._metascalp_cache.set_cluster(conn_id, ticker, data)
    return {'ok': True, **data, 'cached': False}


@bm.app.get('/api/metascalp/connections/{conn_id}/signal-levels')
def metascalp_signal_levels(conn_id: str, ticker: str) -> dict:
    """Уровни сигналов (с кэшем)."""
    cached = bm._metascalp_cache.get_signal_levels(conn_id, ticker)
    if cached is not None:
        return {'ok': True, 'connection_id': conn_id, 'ticker': ticker, 'levels': cached, 'cached': True}
    levels = bm._metascalp_client.signal_levels(conn_id, ticker)
    data = [l.__dict__ for l in levels]
    bm._metascalp_cache.set_signal_levels(conn_id, ticker, data)
    return {'ok': True, 'connection_id': conn_id, 'ticker': ticker, 'levels': data, 'cached': False}


@bm.app.post('/api/metascalp/connections/{conn_id}/orders')
def metascalp_place_order(conn_id: str, payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Размещение ордера через MetaScalp."""
    ticker = str(payload.get('ticker', ''))
    side = str(payload.get('side', ''))
    order_type = str(payload.get('type', ''))
    size = float(payload.get('size', 0))
    price = payload.get('price')
    if not ticker or not side or (not order_type) or (size <= 0):
        return {'ok': False, 'error': 'ticker, side, type, size are required'}
    result = bm._metascalp_client.place_order(conn_id, ticker, side, order_type, size, float(price) if price is not None else None)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'orders')
    return result


@bm.app.post('/api/metascalp/connections/{conn_id}/orders/cancel')
def metascalp_cancel_order(conn_id: str, payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Отмена ордера."""
    order_id = str(payload.get('order_id', ''))
    if not order_id:
        return {'ok': False, 'error': 'order_id is required'}
    result = bm._metascalp_client.cancel_order(conn_id, order_id)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'orders')
    return result


@bm.app.post('/api/metascalp/connections/{conn_id}/orders/cancel-all')
def metascalp_cancel_all(conn_id: str, payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Отмена всех ордеров."""
    ticker = payload.get('ticker')
    result = bm._metascalp_client.cancel_all_orders(conn_id, ticker)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'orders')
    return result


@bm.app.post('/api/metascalp/connections/{conn_id}/signal-levels')
def metascalp_place_signal_level(conn_id: str, payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Установка уровня сигнала."""
    ticker = str(payload.get('ticker', ''))
    price = float(payload.get('price', 0))
    rule = str(payload.get('rule', ''))
    if not ticker or price <= 0:
        return {'ok': False, 'error': 'ticker and price are required'}
    result = bm._metascalp_client.place_signal_level(conn_id, ticker, price, rule)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'signal_levels')
    return result


@bm.app.delete('/api/metascalp/connections/{conn_id}/signal-levels/{level_id}')
def metascalp_remove_signal_level(conn_id: str, level_id: str, _: None=Depends(bm._require_admin_token)) -> dict:
    """Удаление уровня сигнала по ID."""
    result = bm._metascalp_client.remove_signal_level(conn_id, level_id)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'signal_levels')
    return result


@bm.app.delete('/api/metascalp/connections/{conn_id}/signal-levels')
def metascalp_remove_all_signal_levels(conn_id: str, ticker: str, _: None=Depends(bm._require_admin_token)) -> dict:
    """Удаление всех уровней сигналов для тикера."""
    result = bm._metascalp_client.remove_all_signal_levels(conn_id, ticker)
    if result.get('ok'):
        bm._metascalp_cache.invalidate(conn_id, 'signal_levels')
    return result


@bm.app.delete('/api/metascalp/signal-levels/triggered')
def metascalp_remove_triggered(_: None=Depends(bm._require_admin_token)) -> dict:
    """Удаление всех сработавших уровней сигналов."""
    result = bm._metascalp_client.remove_triggered_signal_levels()
    if result.get('ok'):
        bm._metascalp_cache.invalidate(None, 'signal_levels')
    return result


@bm.app.get('/api/metascalp/risk')
def metascalp_risk() -> dict:
    """Риск-метрики MetaScalp из PortfolioRiskManager."""
    adapter = bm._MetaScalpAdapter()
    return {'ok': True, 'engine': adapter.engine_name, 'open_notional': adapter.get_open_notional(), 'open_symbols': adapter.get_open_symbols(), 'positions_count': adapter.get_status().get('positions_count', 0), 'orders_count': adapter.get_status().get('orders_count', 0)}


@bm.app.get('/api/metascalp/auto-trade/config')
def metascalp_auto_trade_config() -> dict:
    """Конфигурация авто-торговли signal levels."""
    return {'ok': True, 'config': bm._metascalp_auto_trader.get_config()}


@bm.app.post('/api/metascalp/auto-trade/config')
def metascalp_auto_trade_config_set(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить конфигурацию авто-торговли."""
    bm._metascalp_auto_trader.set_config(payload)
    return {'ok': True, 'config': bm._metascalp_auto_trader.get_config()}


@bm.app.post('/api/metascalp/auto-trade/trigger')
def metascalp_auto_trade_trigger(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Ручной триггер авто-торговли (для тестирования)."""
    conn_id = str(payload.get('conn_id', ''))
    ticker = str(payload.get('ticker', ''))
    price = float(payload.get('price', 0))
    rule = str(payload.get('rule', ''))
    if not conn_id or not ticker or price <= 0:
        return {'ok': False, 'error': 'conn_id, ticker, price are required'}
    result = bm._metascalp_auto_trader.on_signal_triggered(conn_id, ticker, price, rule)
    return result


@bm.app.get('/api/basis')
def basis_all() -> dict:
    """Return all current basis snapshots from the basis calculator."""
    snapshots = bm._futures_arb_basis_calc.get_all_basis()
    return {'ok': True, 'count': len(snapshots), 'snapshots': [{'symbol': s.symbol, 'combo': s.exchange_combo, 'spot_mid': s.spot_mid, 'futures_mid': s.futures_mid, 'basis_bps': s.basis_bps, 'executable_cc_bps': s.executable_basis_cc_bps, 'executable_rcc_bps': s.executable_basis_rcc_bps, 'estimated_apy': s.estimated_apy, 'funding_rate': s.funding_rate or 0.0, 'status': s.status, 'timestamp_ms': s.timestamp_ms} for s in snapshots]}


@bm.app.get('/api/metascalp/basis')
def metascalp_basis(ticker: str=Query('BTCUSDT', min_length=3, max_length=40), combo: str=Query('mexc_spot+mexc_futures')) -> dict:
    symbol = ticker.strip().upper()
    snapshot = bm._futures_arb_basis_calc.get_current_basis(symbol, combo)
    if snapshot is None:
        return {'ok': False, 'error': f'No basis data for {symbol} {combo}'}
    from mexc_monitor.futures_arb.strategy_engine import _futures_exchange_from_combo
    futures_exchange = _futures_exchange_from_combo(combo)
    funding_info = None
    if futures_exchange:
        funding_info = bm._futures_arb_funding.get_funding(symbol, futures_exchange)
    return {'ok': True, 'ticker': symbol, 'combo': combo, 'spot_mid': snapshot.spot_mid, 'futures_mid': snapshot.futures_mid, 'basis_bps': snapshot.basis_bps, 'executable_cc_bps': snapshot.executable_basis_cc_bps, 'executable_rcc_bps': snapshot.executable_basis_rcc_bps, 'funding_rate': funding_info.current_rate if funding_info else snapshot.funding_rate or 0.0, 'estimated_apy': snapshot.estimated_apy, 'status': snapshot.status, 'timestamp_ms': snapshot.timestamp_ms}


@bm.app.post('/api/metascalp/spread')
def metascalp_open_spread(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    conn_id = str(payload.get('conn_id', ''))
    ticker = str(payload.get('ticker', '')).strip().upper()
    side = str(payload.get('side', '')).strip()
    notional = float(payload.get('notional', 0))
    combo = str(payload.get('combo', 'mexc_spot+mexc_futures'))
    if not conn_id or not ticker or (not side) or (notional <= 0):
        return {'ok': False, 'error': 'conn_id, ticker, side, notional are required'}
    if side not in ('Buy', 'Sell'):
        return {'ok': False, 'error': 'side must be Buy or Sell'}
    snapshot = bm._futures_arb_basis_calc.get_current_basis(ticker, combo)
    if snapshot is None or snapshot.status == 'stale' or snapshot.spot_mid <= 0:
        return {'ok': False, 'error': f'No fresh basis data for {ticker}'}
    spot_price = snapshot.spot_mid
    futures_price = snapshot.futures_mid
    spot_side = side
    futures_side = 'Sell' if side == 'Buy' else 'Buy'
    spot_size = round(notional / spot_price, 6) if spot_price > 0 else 0
    futures_size = round(notional / futures_price, 6) if futures_price > 0 else 0
    if spot_size <= 0 or futures_size <= 0:
        return {'ok': False, 'error': 'Invalid price data for sizing'}
    spot_result = bm._metascalp_client.place_order(conn_id, ticker, spot_side, 'Market', spot_size, None)
    futures_ticker = ticker.replace('USDT', '_USDT') if 'mexc_futures' in combo else ticker
    futures_result = bm._metascalp_client.place_order(conn_id, futures_ticker, futures_side, 'Market', futures_size, None)
    strategy_name = 'cash_and_carry' if side == 'Buy' else 'reverse_cash_and_carry'
    bm._metascalp_cache.invalidate(conn_id, 'orders')
    bm._metascalp_cache.invalidate(conn_id, 'positions')
    return {'ok': True, 'strategy': strategy_name, 'spot_order': spot_result, 'futures_order': futures_result, 'basis_bps': snapshot.basis_bps, 'executable_cc_bps': snapshot.executable_basis_cc_bps, 'executable_rcc_bps': snapshot.executable_basis_rcc_bps}


@bm.app.post('/api/metascalp/open-ticker')
def metascalp_open_ticker(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Create a signal level in MetaScalp to bring the ticker into focus.

    MetaScalp shows a popup when a signal level is created; clicking it opens the ticker.
    Also returns a metascalp:// URL the frontend can try as a custom protocol fallback.
    """
    conn_id = str(payload.get('conn_id', '')).strip()
    ticker = str(payload.get('ticker', '')).strip().upper()
    price = payload.get('price')
    if not ticker:
        return {'ok': False, 'error': 'ticker is required'}
    if not conn_id:
        conns = bm._metascalp_client.connections()
        if conns:
            conn_id = conns[0].id
        else:
            return {'ok': False, 'error': 'No MetaScalp connections available'}
    if price is None:
        from mexc_monitor.spread_buffer import get_latest
        tick = get_latest(ticker)
        if tick is None:
            fut = ticker.replace('USDT', '_USDT') if ticker.endswith('USDT') else None
            if fut:
                tick = get_latest(fut)
        price = tick.mid if tick else 0.0
    if price <= 0:
        return {'ok': False, 'error': 'Cannot resolve current price for ticker'}
    result = bm._metascalp_client.place_signal_level(conn_id=conn_id, ticker=ticker, price=float(price), rule='web_open')
    return {'ok': result.get('ok', False), 'connection_id': conn_id, 'ticker': ticker, 'price': price, 'metascalp_url': f'metascalp://open-ticker/{ticker}?connection={conn_id}', 'signal_level_result': result}


@bm.app.get('/api/metascalp/density/scan')
def metascalp_density_scan(conn_id: str=Query('', description='Connection ID (пусто = auto-resolve)'), tickers: str=Query('', description='CSV тикеров (пусто = watchlist из конфига)'), only_candidates: bool=Query(True, description='Только кандидаты (стены + спред)'), min_notional: float=Query(0, ge=0, description='Override min_notional_usdt (0 = из конфига)'), multiplier: float=Query(0, ge=0, description='Override multiplier (0 = из конфига)'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Сканировать стакан MetaScalp на плотности под стратегию ProBoyScalp."""
    cid = conn_id.strip()
    if not cid:
        conns = bm._metascalp_client.connections()
        if not conns:
            return {'ok': False, 'error': 'No MetaScalp connections available'}
        cid = conns[0].id
    if tickers.strip():
        tk_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    else:
        tk_list = [str(t).strip().upper() for t in bm._metascalp_signal_worker.get_config().get('watchlist', []) if str(t).strip()]
    if not tk_list:
        return {'ok': False, 'error': 'No tickers provided and watchlist is empty'}
    scanner = bm._metascalp_density_scanner
    if min_notional > 0 or multiplier > 0:
        from mexc_monitor.metascalp.density_scanner import MetaScalpDensityScanner
        cfg = bm._metascalp_signal_worker.get_config()
        scanner = MetaScalpDensityScanner(bm._metascalp_client, min_notional_usdt=min_notional if min_notional > 0 else float(cfg.get('min_notional_usdt', 1000)), multiplier=multiplier if multiplier > 0 else float(cfg.get('multiplier', 5)), min_spread_bps=float(cfg.get('min_spread_bps', 5)))
    scans = scanner.scan_watchlist(cid, tk_list, only_candidates=only_candidates, push_history=True)
    return {'ok': True, 'conn_id': cid, 'scanned_count': len(tk_list), 'returned_count': len(scans), 'scans': [s.to_dict() for s in scans]}


@bm.app.post('/api/metascalp/density/scan-now')
def metascalp_density_scan_now(_: None=Depends(bm._require_admin_token)) -> dict:
    """Принудительный проход SignalWorker сейчас (независимо от interval)."""
    signals = bm._metascalp_signal_worker.scan_now()
    return {'ok': True, 'signals_count': len(signals), 'signals': [s.to_dict() for s in signals]}


@bm.app.get('/api/metascalp/signals')
def metascalp_signals(limit: int=Query(20, ge=1, le=200), _: None=Depends(bm._require_admin_token)) -> dict:
    """Последние комбинированные сигналы (плотности + участник) из ring buffer."""
    signals = bm._metascalp_signal_worker.get_top_signals(limit=limit)
    return {'ok': True, 'count': len(signals), 'signals': signals}


@bm.app.get('/api/metascalp/density/last-scan')
def metascalp_density_last_scan(_: None=Depends(bm._require_admin_token)) -> dict:
    """Последний полный снимок скана watchlist (все тикеры, включая пропущенные)."""
    scans = bm._metascalp_signal_worker.get_last_scan()
    return {'ok': True, 'count': len(scans), 'scans': scans}


@bm.app.get('/api/metascalp/density/history')
def metascalp_density_history(ticker: str=Query(..., description='Тикер'), since_ms: int | None=Query(None, description='С (epoch ms)'), max_points: int=Query(200, ge=10, le=2000), _: None=Depends(bm._require_admin_token)) -> dict:
    """История snapshots плотности по тикеру из density_buffer."""
    from mexc_monitor.density_buffer import get_history, snapshot_to_dict
    ticker_norm = ticker.strip().upper()
    history = get_history(ticker_norm, since_ms=since_ms, max_points=max_points)
    return {'ok': True, 'ticker': ticker_norm, 'count': len(history), 'history': [snapshot_to_dict(s) for s in history]}


@bm.app.get('/api/metascalp/density/wall-changes')
def metascalp_density_wall_changes(ticker: str=Query(..., description='Тикер'), since_ms: int | None=Query(None, description='С (epoch ms)'), limit: int=Query(100, ge=1, le=1000), _: None=Depends(bm._require_admin_token)) -> dict:
    """История изменений стен (appeared/disappeared/grew/shrunk) по тикеру."""
    from mexc_monitor.density_buffer import get_wall_changes, wall_change_to_dict
    ticker_norm = ticker.strip().upper()
    changes = get_wall_changes(ticker_norm, since_ms=since_ms, limit=limit)
    return {'ok': True, 'ticker': ticker_norm, 'count': len(changes), 'changes': [wall_change_to_dict(c) for c in changes]}


@bm.app.get('/api/metascalp/participants')
def metascalp_participants(tickers: str=Query('', description='CSV тикеров (пусто = все отслеживаемые)'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Активные участники (всплески market-ордеров) сейчас."""
    tk_list = [t.strip().upper() for t in tickers.split(',') if t.strip()] if tickers.strip() else None
    signals = bm._metascalp_participant_detector.detect_all(tk_list)
    return {'ok': True, 'count': len(signals), 'participants': [s.to_dict() for s in signals], 'detector_stats': bm._metascalp_participant_detector.stats()}


@bm.app.get('/api/metascalp/participants/volume')
def metascalp_participants_volume(ticker: str=Query(..., description='Тикер'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Сводка объёмов buy/sell по тикеру без формирования сигнала (для UI)."""
    ticker_norm = ticker.strip().upper()
    return {'ok': True, 'summary': bm._metascalp_participant_detector.get_volume_summary(ticker_norm)}


@bm.app.post('/api/metascalp/trades/subscribe')
def metascalp_trades_subscribe(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Подписаться на WS trades тикеров (для ParticipantDetector).

    Body: {"conn_id": "...", "tickers": ["LYN_USDT", ...]}
    """
    conn_id = str(payload.get('conn_id', '')).strip()
    tickers = payload.get('tickers', [])
    if not isinstance(tickers, list):
        return {'ok': False, 'error': 'tickers must be a list'}
    if not conn_id:
        conns = bm._metascalp_client.connections()
        if not conns:
            return {'ok': False, 'error': 'No MetaScalp connections available'}
        conn_id = conns[0].id
    added: list[str] = []
    for tk in tickers:
        tk = str(tk).strip().upper()
        if tk:
            bm._metascalp_ws_bridge.subscribe_trades(conn_id, tk)
            added.append(tk)
    return {'ok': True, 'conn_id': conn_id, 'subscribed': added, 'all_subscribed': [f'{c}:{t}' for c, t in bm._metascalp_ws_bridge.subscribed_trades()]}


@bm.app.get('/api/metascalp/signals/config')
def metascalp_signals_config_get(_: None=Depends(bm._require_admin_token)) -> dict:
    """Текущий конфиг SignalWorker (watchlist, пороги, interval)."""
    return {'ok': True, 'config': bm._metascalp_signal_worker.get_config(), 'running': bm._metascalp_signal_worker.is_running()}


@bm.app.post('/api/metascalp/signals/config')
def metascalp_signals_config_update(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить конфиг SignalWorker (поля: enabled, watchlist, interval_sec, пороги)."""
    cfg = bm._metascalp_signal_worker.update_config(payload)
    return {'ok': True, 'config': cfg, 'running': bm._metascalp_signal_worker.is_running()}


@bm.app.post('/api/metascalp/signals/reload')
def metascalp_signals_reload(_: None=Depends(bm._require_admin_token)) -> dict:
    """Перезагрузить конфиг SignalWorker с диска (с рестартом если был включён)."""
    cfg = bm._metascalp_signal_worker.reload_config()
    return {'ok': True, 'config': cfg, 'running': bm._metascalp_signal_worker.is_running()}
