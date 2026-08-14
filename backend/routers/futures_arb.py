"""Routes: futures_arb (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/futures-arb/status')
def futures_arb_status() -> dict:
    """Статус движка Futures/Spot Arbitrage + текущие базисы."""
    status = bm._futures_arb_engine.get_status()
    stats = bm._futures_arb_position_mgr.get_stats()
    return {'ok': True, **status, 'stats': {'total_trades': stats.total_trades, 'win_rate': stats.win_rate, 'total_net_pnl_usdt': stats.total_net_pnl_usdt, 'total_funding_earned': stats.total_funding_earned}}


@bm.app.get('/api/futures-arb/positions')
def futures_arb_positions() -> dict:
    """Открытые позиции с real-time PNL."""
    from dataclasses import asdict
    positions = bm._futures_arb_position_mgr.get_open_positions()
    return {'ok': True, 'positions': [asdict(p) for p in positions], 'count': len(positions)}


@bm.app.get('/api/futures-arb/history')
def futures_arb_history(limit: int=Query(50, ge=1, le=500), offset: int=Query(0, ge=0)) -> dict:
    """Закрытые позиции с полной разбивкой PNL."""
    from dataclasses import asdict
    positions = bm._futures_arb_position_mgr.get_closed_positions(limit=limit, offset=offset)
    return {'ok': True, 'positions': [asdict(p) for p in positions], 'count': len(positions)}


@bm.app.post('/api/futures-arb/start')
def futures_arb_start(_: None=Depends(bm._require_admin_token)) -> dict:
    """Запустить движок Futures/Spot Arbitrage."""
    bm._futures_arb_basis_calc.start()
    bm._futures_arb_funding.start()
    bm._futures_arb_basis_store.start()
    result = bm._futures_arb_engine.start()
    return {'ok': True, **result}


@bm.app.post('/api/futures-arb/stop')
def futures_arb_stop(_: None=Depends(bm._require_admin_token)) -> dict:
    """Остановить движок Futures/Spot Arbitrage."""
    result = bm._futures_arb_engine.stop()
    bm._futures_arb_basis_store.stop()
    bm._futures_arb_funding.stop()
    bm._futures_arb_basis_calc.stop()
    bm._futures_arb_position_mgr.serialize_state()
    return {'ok': True, **result}


@bm.app.patch('/api/futures-arb/settings')
def futures_arb_update_settings(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить конфигурацию в runtime."""
    result = bm._futures_arb_engine.update_settings(payload)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result)
    return {'ok': True, **result}


@bm.app.get('/api/futures-arb/basis-history')
def futures_arb_basis_history(symbol: str=Query(...), exchange_combo: str=Query('mexc_spot+mexc_futures'), since: str | None=Query(None), until: str | None=Query(None), limit: int=Query(500, ge=1, le=5000)) -> dict:
    """История базиса для графика."""
    rows = bm._futures_arb_basis_store.query_history(symbol=symbol.upper(), exchange_combo=exchange_combo, since=since, until=until, limit=limit)
    return {'ok': True, 'rows': rows, 'count': len(rows)}


@bm.app.post('/api/futures-arb/close-position')
def futures_arb_close_position(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Ручное закрытие позиции."""
    position_id = payload.get('position_id')
    if not position_id:
        raise HTTPException(status_code=400, detail='position_id required')
    result = bm._futures_arb_engine.close_position_manual(position_id)
    if 'error' in result:
        raise HTTPException(status_code=404, detail=result['error'])
    return {'ok': True, **result}
