from __future__ import annotations

import pandas as pd


def quote_suffix_for_filter(market: str, raw: str) -> str:
    s = raw.strip().upper()
    if not s:
        return "USDT" if market == "spot" else "_USDT"
    if market == "futures" and not s.startswith("_"):
        return f"_{s}"
    return s


def apply_cross_market_filters(
    df: pd.DataFrame,
    *,
    quote_raw: str,
    min_basis_bps: float,
    min_vol_quote: float,
    search: str,
    sort_by: str,
    ascending: bool,
) -> pd.DataFrame:
    """Фильтры для снимка спот↔фьючерс: |basis_mid_bps|, объёмы на обеих ногах."""
    quote_suffix = quote_suffix_for_filter("spot", quote_raw)
    view = df.copy()
    if quote_suffix:
        view = view[view["symbol_spot"].str.endswith(quote_suffix, na=False)]
    if min_basis_bps > 0:
        view = view[view["basis_mid_bps"].fillna(0).abs() >= min_basis_bps]
    if min_vol_quote > 0:
        view = view[
            (view["volume_24h_quote_spot"].fillna(0) >= min_vol_quote)
            & (view["volume_24h_quote_fut"].fillna(0) >= min_vol_quote)
        ]
    if search:
        su = search.strip().upper()
        view = view[
            view["symbol_spot"].str.contains(su, na=False)
            | view["symbol_futures"].str.contains(su, na=False)
        ]
    sort_col = (
        sort_by
        if sort_by in view.columns
        else (
            "basis_mid_bps"
            if "basis_mid_bps" in view.columns
            else ("symbol_spot" if "symbol_spot" in view.columns else sort_by)
        )
    )
    if sort_col in view.columns:
        na_pos = "last" if ascending else "first"
        view = view.sort_values(by=sort_col, ascending=ascending, na_position=na_pos)
    return view


def apply_market_filters(
    df: pd.DataFrame,
    *,
    market: str,
    quote_raw: str,
    min_spread_bps: float,
    min_vol_quote: float,
    search: str,
    sort_by: str,
    ascending: bool,
    min_bid_l1_notional_quote: float = 0.0,
    min_ask_l1_notional_quote: float = 0.0,
) -> pd.DataFrame:
    """Те же правила, что в Streamlit UI (фильтрация и сортировка локально по снимку)."""
    if market == "cross":
        return apply_cross_market_filters(
            df,
            quote_raw=quote_raw,
            min_basis_bps=min_spread_bps,
            min_vol_quote=min_vol_quote,
            search=search,
            sort_by=sort_by,
            ascending=ascending,
        )
    quote_suffix = quote_suffix_for_filter(market, quote_raw)
    view = df.copy()
    if quote_suffix:
        view = view[view["symbol"].str.endswith(quote_suffix, na=False)]
    if min_spread_bps > 0:
        view = view[view["spread_bps"].fillna(0) >= min_spread_bps]
    if min_vol_quote > 0:
        view = view[view["volume_24h_quote"].fillna(0) >= min_vol_quote]
    if min_bid_l1_notional_quote > 0 and "bid_qty" in view.columns and "bid" in view.columns:
        bn = view["bid_qty"].fillna(0) * view["bid"].fillna(0)
        view = view[bn >= min_bid_l1_notional_quote]
    if min_ask_l1_notional_quote > 0 and "ask_qty" in view.columns and "ask" in view.columns:
        an = view["ask_qty"].fillna(0) * view["ask"].fillna(0)
        view = view[an >= min_ask_l1_notional_quote]
    if search:
        view = view[view["symbol"].str.contains(search.strip().upper(), na=False)]

    sort_col = (
        sort_by
        if sort_by in view.columns
        else (
            "spread_bps"
            if "spread_bps" in view.columns
            else ("symbol" if "symbol" in view.columns else sort_by)
        )
    )
    if sort_col in view.columns:
        na_pos = "last" if ascending else "first"
        view = view.sort_values(by=sort_col, ascending=ascending, na_position=na_pos)
    return view
