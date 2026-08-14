"""Routes: portfolio_risk (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/portfolio-risk/status')
def portfolio_risk_status() -> dict:
    """Portfolio risk status: aggregated exposure, drawdown, alerts."""
    status = bm._portfolio_risk.get_status()
    return {'ok': True, 'total_exposure_usdt': status.total_exposure_usdt, 'engine_count': status.engine_count, 'positions_by_symbol': status.positions_by_symbol, 'daily_drawdown_usdt': status.daily_drawdown_usdt, 'kill_switch_active': status.kill_switch_active, 'alerts': status.alerts, 'all_clear': status.all_clear}


@bm.app.post('/api/portfolio-risk/kill-switch')
def portfolio_risk_kill_switch(_: None=Depends(bm._require_admin_token)) -> dict:
    """Activate global kill switch across all engines."""
    bm._portfolio_risk.activate_kill_switch(reason='api_request')
    return {'ok': True, 'kill_switch_active': True}


@bm.app.post('/api/portfolio-risk/deactivate-kill-switch')
def portfolio_risk_deactivate(_: None=Depends(bm._require_admin_token)) -> dict:
    """Deactivate global kill switch."""
    bm._portfolio_risk.deactivate_kill_switch()
    return {'ok': True, 'kill_switch_active': False}
