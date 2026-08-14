"""Routes: capture (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/capture/status')
def capture_status() -> dict:
    """Статус движка сбора спреда."""
    return {'ok': True, **bm._spread_capture_engine.get_status()}


@bm.app.patch('/api/capture/settings')
def capture_update_settings(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить настройки стратегии."""
    try:
        return {'ok': True, **bm._spread_capture_engine.update_settings(payload)}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@bm.app.post('/api/capture/start')
def capture_start(_: None=Depends(bm._require_admin_token)) -> dict:
    """Запустить движок сбора спреда."""
    return {'ok': True, **bm._spread_capture_engine.start()}


@bm.app.post('/api/capture/stop')
def capture_stop(_: None=Depends(bm._require_admin_token)) -> dict:
    """Остановить движок."""
    return {'ok': True, **bm._spread_capture_engine.stop()}


@bm.app.post('/api/capture/reset-position')
def capture_reset_position(_: None=Depends(bm._require_admin_token)) -> dict:
    """Аварийный сброс позиции."""
    return {'ok': True, **bm._spread_capture_engine.reset_position()}


@bm.app.post('/api/capture/reset-stats')
def capture_reset_stats(_: None=Depends(bm._require_admin_token)) -> dict:
    """Сброс статистики."""
    return {'ok': True, **bm._spread_capture_engine.reset_stats()}


@bm.app.get('/api/capture/pnl')
def capture_current_pnl() -> dict:
    """Текущий PNL открытой позиции."""
    pnl = bm._spread_capture_engine.get_current_pnl()
    return {'ok': True, 'pnl': pnl}


@bm.app.get('/api/capture/trades')
def capture_trades(limit: int=Query(50, ge=1, le=500)) -> dict:
    """История сделок."""
    trades = bm._spread_capture_engine.get_trades(limit=limit)
    return {'ok': True, 'count': len(trades), 'trades': trades}


@bm.app.get('/api/capture/events')
def capture_events(limit: int=Query(50, ge=1, le=200)) -> dict:
    """Лог событий движка."""
    events = bm._spread_capture_engine.get_events(limit=limit)
    return {'ok': True, 'count': len(events), 'events': events}


@bm.app.get('/api/capture/signals')
def capture_signals(limit: int=Query(20, ge=1, le=100)) -> dict:
    """Последние сигналы входа."""
    signals = bm._spread_capture_engine.get_signals(limit=limit)
    return {'ok': True, 'count': len(signals), 'signals': signals}
