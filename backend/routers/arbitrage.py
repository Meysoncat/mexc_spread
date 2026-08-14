"""Routes: arbitrage (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/arbitrage/status')
def arbitrage_status(_: None=Depends(bm._require_admin_token)) -> dict:
    """Статус арбитражного движка."""
    return {'ok': True, **bm._arbitrage_engine.get_status()}


@bm.app.post('/api/arbitrage/start')
def arbitrage_start(_: None=Depends(bm._require_admin_token)) -> dict:
    """Запустить арбитражный движок."""
    return {'ok': True, **bm._arbitrage_engine.start()}


@bm.app.post('/api/arbitrage/stop')
def arbitrage_stop(_: None=Depends(bm._require_admin_token)) -> dict:
    """Остановить арбитражный движок."""
    return {'ok': True, **bm._arbitrage_engine.stop()}


@bm.app.post('/api/arbitrage/kill-switch')
def arbitrage_kill_switch(enabled: bool=Query(...), _: None=Depends(bm._require_admin_token)) -> dict:
    """Kill switch арбитража."""
    return {'ok': True, **bm._arbitrage_engine.set_kill_switch(enabled)}


@bm.app.patch('/api/arbitrage/settings')
def arbitrage_update_settings(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить настройки арбитража."""
    try:
        return {'ok': True, **bm._arbitrage_engine.update_settings(payload)}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@bm.app.get('/api/arbitrage/positions')
def arbitrage_positions(_: None=Depends(bm._require_admin_token)) -> dict:
    """Открытые арбитражные позиции."""
    positions = bm._arbitrage_engine.get_positions()
    return {'ok': True, 'positions': positions, 'count': len(positions)}


@bm.app.get('/api/arbitrage/trades')
def arbitrage_trades(limit: int=Query(50, ge=1, le=500), _: None=Depends(bm._require_admin_token)) -> dict:
    """История арбитражных сделок."""
    trades = bm._arbitrage_engine.get_trades(limit=limit)
    return {'ok': True, 'trades': trades, 'count': len(trades)}


@bm.app.get('/api/arbitrage/events')
def arbitrage_events(limit: int=Query(50, ge=1, le=200), _: None=Depends(bm._require_admin_token)) -> dict:
    """Лог событий арбитража."""
    events = bm._arbitrage_engine.get_events(limit=limit)
    return {'ok': True, 'events': events, 'count': len(events)}


@bm.app.get('/api/cross-spread/history')
def cross_spread_history(symbol: str | None=Query(None, description='Символ (BTCUSDT)'), since: str | None=Query(None, description='ISO8601 начало периода'), until: str | None=Query(None, description='ISO8601 конец периода'), limit: int=Query(2000, ge=10, le=5000)) -> dict:
    """История межбиржевого спреда MEXC ↔ AsterDEX."""
    db_path = 'data/cross_spread_history.sqlite'
    try:
        config_path = bm.Path(__file__).resolve().parent.parent / 'config' / 'external_apis.json'
        if config_path.is_file():
            raw = bm.json.loads(config_path.read_text(encoding='utf-8'))
            db_path = raw.get('cross_spread_history', {}).get('db_path', db_path)
    except Exception:
        pass
    rows = bm.query_cross_spread_history(db_path=db_path, symbol=symbol, since_iso=since, until_iso=until, limit=limit)
    return {'ok': True, 'symbol': symbol.upper() if symbol else None, 'count': len(rows), 'rows': rows}
