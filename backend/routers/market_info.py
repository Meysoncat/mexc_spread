"""Routes: market_info (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Query

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/withdrawal-fees')
def withdrawal_fees() -> dict:
    """Комиссии на вывод токенов по сетям из конфига."""
    if not bm._WITHDRAWAL_FEES_PATH.exists():
        return {'ok': False, 'error': 'withdrawal_fees.json not found', 'tokens': {}}
    try:
        data = bm.json.loads(bm._WITHDRAWAL_FEES_PATH.read_text(encoding='utf-8'))
        return {'ok': True, **data}
    except (bm.json.JSONDecodeError, OSError) as e:
        return {'ok': False, 'error': str(e), 'tokens': {}}


@bm.app.get('/api/coin-networks')
def coin_networks(coins: str=Query('', description='Список монет через запятую: BTC,ETH,SOL'), force: bool=Query(False, description='Игнорировать кэш и запросить биржи заново')) -> dict:
    """Сети депозита/вывода по монетам с бирж с публичным currency-API.

    Поддерживаются только Gate.io и Bitget (у остальных данные о с��тях
    доступны лишь через подписанные эндпоинты). Ответ:
    ``{coins: {BTC: {gateio: [{network, deposit, withdraw}], ...}}}``.
    """
    from mexc_monitor.coin_networks import SUPPORTED_EXCHANGES, get_coin_networks
    coin_list = [c for c in coins.split(',') if c.strip()]
    if not coin_list:
        return {'ok': True, 'coins': {}, 'supported_exchanges': list(SUPPORTED_EXCHANGES)}
    try:
        data = get_coin_networks(coin_list, force=force)
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'coins': {}}
    return {'ok': True, 'coins': data, 'supported_exchanges': list(SUPPORTED_EXCHANGES)}


@bm.app.get('/api/withdrawal-fees/calculate')
def calculate_withdrawal_cost(token: str=Query('USDT', description='Токен: USDT, BTC, ETH, SOL, XRP'), src_exchange: str=Query('mexc', description='Биржа отправления'), dst_exchange: str=Query('binance', description='Биржа назначения'), network: str=Query('', description='Сеть (TRC20, BEP20, ERC20, ...)'), spread_bps: float=Query(0, description='Текущий спред в bps'), notional_usdt: float=Query(1000, description='Размер сделки в USDT')) -> dict:
    """Расчёт чистой прибыли после withdrawal fees."""
    if not bm._WITHDRAWAL_FEES_PATH.exists():
        return {'ok': False, 'error': 'withdrawal_fees.json not found'}
    try:
        cfg = bm.json.loads(bm._WITHDRAWAL_FEES_PATH.read_text(encoding='utf-8'))
    except (bm.json.JSONDecodeError, OSError) as e:
        return {'ok': False, 'error': str(e)}
    token = token.upper()
    src = src_exchange.lower()
    dst = dst_exchange.lower()
    net = network.upper() if network else ''
    token_data = cfg.get('tokens', {}).get(token, {})
    networks = token_data.get('networks', {})
    overrides = cfg.get('exchange_overrides', {})

    def _fee(exchange: str, net_name: str) -> float | None:
        ex_override = overrides.get(exchange, {}).get(token, {}).get(net_name)
        if ex_override is not None:
            return float(ex_override)
        net_data = networks.get(net_name, {})
        return float(net_data.get('fee', 0)) if net_data else None
    if not net:
        results = []
        for net_name in networks:
            src_fee = _fee(src, net_name)
            dst_fee = _fee(dst, net_name)
            if src_fee is not None and dst_fee is not None:
                total_fee = src_fee + dst_fee
                net_profit_bps = spread_bps - total_fee / notional_usdt * 10000 if notional_usdt > 0 else 0
                results.append({'network': net_name, 'src_fee_usdt': src_fee, 'dst_fee_usdt': dst_fee, 'total_fee_usdt': total_fee, 'net_profit_bps': round(net_profit_bps, 2), 'net_profit_usdt': round(spread_bps / 10000 * notional_usdt - total_fee, 4), 'eta_min': networks[net_name].get('eta_min', 0)})
        results.sort(key=lambda x: x['total_fee_usdt'])
        return {'ok': True, 'token': token, 'src_exchange': src_exchange, 'dst_exchange': dst_exchange, 'spread_bps': spread_bps, 'notional_usdt': notional_usdt, 'networks': results, 'best_network': results[0] if results else None}
    src_fee = _fee(src, net)
    dst_fee = _fee(dst, net)
    if src_fee is None or dst_fee is None:
        return {'ok': False, 'error': f'Network {net} not found for {token}'}
    total_fee = src_fee + dst_fee
    net_profit_bps = spread_bps - total_fee / notional_usdt * 10000 if notional_usdt > 0 else 0
    return {'ok': True, 'token': token, 'src_exchange': src_exchange, 'dst_exchange': dst_exchange, 'network': net, 'src_fee_usdt': src_fee, 'dst_fee_usdt': dst_fee, 'total_fee_usdt': total_fee, 'spread_bps': spread_bps, 'notional_usdt': notional_usdt, 'net_profit_bps': round(net_profit_bps, 2), 'net_profit_usdt': round(spread_bps / 10000 * notional_usdt - total_fee, 4), 'eta_min': networks.get(net, {}).get('eta_min', 0)}


@bm.app.get('/api/slippage-estimate')
def slippage_estimate(symbol: str=Query('BTCUSDT', description='Символ (BTCUSDT, ETH_USDT, ...)'), market: str=Query('spot', description='spot или futures'), exchange: str=Query('mexc', description='Биржа'), notional_usdt: float=Query(1000, description='Размер ордера в USDT'), side: str=Query('buy', description='buy или sell')) -> dict:
    """On-demand оценка проскальзывания по L2 стакану."""
    from mexc_monitor.orderbook import fetch_orderbook_depth
    try:
        depth = fetch_orderbook_depth(market, symbol, limit=50)
    except Exception as e:
        try:
            depth = bm._fetch_binance_depth(market, symbol, limit=50)
        except Exception:
            return {'ok': False, 'error': f'MEXC: {e}. Binance fallback failed.', 'symbol': symbol}
    levels = depth.get('asks' if side == 'buy' else 'bids', [])
    if not levels:
        return {'ok': False, 'error': 'No depth data', 'symbol': symbol}
    bids = depth.get('bids', [])
    asks = depth.get('asks', [])
    if not bids or not asks:
        return {'ok': False, 'error': 'Incomplete depth', 'symbol': symbol}
    best_bid = float(bids[0][0]) if isinstance(bids[0], (list, tuple)) else float(bids[0].get('price', 0))
    best_ask = float(asks[0][0]) if isinstance(asks[0], (list, tuple)) else float(asks[0].get('price', 0))
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0
    if side == 'buy':
        qty = notional_usdt / best_ask if best_ask > 0 else 0
        levels_normalized = [(float(l[0]) if isinstance(l, (list, tuple)) else float(l.get('price', 0)), float(l[1]) if isinstance(l, (list, tuple)) else float(l.get('qty', 0))) for l in asks]
    else:
        qty = notional_usdt / best_bid if best_bid > 0 else 0
        levels_normalized = [(float(l[0]) if isinstance(l, (list, tuple)) else float(l.get('price', 0)), float(l[1]) if isinstance(l, (list, tuple)) else float(l.get('qty', 0))) for l in bids]
    remaining = qty
    total_cost = 0.0
    total_filled = 0.0
    levels_consumed = 0
    for price, level_qty in levels_normalized:
        if remaining <= 0 or price <= 0:
            break
        fill = min(level_qty, remaining)
        total_cost += price * fill
        total_filled += fill
        remaining -= fill
        levels_consumed += 1
    if total_filled == 0:
        return {'ok': False, 'error': 'No fill possible', 'symbol': symbol}
    vwap_price = total_cost / total_filled
    if side == 'buy':
        slippage_bps = (vwap_price - best_ask) / mid * 10000 if mid > 0 else 0
    else:
        slippage_bps = (best_bid - vwap_price) / mid * 10000 if mid > 0 else 0
    filled_notional = total_cost
    unfilled_notional = remaining * (best_ask if side == 'buy' else best_bid)
    return {'ok': True, 'symbol': symbol, 'market': market, 'exchange': exchange, 'side': side, 'notional_usdt': notional_usdt, 'best_price': best_ask if side == 'buy' else best_bid, 'vwap_price': round(vwap_price, 8), 'slippage_bps': round(slippage_bps, 2), 'filled_notional_usdt': round(filled_notional, 2), 'unfilled_notional_usdt': round(unfilled_notional, 2), 'fully_filled': remaining <= 0, 'levels_consumed': levels_consumed, 'mid': round(mid, 8)}


@bm.app.get('/api/open-interest')
def open_interest(symbol: str=Query('BTCUSDT', description='Символ (BTCUSDT, ETHUSDT, ...)')) -> dict:
    """Open Interest для фьючерсного контракта. MEXC с fallback на Binance."""
    import httpx
    sym = symbol.strip().upper().replace('/', '_').replace('-', '_')
    if '_' not in sym:
        sym_usdt = sym + '_USDT'
    else:
        sym_usdt = sym
    mexc_url = f'https://api.mexc.com/api/v1/contract/open_interest/{sym_usdt}'
    try:
        r = httpx.get(mexc_url, timeout=8)
        r.raise_for_status()
        data = r.json()
        return {'ok': True, 'symbol': symbol.strip().upper(), 'open_interest': data.get('openInterest', data.get('holdVol', 0)), 'currency': data.get('currency', 'USDT'), 'source': 'mexc'}
    except Exception:
        pass
    binance_sym = sym.replace('_USDT', 'USDT').replace('_', '')
    binance_url = 'https://fapi.binance.com/fapi/v1/openInterest'
    try:
        r = httpx.get(binance_url, params={'symbol': binance_sym}, timeout=8)
        r.raise_for_status()
        data = r.json()
        return {'ok': True, 'symbol': symbol.strip().upper(), 'open_interest': float(data.get('openInterest', 0)), 'currency': 'USDT', 'source': 'binance_fallback'}
    except Exception as e:
        return {'ok': False, 'error': f'MEXC + Binance failed: {e}', 'symbol': symbol.strip().upper()}
