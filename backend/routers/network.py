"""Routes: network (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Body, Depends, HTTPException, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/network/config')
def network_config_get(_: None=Depends(bm._require_admin_token)) -> dict:
    """Current exchange-proxy settings (default + per-exchange overrides).

    Admin only: ответ содержит сами прокси-URL, а они обычно с креденшелами
    (``socks5h://user:pass@host``).
    """
    return {'ok': True, **bm._network_state()}


@bm.app.patch('/api/network/config')
def network_config_update(payload: dict=Body(...), _: None=Depends(bm._require_admin_token)) -> dict:
    """Set/clear proxies at runtime — default и/или per-exchange (admin only).

    Body (all fields optional):
    - ``default_proxy``: str — общий прокси ("" сбрасывает);
    - ``per_exchange``: {exchange: url|"direct"|""} — точечные переопределения;
    - ``http_proxy_url``: str — устаревший алиас для ``default_proxy``.

    Схемы: http/https/socks5/socks5h, либо "direct" для обхода прокси.
    """
    try:
        if 'default_proxy' in payload or 'http_proxy_url' in payload:
            raw = payload.get('default_proxy', payload.get('http_proxy_url', ''))
            bm.REGISTRY.set_default(str(raw or '').strip() or None)
        per = payload.get('per_exchange')
        if isinstance(per, dict):
            for ex, val in per.items():
                if ex.strip().lower() not in bm.KNOWN_EXCHANGES:
                    raise HTTPException(status_code=400, detail=f'Неизвестная биржа: {ex}')
                bm.REGISTRY.set_exchange(ex, str(val or '').strip() or None)
    except bm.ProxyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bm.reset_clients()
    bm.set_runtime_http_proxy(bm.REGISTRY.snapshot()['default'])
    return {'ok': True, **bm._network_state()}


@bm.app.get('/api/network/test')
def network_test(exchange: str=Query('mexc'), _: None=Depends(bm._require_admin_token)) -> dict:
    """Probe one exchange's REST through its resolved proxy; report timing.

    Uses the same lightweight probe endpoints as /api/diagnostics/sources, so
    the result reflects exactly what smart routing does for that venue.

    Admin only: ответ раскрывает эффективный прокси-URL.
    """
    from mexc_monitor.source_probes import probe_one
    ex = exchange.strip().lower()
    res = probe_one(ex, bm.DEFAULT_SETTINGS)
    return {'ok': True, 'exchange': ex, 'reachable': res.get('status') == 'ok', 'status': res.get('status'), 'status_code': res.get('status_code'), 'elapsed_ms': res.get('elapsed_ms'), 'error': res.get('error'), 'proxy': bm.effective_http_proxy(bm.DEFAULT_SETTINGS, ex)}
