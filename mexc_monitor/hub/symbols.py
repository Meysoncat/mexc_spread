"""Нормализация биржевых символов к общему базовому активу.

Разные биржи называют один и тот же perp по-разному: ``BTCUSDT``,
``BTC-USDT-SWAP``, ``BTC_USDT``, ``BTC-USD``, ``BTC``. Для сравнения цен между
площадками нужен общий ключ — базовый актив (``BTC``) — и множитель цены:
``1000PEPEUSDT`` на Binance котируется за 1000 PEPE, ``kPEPE`` на Hyperliquid —
тоже за 1000. Цена за одну монету = цена инструмента / ``price_mult``.
"""

from __future__ import annotations

import re

QUOTES: tuple[str, ...] = ("USDT", "USDC", "USD")

# Порядок важен: длинные префиксы раньше коротких.
_MULT_PREFIXES: tuple[tuple[str, float], ...] = (
    ("1000000", 1_000_000.0),
    ("100000", 100_000.0),
    ("10000", 10_000.0),
    ("1000", 1_000.0),
    ("1M", 1_000_000.0),
)

_SEP_RE = re.compile(r"[-_/:]")


def norm_key(raw: str) -> str:
    """Ключ инструмента внутри биржи: без разделителей и суффикса SWAP/PERP.

    ``BTC-USDT-SWAP`` → ``BTCUSDT``, ``BTC_USDT`` → ``BTCUSDT``,
    ``btcusdt`` → ``BTCUSDT``. Hyperliquid-префикс ``k`` сохраняется
    (``kPEPE``), потому что его регистр несёт смысл.
    """
    s = raw.strip()
    for suffix in ("-SWAP", "_SWAP", "-PERP", "_PERP"):
        if s.upper().endswith(suffix):
            s = s[: -len(suffix)]
    s = _SEP_RE.sub("", s)
    if s.startswith("k") and len(s) > 1 and s[1:].isupper():
        return s
    return s.upper()


def split_quote(key: str, *, default_quote: str | None = None) -> tuple[str, str] | None:
    """``BTCUSDT`` → (``BTC``, ``USDT``). Без известного суффикса — default_quote."""
    for q in QUOTES:
        if key.endswith(q) and len(key) > len(q):
            return key[: -len(q)], q
    if default_quote is not None and key:
        return key, default_quote
    return None


def split_multiplier(base: str) -> tuple[str, float]:
    """``1000PEPE`` → (``PEPE``, 1000), ``kPEPE`` → (``PEPE``, 1000).

    Активы, у которых цифры — часть имени (``1INCH``), не трогаем: префикс
    должен быть ровно одним из известных множителей и за ним — буква.
    """
    if base.startswith("k") and len(base) > 1 and base[1:].isupper():
        return base[1:], 1_000.0
    for prefix, mult in _MULT_PREFIXES:
        rest = base[len(prefix):]
        if base.startswith(prefix) and rest and rest[0].isalpha():
            return rest, mult
    return base, 1.0


def parse_symbol(raw: str, *, default_quote: str | None = None) -> tuple[str, str, float] | None:
    """Сырой символ биржи → (base, quote, price_mult) или None."""
    key = norm_key(raw)
    parts = split_quote(key, default_quote=default_quote)
    if parts is None:
        return None
    base, quote = parts
    base, mult = split_multiplier(base)
    if not base:
        return None
    return base, quote, mult
