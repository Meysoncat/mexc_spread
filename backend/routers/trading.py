"""Routes: trading (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/trading/exchanges')
def trading_exchanges() -> dict:
    """Return all supported exchanges with availability status."""
    result = []
    for ex in bm.Exchange:
        config = bm.EXCHANGE_CONFIGS[ex]
        has_creds = bool(bm.os.environ.get(f'{config.env_prefix}_API_KEY') and bm.os.environ.get(f'{config.env_prefix}_API_SECRET'))
        result.append({'exchange': ex.value, 'available': has_creds, 'paper_only': not has_creds, 'markets': ['spot', 'futures'], 'spot_base_url': config.spot_base_url, 'futures_base_url': config.futures_base_url, 'order_types': ['LIMIT', 'MARKET']})
    return {'ok': True, 'exchanges': result}


@bm.app.get('/api/trading/engines')
def trading_engines(_: None=Depends(bm._require_admin_token)) -> dict:
    """Return all registered engine instances."""
    return {'ok': True, 'engines': bm._registry.list_engines()}


@bm.app.get('/api/trading/accounts')
def trading_accounts() -> dict:
    """List configured trading accounts."""
    if not bm._ACCOUNTS_PATH.exists():
        return {'ok': True, 'accounts': [], 'message': 'config/trading_accounts.json not found'}
    try:
        data = bm.json.loads(bm._ACCOUNTS_PATH.read_text(encoding='utf-8'))
        accounts = data.get('accounts', [])
        for acc in accounts:
            env_key = acc.get('api_key_env', '')
            env_secret = acc.get('api_secret_env', '')
            acc['has_credentials'] = bool(bm.os.environ.get(env_key, '').strip() and bm.os.environ.get(env_secret, '').strip())
        return {'ok': True, 'accounts': accounts}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'accounts': []}


@bm.app.get('/api/backtest')
def backtest(symbol: str=Query('BTCUSDT', description='Символ'), market: str=Query('futures', description='spot или futures'), entry_bps: float=Query(30.0, description='Порог входа (bps)'), exit_bps: float=Query(5.0, description='Порог выхода (bps)'), notional: float=Query(1000.0, description='Размер ордера (USDT)'), max_hold_sec: int=Query(300, description='Макс. удержание (сек)')) -> dict:
    """Бэктест стратегии захвата спреда на исторических данных."""
    from mexc_monitor.backtest import BacktestSettings, run_backtest
    from mexc_monitor.history_store import resolve_history_db_path
    db_path = resolve_history_db_path(bm.DEFAULT_SETTINGS)
    if not db_path.is_file():
        return {'ok': False, 'error': f'History DB not found: {db_path}'}
    settings = BacktestSettings(symbol=symbol.strip().upper(), market=market if market in ('spot', 'futures') else 'futures', entry_threshold_bps=entry_bps, exit_threshold_bps=exit_bps, order_notional_usdt=notional, max_hold_sec=max_hold_sec)
    try:
        result = run_backtest(db_path, settings)
        return {'ok': True, **result.to_dict()}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@bm.app.get('/api/system/capabilities')
def system_capabilities() -> dict:
    """Report whether each trading engine is ready to place REAL live orders.

    This endpoint exists so the UI can honestly tell the user, BEFORE they pick
    "live" mode, whether that mode will actually trade or silently fall back to
    a simulator. Today only TradingEngine places real orders; SpreadCapture and
    Arbitrage engines have full live-order code paths but never get an
    OrderExecutor injected in production, so "live" used to fake fills without
    warning. FuturesArb has no live path at all by design.

    Returns per-engine `live_ready: bool` plus a human-readable `reasons` list
    explaining what's missing. This is a read-only status endpoint — it does
    NOT require the admin token, because the user needs to see it before they
    even have a token configured (otherwise they can't tell what's wrong).
    """
    capture_reasons: list[str] = []
    capture_ready = False
    capture_executor = getattr(bm._spread_capture_engine, '_order_executor', None)
    if capture_executor is None:
        capture_reasons.append('order_executor not injected (live mode will simulate fills)')
    else:
        capture_ready = True
    arb_reasons: list[str] = []
    arb_ready = False
    arb_executor = getattr(bm._arbitrage_engine, '_order_executor', None)
    if arb_executor is None:
        arb_reasons.append('order_executor not injected (live mode will auto-mark fills)')
    arb_settings = getattr(bm._arbitrage_engine, '_settings', None)
    if arb_settings is not None and (not getattr(arb_settings, 'use_real_orders', False)):
        arb_reasons.append('use_real_orders=False (engine config)')
    if arb_executor is not None and (not arb_reasons):
        arb_ready = True
    fa_reasons = ['engine has no live order executor (paper-only by design)']
    fa_ready = False
    trading_per_exchange: dict[str, list[str]] = {}
    trading_any_ready = False
    for ex in bm.Exchange:
        config = bm.EXCHANGE_CONFIGS[ex]
        has_creds = bool(bm.os.environ.get(f'{config.env_prefix}_API_KEY') and bm.os.environ.get(f'{config.env_prefix}_API_SECRET'))
        key = f'{ex.value}'
        if has_creds:
            trading_any_ready = True
        else:
            trading_per_exchange[key] = [f'{config.env_prefix}_API_KEY / _API_SECRET not set']
    return {'ok': True, 'live_ready': {'capture': capture_ready, 'arbitrage': arb_ready, 'futures_arb': fa_ready, 'trading': trading_any_ready}, 'reasons': {'capture': capture_reasons, 'arbitrage': arb_reasons, 'futures_arb': fa_reasons, 'trading': trading_per_exchange}}


@bm.app.get('/api/trading/status')
def trading_status(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, **engine.status()}


@bm.app.post('/api/trading/start')
def trading_start(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, **engine.start()}


@bm.app.post('/api/trading/stop')
def trading_stop(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, **engine.stop()}


@bm.app.post('/api/trading/kill-switch')
def trading_kill_switch(enabled: bool=Query(..., description='true -> kill switch ON (stop orders)'), exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, **engine.set_kill_switch(enabled)}


@bm.app.post('/api/trading/run-once')
def trading_run_once(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, **engine.run_once()}


@bm.app.post('/api/trading/reconcile')
def trading_reconcile(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Reconcile in-memory positions with exchange (live mode)."""
    engine = bm._resolve_engine(exchange, market)
    return engine.reconcile()


@bm.app.get('/api/trading/runtime-settings')
def trading_runtime_settings(exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    return {'ok': True, 'settings': engine.status().get('settings', {})}


@bm.app.patch('/api/trading/runtime-settings')
def trading_runtime_settings_update(payload: dict, exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    try:
        out = engine.update_runtime_settings(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {'ok': True, **out}


@bm.app.get('/api/trading/events')
def trading_events(limit: int=Query(100, ge=1, le=1000), exchange: str | None=Query(None, description='Exchange name (default: mexc)'), market: str | None=Query(None, description='Market type (default: spot)'), _: None=Depends(bm._require_admin_token)) -> dict:
    engine = bm._resolve_engine(exchange, market)
    rows = engine.read_recent_events(limit=limit)
    return {'ok': True, 'count': len(rows), 'rows': rows}
