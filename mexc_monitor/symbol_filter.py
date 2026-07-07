from __future__ import annotations

from typing import Literal

from mexc_monitor.config import Settings
from mexc_monitor.models import BookTickerRow

MarketId = Literal["spot", "futures"]


def _norm_spot(sym: str) -> str:
    return sym.strip().upper()


def _norm_futures(sym: str) -> str:
    s = sym.strip().upper()
    if "_" not in s:
        return s
    return "_".join(p for p in s.split("_") if p)


def filter_rows_by_universe(
    rows: list[BookTickerRow],
    market: MarketId,
    settings: Settings,
) -> list[BookTickerRow]:
    """
    Whitelist: если непустой — только эти символы (после нормализации).
    Blacklist: всегда исключаются.
    Пустой whitelist = без ограничения по списку (все с биржи после blacklist).
    """
    if market == "spot":
        wl, bl = settings.spot_symbols_whitelist, settings.spot_symbols_blacklist
        norm = _norm_spot
    else:
        wl, bl = settings.futures_symbols_whitelist, settings.futures_symbols_blacklist
        norm = _norm_futures

    wl_set = {norm(x) for x in wl} if wl else None
    bl_set = {norm(x) for x in bl}

    out: list[BookTickerRow] = []
    for r in rows:
        sym = norm(r.symbol)
        if wl_set is not None and sym not in wl_set:
            continue
        if sym in bl_set:
            continue
        out.append(r)
    return out
