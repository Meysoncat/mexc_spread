"""Routes: diagnostics (extracted from backend/main.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/health')
def health() -> dict[str, Any]:
    from mexc_monitor.ws_bookticker import feeds_health
    from mexc_monitor.ws_futures_depth_book import depth_book_health
    l2 = bm.l2_depth_health()
    return {'status': 'ok', 'ws_feeds': feeds_health(), 'orderbook_ws': {'mexc_futures_depth': depth_book_health(), 'okx_l2_depth': l2.get('okx'), 'bybit_l2_depth': l2.get('bybit')}}


@bm.app.get('/api/diagnostics/clock-skew')
def diagnostics_clock_skew() -> dict[str, Any]:
    """Расхождение локальных часов с часами бирж (по заголовку ``Date``).

    Замеры собираются пассивно на каждом HTTP-ответе. ``uncertainty_ms`` —
    шум измерения (гранулярность ``Date`` 1 с + половина RTT); пока скос его
    не превышает, он считается недостоверным и не применяется к таймстемпам
    в freshness-проверках.
    """
    from mexc_monitor.clock_skew import get_detector
    detector = get_detector()
    status = detector.get_status()
    return {'ok': True, 'exchanges': status, 'skewed': [s['exchange'] for s in status if s.get('skewed')]}


@bm.app.get('/api/diagnostics/sources')
def diagnostics_sources(timeout_sec: float=Query(8.0, ge=1.0, le=30.0)) -> dict[str, Any]:
    """Диагностика источников: REST-латентность + геоблок и свежесть WS-фида.

    По каждой бирже возвращает:
    - ``rest``: {status, status_code, elapsed_ms, url} — проба REST (через
      прокси, если настроен);
    - ``ws``: {running, live, symbols, last_message_age_sec} — состояние
      WebSocket-фида (если для биржи он есть);
    - ``recommended``: какой путь сейчас предпочтителен для снимка.
    """
    from mexc_monitor.source_probes import PROBES, probe_all
    from mexc_monitor.ws_bookticker import feeds_health
    rest = probe_all(bm.DEFAULT_SETTINGS, timeout_sec=timeout_sec)
    ws = feeds_health()
    sources: list[dict[str, Any]] = []
    for name in PROBES:
        rest_res = rest.get(name, {})
        ws_res = ws.get(name)
        ws_spot_res = ws.get(f'{name}_spot')
        ws_live = bool(ws_res and ws_res.get('live'))
        rest_ok = rest_res.get('status') == 'ok'
        if ws_live:
            recommended = 'ws'
        elif rest_ok:
            recommended = 'rest'
        else:
            recommended = 'none'
        sources.append({'exchange': name, 'rest': rest_res, 'ws': ws_res, 'ws_spot': ws_spot_res, 'recommended': recommended, 'proxy': bm.effective_http_proxy(bm.DEFAULT_SETTINGS, name)})
    active_proxy = bm.effective_http_proxy(bm.DEFAULT_SETTINGS, 'generic')
    reachable = sum((1 for s in sources if s['rest'].get('status') == 'ok'))
    return {'ok': True, 'generated_at': bm.datetime.now(bm.timezone.utc).isoformat(), 'active_proxy': active_proxy, 'summary': {'total': len(sources), 'rest_reachable': reachable, 'ws_live': sum((1 for s in sources if s['ws'] and s['ws'].get('live'))), 'ws_spot_live': sum((1 for s in sources if s['ws_spot'] and s['ws_spot'].get('live')))}, 'sources': sources}


@bm.app.get('/api/admin-token')
def get_admin_token(request: Request) -> dict:
    """
    Отдать ADMIN_TOKEN фронтенду — только для локального dev-запуска.

    Проверка «client.host == 127.0.0.1» сама по себе НЕ защищает: при
    reverse-proxy на том же хосте (nginx → 127.0.0.1:8006) любой внешний
    запрос выглядит как локальный и получал бы полный доступ к торговому API.
    Поэтому дополнительно требуем отсутствие forwarded-заголовков и даём
    операторам жёсткий выключатель ADMIN_TOKEN_LOCAL_BOOTSTRAP=0.

    В проде токен вводится вручную (Trading Admin → поле X-Admin-Token).
    """
    if not bm._local_bootstrap_allowed(request):
        raise HTTPException(status_code=403, detail='admin token bootstrap disabled: paste ADMIN_TOKEN manually (Trading Admin → X-Admin-Token)')
    return {'ok': True, 'token': bm._ADMIN_TOKEN}


@bm.app.get('/api/debug/mexc-connectivity')
def debug_mexc_connectivity(timeout_sec: float=Query(8.0, ge=1.0, le=30.0, description='Timeout per check in seconds')) -> dict:
    """
    Быстрая диагностика доступности MEXC endpoints из текущего runtime API.
    """
    import httpx
    checks: dict[str, dict] = {}

    def _check_http(name: str, url: str) -> None:
        started = bm.time.monotonic()
        try:
            r = httpx.get(url, timeout=timeout_sec)
            checks[name] = {'ok': r.status_code == 200, 'status_code': r.status_code, 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': url, 'body_preview': r.text[:180]}
        except Exception as e:
            checks[name] = {'ok': False, 'status_code': None, 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': url, 'error': f'{type(e).__name__}: {e}'}
    _check_http('spot_book_ticker', bm.DEFAULT_SETTINGS.book_ticker_url)
    _check_http('futures_ticker', bm.DEFAULT_SETTINGS.contract_ticker_url)
    ws_url = bm.DEFAULT_SETTINGS.futures_ws_url
    started = bm.time.monotonic()
    try:
        import websocket
        from websocket import WebSocketBadStatusException
        ws = websocket.create_connection(ws_url, timeout=timeout_sec)
        try:
            ws.close()
        except Exception:
            pass
        checks['futures_ws_handshake'] = {'ok': True, 'http_status': 101, 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': ws_url}
    except ImportError:
        checks['futures_ws_handshake'] = {'ok': False, 'http_status': None, 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': ws_url, 'error': 'websocket-client is not installed'}
    except WebSocketBadStatusException as e:
        checks['futures_ws_handshake'] = {'ok': False, 'http_status': int(e.status_code), 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': ws_url, 'error': str(e)}
    except Exception as e:
        checks['futures_ws_handshake'] = {'ok': False, 'http_status': None, 'elapsed_ms': int((bm.time.monotonic() - started) * 1000), 'url': ws_url, 'error': f'{type(e).__name__}: {e}'}
    all_ok = all((item.get('ok') for item in checks.values()))
    return {'ok': all_ok, 'timeout_sec': timeout_sec, 'settings': {'spot_base_url': bm.DEFAULT_SETTINGS.base_url, 'futures_base_url': bm.DEFAULT_SETTINGS.futures_base_url, 'futures_ws_url': bm.DEFAULT_SETTINGS.futures_ws_url, 'futures_ticker_source': bm.DEFAULT_SETTINGS.futures_ticker_source}, 'checks': checks}


@bm.app.get('/api/metrics-reference')
def metrics_reference() -> dict:
    """Отдаёт JSON справки по метрикам (редактируется без пересборки UI при dev proxy)."""
    path = bm._METRICS_REF_PUBLIC if bm._METRICS_REF_PUBLIC.is_file() else bm._METRICS_REF_SRC
    if not path.is_file():
        raise HTTPException(status_code=404, detail='metrics-reference.json not found')
    try:
        return bm.json.loads(path.read_text(encoding='utf-8'))
    except bm.json.JSONDecodeError as e:
        bm.logger.warning('metrics-reference invalid JSON: %s', e)
        raise HTTPException(status_code=500, detail='invalid metrics-reference.json') from e
