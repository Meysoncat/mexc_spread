"""Routes: aster (extracted from backend/main.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/aster/ping')
def aster_ping() -> dict:
    """Проверка связи с AsterDEX."""
    ok = bm._aster_public.ping()
    return {'ok': ok, 'exchange': 'asterdex'}


@bm.app.get('/api/aster/symbols')
def aster_symbols() -> dict:
    """Список торгуемых символов на AsterDEX."""
    try:
        symbols = bm._aster_public.get_symbols()
        return {'ok': True, 'symbols': symbols, 'count': len(symbols)}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'symbols': [], 'count': 0}


@bm.app.get('/api/aster/book-ticker')
def aster_book_ticker(symbol: str | None=Query(None, description='Символ (BTCUSDT) или пусто для всех')) -> dict:
    """Лучшие bid/ask на AsterDEX."""
    try:
        tickers = bm._aster_public.book_ticker(symbol=symbol)
        rows = [{'symbol': t.symbol, 'bid_price': t.bid_price, 'bid_qty': t.bid_qty, 'ask_price': t.ask_price, 'ask_qty': t.ask_qty, 'time_ms': t.time_ms, 'spread_abs': t.ask_price - t.bid_price, 'mid': (t.bid_price + t.ask_price) / 2, 'spread_bps': 10000 * (t.ask_price - t.bid_price) / ((t.bid_price + t.ask_price) / 2) if t.bid_price + t.ask_price > 0 else None} for t in tickers]
        return {'ok': True, 'count': len(rows), 'tickers': rows}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'count': 0, 'tickers': []}


@bm.app.get('/api/aster/ticker-24h')
def aster_ticker_24h(symbol: str | None=Query(None, description='Символ или пусто для всех')) -> dict:
    """24h статистика AsterDEX."""
    try:
        tickers = bm._aster_public.ticker_24h(symbol=symbol)
        rows = [{'symbol': t.symbol, 'last_price': t.last_price, 'price_change_percent': t.price_change_percent, 'high_price': t.high_price, 'low_price': t.low_price, 'volume': t.volume, 'quote_volume': t.quote_volume} for t in tickers]
        return {'ok': True, 'count': len(rows), 'tickers': rows}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'count': 0, 'tickers': []}


@bm.app.get('/api/aster/depth')
def aster_depth(symbol: str=Query(..., min_length=3, max_length=40), limit: int=Query(20, ge=5, le=1000)) -> dict:
    """Стакан AsterDEX."""
    try:
        data = bm._aster_public.depth(symbol, limit=limit)
        return {'ok': True, 'symbol': symbol.upper(), **data}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'symbol': symbol.upper(), 'bids': [], 'asks': []}


@bm.app.get('/api/aster/klines')
def aster_klines(symbol: str=Query(..., min_length=3, max_length=40), interval: str=Query('1h'), limit: int=Query(500, ge=1, le=1500)) -> dict:
    """Свечи AsterDEX."""
    try:
        raw = bm._aster_public.klines(symbol, interval=interval, limit=limit)
        candles = []
        for c in raw:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                continue
            candles.append({'time': int(c[0]) // 1000, 'open': float(c[1]), 'high': float(c[2]), 'low': float(c[3]), 'close': float(c[4]), 'volume': float(c[5])})
        return {'ok': True, 'symbol': symbol.upper(), 'interval': interval, 'count': len(candles), 'candles': candles}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'symbol': symbol.upper(), 'interval': interval, 'candles': []}


@bm.app.get('/api/aster/funding')
def aster_funding(symbol: str | None=Query(None, description='Символ или пусто для всех')) -> dict:
    """Mark price и funding rate AsterDEX."""
    try:
        data = bm._aster_public.premium_index(symbol=symbol)
        rows = [{'symbol': f.symbol, 'mark_price': f.mark_price, 'index_price': f.index_price, 'last_funding_rate': f.last_funding_rate, 'next_funding_time': f.next_funding_time} for f in data]
        return {'ok': True, 'count': len(rows), 'funding': rows}
    except bm.AsterError as e:
        return {'ok': False, 'error': str(e), 'count': 0, 'funding': []}


@bm.app.get('/api/aster/cross-spread')
def aster_cross_spread(symbol: str=Query(..., min_length=3, max_length=40, description='Символ (BTCUSDT)')) -> dict:
    """
    Межбиржевой спред MEXC ↔ AsterDEX для одного символа.
    Сравнивает лучшие bid/ask на обеих площадках.
    """
    sym = symbol.strip().upper()
    result: dict[str, Any] = {'ok': True, 'symbol': sym, 'mexc': None, 'aster': None, 'cross_spread': None}
    try:
        aster_tickers = bm._aster_public.book_ticker(symbol=sym)
        if aster_tickers:
            t = aster_tickers[0]
            result['aster'] = {'bid': t.bid_price, 'ask': t.ask_price, 'bid_qty': t.bid_qty, 'ask_qty': t.ask_qty, 'mid': (t.bid_price + t.ask_price) / 2, 'spread_bps': 10000 * (t.ask_price - t.bid_price) / ((t.bid_price + t.ask_price) / 2) if t.bid_price + t.ask_price > 0 else None}
    except bm.AsterError as e:
        result['aster_error'] = str(e)
    from mexc_monitor.spread_buffer import get_latest as sb_latest
    mexc_tick = sb_latest(sym)
    if mexc_tick:
        result['mexc'] = {'bid': mexc_tick.bid, 'ask': mexc_tick.ask, 'bid_qty': mexc_tick.bid_qty, 'ask_qty': mexc_tick.ask_qty, 'mid': mexc_tick.mid, 'spread_bps': mexc_tick.spread_bps}
    else:
        from mexc_monitor.spread_buffer import get_latest as sb_latest2
        fut_sym = sym.replace('USDT', '_USDT') if 'USDT' in sym and '_' not in sym else sym
        mexc_tick2 = sb_latest2(fut_sym)
        if mexc_tick2:
            result['mexc'] = {'bid': mexc_tick2.bid, 'ask': mexc_tick2.ask, 'bid_qty': mexc_tick2.bid_qty, 'ask_qty': mexc_tick2.ask_qty, 'mid': mexc_tick2.mid, 'spread_bps': mexc_tick2.spread_bps}
    if result.get('mexc') and result.get('aster'):
        mexc_data = result['mexc']
        aster_data = result['aster']
        mexc_mid = mexc_data['mid']
        aster_mid = aster_data['mid']
        basis_abs = aster_mid - mexc_mid
        basis_bps = 10000 * basis_abs / mexc_mid if mexc_mid > 0 else None
        buy_mexc_sell_aster = aster_data['bid'] - mexc_data['ask']
        buy_aster_sell_mexc = mexc_data['bid'] - aster_data['ask']
        result['cross_spread'] = {'basis_abs': basis_abs, 'basis_bps': basis_bps, 'buy_mexc_sell_aster_abs': buy_mexc_sell_aster, 'buy_mexc_sell_aster_bps': 10000 * buy_mexc_sell_aster / mexc_mid if mexc_mid > 0 else None, 'buy_aster_sell_mexc_abs': buy_aster_sell_mexc, 'buy_aster_sell_mexc_bps': 10000 * buy_aster_sell_mexc / aster_mid if aster_mid > 0 else None}
    return result


@bm.app.get('/api/aster/account')
def aster_account(_: None=Depends(bm._require_admin_token)) -> dict:
    """Информация об аккаунте AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            data = client.get_account()
        return {'ok': True, 'account': data}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e)}


@bm.app.get('/api/aster/positions')
def aster_positions(symbol: str | None=Query(None), _: None=Depends(bm._require_admin_token)) -> dict:
    """Открытые позиции на AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            positions = client.get_positions(symbol=symbol)
        active = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        return {'ok': True, 'positions': active, 'count': len(active)}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e), 'positions': [], 'count': 0}


@bm.app.get('/api/aster/open-orders')
def aster_open_orders(symbol: str | None=Query(None), _: None=Depends(bm._require_admin_token)) -> dict:
    """Открытые ордера на AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            orders = client.get_open_orders(symbol=symbol)
        return {'ok': True, 'orders': orders, 'count': len(orders)}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e), 'orders': [], 'count': 0}


@bm.app.post('/api/aster/order')
def aster_place_order(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Размещение ордера на AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    symbol = str(payload.get('symbol', '')).strip().upper()
    side = str(payload.get('side', '')).strip().upper()
    order_type = str(payload.get('type', 'LIMIT')).strip().upper()
    quantity = float(payload.get('quantity', 0))
    price = payload.get('price')
    time_in_force = str(payload.get('timeInForce', 'GTC')).strip().upper()
    reduce_only = bool(payload.get('reduceOnly', False))
    client_order_id = payload.get('newClientOrderId')
    if not symbol or not side or quantity <= 0:
        return {'ok': False, 'error': 'symbol, side, quantity are required'}
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            result = client.place_order(symbol=symbol, side=side, order_type=order_type, quantity=quantity, price=float(price) if price else None, time_in_force=time_in_force, reduce_only=reduce_only, client_order_id=client_order_id)
        return {'ok': True, 'order': result}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e)}


@bm.app.delete('/api/aster/order')
def aster_cancel_order(symbol: str=Query(..., min_length=3), orderId: int | None=Query(None), origClientOrderId: str | None=Query(None), _: None=Depends(bm._require_admin_token)) -> dict:
    """Отмена ордера на AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            result = client.cancel_order(symbol=symbol, order_id=orderId, client_order_id=origClientOrderId)
        return {'ok': True, 'result': result}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e)}


@bm.app.post('/api/aster/leverage')
def aster_set_leverage(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Установить плечо на AsterDEX."""
    if not bm._ASTER_API_KEY or not bm._ASTER_API_SECRET:
        return {'ok': False, 'error': 'ASTER_API_KEY/ASTER_API_SECRET not configured'}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    symbol = str(payload.get('symbol', '')).strip().upper()
    leverage = int(payload.get('leverage', 1))
    if not symbol:
        return {'ok': False, 'error': 'symbol is required'}
    try:
        with AsterPrivateClient(api_key=bm._ASTER_API_KEY, api_secret=bm._ASTER_API_SECRET) as client:
            result = client.set_leverage(symbol, leverage)
        return {'ok': True, 'result': result}
    except AsterPrivateApiError as e:
        return {'ok': False, 'error': str(e)}


@bm.app.get('/api/aster/ws/status')
def aster_ws_status() -> dict:
    """Статус WebSocket-подключения к AsterDEX."""
    client = bm.get_aster_ws_client()
    if client is None:
        return {'ok': True, 'connected': False, 'subscribed_symbols': [], 'count': 0}
    return {'ok': True, 'connected': client.connected, 'subscribed_symbols': client.get_subscribed_symbols(), 'count': len(client.get_subscribed_symbols())}


@bm.app.post('/api/aster/ws/subscribe')
def aster_ws_subscribe(symbol: str=Query(..., min_length=2, max_length=40), _: None=Depends(bm._require_admin_token)) -> dict:
    """Подписаться на bookTicker AsterDEX для символа."""
    sym = symbol.strip().upper()
    client = bm.get_aster_ws_client()
    if client is None:
        client = bm.ensure_aster_ws_started(symbols=[sym])
    else:
        client.subscribe(sym)
    return {'ok': True, 'symbol': sym, 'subscribed_symbols': client.get_subscribed_symbols()}


@bm.app.post('/api/aster/ws/unsubscribe')
def aster_ws_unsubscribe(symbol: str=Query(..., min_length=2, max_length=40), _: None=Depends(bm._require_admin_token)) -> dict:
    """Отписаться от bookTicker AsterDEX."""
    sym = symbol.strip().upper()
    client = bm.get_aster_ws_client()
    if client is None:
        return {'ok': True, 'symbol': sym, 'subscribed_symbols': []}
    client.unsubscribe(sym)
    return {'ok': True, 'symbol': sym, 'subscribed_symbols': client.get_subscribed_symbols()}
