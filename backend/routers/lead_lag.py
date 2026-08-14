"""Routes: lead_lag (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/lead-lag/status')
def lead_lag_status() -> dict:
    """Статус движка lead-lag: running, connections, symbols, signals, uptime."""
    engine = bm._get_lead_lag_engine()
    return engine.get_status_info()


@bm.app.get('/api/lead-lag/signals')
def lead_lag_signals(active: bool=Query(False, description='Только активные сигналы'), symbol: str | None=Query(None, description='Фильтр по символу'), limit: int=Query(50, ge=1, le=1000, description='Лимит записей (1-1000)')) -> list[dict]:
    """Список сигналов, отсортированных по created_at DESC."""
    engine = bm._get_lead_lag_engine()
    if active:
        signals = engine.get_active_signals()
    else:
        signals = engine.get_recent_signals(limit=limit)
    if symbol:
        signals = [s for s in signals if s.symbol == symbol]
    signals = signals[:limit]
    from dataclasses import asdict
    result = []
    for sig in signals:
        d = asdict(sig)
        d['direction'] = sig.direction.value if hasattr(sig.direction, 'value') else sig.direction
        d['status'] = sig.status.value if hasattr(sig.status, 'value') else sig.status
        result.append(d)
    return result


@bm.app.get('/api/lead-lag/stats')
def lead_lag_stats(window_hours: int=Query(24, ge=1, le=168, description='Окно статистики (1-168 часов)')) -> dict:
    """Агрегированная статистика за указанное окно."""
    engine = bm._get_lead_lag_engine()
    stats = engine.get_stats(window_hours=window_hours)
    if stats is None:
        return {'window_hours': window_hours, 'total_signals': 0, 'resolved_signals': 0, 'expired_signals': 0, 'win_rate': None, 'avg_lag_ms': None, 'median_lag_ms': None, 'avg_theoretical_pnl_bps': None, 'total_theoretical_pnl_bps': 0.0, 'signals_per_hour': 0.0, 'top_symbols': []}
    from dataclasses import asdict
    return asdict(stats)


@bm.app.get('/api/lead-lag/prices')
def lead_lag_prices(symbol: str=Query(..., min_length=1, description='Символ (BTCUSDT)')) -> dict:
    """Mid-цены по всем биржам для указанного символа.

    Returns 200 with an empty `prices` dict when the symbol isn't currently
    monitored (engine stopped, symbol not in config, or no snapshot yet).
    Returning 404 here used to surface as a red console error on the /lead-lag
    page even though "no data yet" is a perfectly normal state — not an error.
    The frontend already treats `prices == {}` as "no data" and renders the
    empty state, so this keeps the UX honest without a scary network error.
    """
    engine = bm._get_lead_lag_engine()
    prices = engine.get_prices(symbol.strip().upper())
    if prices is None:
        prices = {}
    return {'symbol': symbol.strip().upper(), 'prices': prices}


@bm.app.get('/api/lead-lag/lag-estimates')
def lead_lag_estimates() -> list[dict]:
    """Текущие оценки lag для всех символов."""
    engine = bm._get_lead_lag_engine()
    return engine.get_lag_estimates()


@bm.app.post('/api/lead-lag/start')
def lead_lag_start(_: None=Depends(bm._require_admin_token)) -> dict:
    """Запуск движка lead-lag (идемпотентно)."""
    engine = bm._get_lead_lag_engine()
    error = engine.start()
    if error:
        raise HTTPException(status_code=400, detail=error)
    return engine.get_status_info()


@bm.app.post('/api/lead-lag/stop')
def lead_lag_stop(_: None=Depends(bm._require_admin_token)) -> dict:
    """Остановка движка lead-lag (идемпотентно)."""
    engine = bm._get_lead_lag_engine()
    engine.stop()
    return engine.get_status_info()
