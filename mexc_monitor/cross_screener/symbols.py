"""Cross-exchange symbol resolution against the shared spread_buffer.

Buffer key conventions:
  - MEXC spot:    plain symbol, e.g. ``BTCUSDT``
  - MEXC futures: underscore form, e.g. ``BTC_USDT``
  - AsterDEX:     ``ASTER:`` prefix, e.g. ``ASTER:BTCUSDT``
  - other venues: ``<EXCHANGE>:`` uppercase prefix when they join the buffer
"""

from __future__ import annotations

from mexc_monitor.spread_buffer import SpreadTick, get_latest, get_tracked_symbols

EXCHANGE_PREFIX: dict[str, str] = {
    "mexc": "",
    "aster": "ASTER:",
}


def prefix_for(exchange: str) -> str:
    return EXCHANGE_PREFIX.get(exchange.lower(), f"{exchange.upper()}:")


def buffer_bases(exchange: str) -> list[str]:
    """Base symbols that currently have ticks for *exchange* in the buffer."""
    prefix = prefix_for(exchange)
    if prefix:
        return [
            s[len(prefix):]
            for s in get_tracked_symbols()
            if s.startswith(prefix) and not s.startswith("CROSS:")
        ]
    return [
        s
        for s in get_tracked_symbols()
        if ":" not in s
    ]


def resolve_tick(base: str, exchange: str) -> tuple[str, SpreadTick] | tuple[None, None]:
    """Find the live tick for *base* on *exchange*.

    For MEXC both the spot (``BTCUSDT``) and futures (``BTC_USDT``) buffer
    keys are tried. Returns ``(buffer_key, tick)`` or ``(None, None)``.
    """
    prefix = prefix_for(exchange)
    candidates = [f"{prefix}{base}"]
    if exchange.lower() == "mexc" and "_" not in base and "USDT" in base:
        candidates.append(base.replace("USDT", "_USDT"))
    for key in candidates:
        tick = get_latest(key)
        if tick is not None:
            return key, tick
    return None, None


def cross_pairs(exchange_a: str, exchange_b: str) -> list[tuple[str, SpreadTick, SpreadTick]]:
    """All (base, tick_a, tick_b) triples with live ticks on both venues."""
    out: list[tuple[str, SpreadTick, SpreadTick]] = []
    for base in buffer_bases(exchange_b):
        _, tick_b = resolve_tick(base, exchange_b)
        if tick_b is None:
            continue
        _, tick_a = resolve_tick(base, exchange_a)
        if tick_a is None:
            continue
        out.append((base, tick_a, tick_b))
    return out
