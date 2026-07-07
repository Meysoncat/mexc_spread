from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from mexc_monitor.filters import apply_market_filters
from mexc_monitor.history_worker import start_history_worker
from mexc_monitor.pipeline import safe_load_snapshot

start_history_worker()

st.set_page_config(page_title="MEXC Spread Monitor", layout="wide")
st.title("MEXC — bid/ask, спред и базис (спот / фьючерсы / базис)")


def _sidebar_api_note(market: str) -> str:
    if market == "futures":
        return (
            "Фьючерсы: один запрос GET contract.mexc.com/api/v1/contract/ticker "
            "(bid1/ask1, volume24, amount24, funding). Лимиты — по документации MEXC Futures."
        )
    if market == "cross":
        return (
            "Базис: два снимка (спот + фьючерсы), сопоставление BTCUSDT ↔ BTC_USDT. "
            "Метрики: fut_mid − spot_mid и bps к спот-mid."
        )
    return (
        "Спот: bookTicker (вес 10) + ticker/24hr (вес 25) за одно обновление, один HTTP-клиент. "
        "Укладывайтесь в лимиты IP биржи."
    )


def _fetch_and_store() -> None:
    market = st.session_state.get("flt_market", "spot")
    df, err = safe_load_snapshot(market=market)
    st.session_state["snapshot_df"] = df
    st.session_state["snapshot_error"] = err
    st.session_state["_snapshot_for_market"] = market
    if df is not None and not df.empty and "observed_at" in df.columns:
        oa = df["observed_at"].iloc[0]
        if (
            oa is not None
            and str(oa).strip()
            and not (isinstance(oa, float) and pd.isna(oa))
        ):
            st.session_state["snapshot_loaded_at"] = str(oa)
        else:
            st.session_state["snapshot_loaded_at"] = datetime.now(timezone.utc).isoformat()
    elif df is not None:
        st.session_state["snapshot_loaded_at"] = datetime.now(timezone.utc).isoformat()


def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    m = st.session_state.get("flt_market", "spot")
    mb = ma = 0.0
    if m != "cross":
        mb = float(st.session_state.get("flt_min_bid_l1_nq") or 0.0)
        ma = float(st.session_state.get("flt_min_ask_l1_nq") or 0.0)
    return apply_market_filters(
        df,
        market=m,
        quote_raw=st.session_state.get("flt_quote") or "",
        min_spread_bps=float(st.session_state.get("flt_min_bps") or 0.0),
        min_vol_quote=float(st.session_state.get("flt_min_vol_quote") or 0.0),
        search=(st.session_state.get("flt_search") or "").strip().upper(),
        sort_by=st.session_state.get("flt_sort") or "spread_bps",
        ascending=bool(st.session_state.get("flt_asc", False)),
        min_bid_l1_notional_quote=mb,
        min_ask_l1_notional_quote=ma,
    )


def _render_main_block() -> None:
    err = st.session_state.get("snapshot_error")
    df = st.session_state.get("snapshot_df")
    market = st.session_state.get("flt_market", "spot")

    if err:
        st.error(err)
        return
    if df is None or df.empty:
        st.warning("Нет данных.")
        return

    loaded = st.session_state.get("snapshot_loaded_at")
    if loaded is not None:
        st.caption(f"Последнее обновление (UTC): {loaded}")

    view = _apply_filters(df)
    total_pairs = len(df)
    shown = len(view)
    pair_hint = (
        "BTCUSDT ↔ BTC_USDT"
        if market == "cross"
        else ("BTCUSDT" if market == "spot" else "BTC_USDT")
    )
    st.metric(
        "Показано торговых пар",
        shown,
        delta=None if shown == total_pairs else f"из {total_pairs} в снимке",
        help="Число строк после фильтров. Одна строка = одна пара, например "
        f"{pair_hint}. Не год и не курс.",
    )

    if market == "cross":
        display_cols = [
            "symbol_spot",
            "symbol_futures",
            "basis_mid_bps",
            "basis_mid_abs",
            "spot_mid",
            "fut_mid",
            "spot_spread_bps",
            "fut_spread_bps",
            "volume_24h_quote_spot",
            "volume_24h_quote_fut",
            "funding_rate",
            "observed_at",
        ]
    else:
        display_cols = [
            "symbol",
            "bid",
            "ask",
            "spread_abs",
            "spread_bps",
            "net_spread_bps",
            "fee_round_trip_bps",
            "mid",
            "l1_max_executable_base",
            "l1_max_notional_quote",
            "l1_covers_reference_notional",
            "volume_24h_base",
            "volume_24h_quote",
            "funding_rate",
            "bid_qty",
            "ask_qty",
            "observed_at",
        ]
    display_cols = [c for c in display_cols if c in view.columns]

    vol_base_label = "Объём 24h (база)" if market == "spot" else "Объём 24h (контракты)"
    vol_quote_label = "Оборот 24h (amount24)" if market == "futures" else "Объём 24h (котировка)"

    c1, _c2 = st.columns([1, 4])
    with c1:
        csv_name = (
            "mexc_cross_basis.csv" if market == "cross" else f"mexc_{market}_spread.csv"
        )
        csv_bytes = view[display_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="Скачать CSV",
            data=csv_bytes,
            file_name=csv_name,
            mime="text/csv",
            disabled=len(view) == 0,
        )

    if market == "cross":
        col_cfg = {
            "symbol_spot": st.column_config.TextColumn("Спот"),
            "symbol_futures": st.column_config.TextColumn("Фьючерс"),
            "basis_mid_bps": st.column_config.NumberColumn("Базис (bps)", format="%.2f"),
            "basis_mid_abs": st.column_config.NumberColumn("Базис (abs)", format="%.8f"),
            "spot_mid": st.column_config.NumberColumn("Mid спот", format="%.8f"),
            "fut_mid": st.column_config.NumberColumn("Mid фьюч", format="%.8f"),
            "spot_spread_bps": st.column_config.NumberColumn("Спред спот (bps)", format="%.2f"),
            "fut_spread_bps": st.column_config.NumberColumn("Спред фьюч (bps)", format="%.2f"),
            "volume_24h_quote_spot": st.column_config.NumberColumn("Объём 24h (спот, кот.)", format="%.2f"),
            "volume_24h_quote_fut": st.column_config.NumberColumn("Оборот 24h (фьюч)", format="%.2f"),
            "funding_rate": st.column_config.NumberColumn("Funding", format="%.6f"),
            "observed_at": st.column_config.TextColumn("observed_at (UTC)"),
        }
    else:
        col_cfg = {
            "symbol": st.column_config.TextColumn("Символ"),
            "bid": st.column_config.NumberColumn("Bid", format="%.8f"),
            "ask": st.column_config.NumberColumn("Ask", format="%.8f"),
            "spread_abs": st.column_config.NumberColumn("Спред (абс.)", format="%.8f"),
            "spread_bps": st.column_config.NumberColumn("Спред (bps)", format="%.2f"),
            "net_spread_bps": st.column_config.NumberColumn("Чистый спред (bps)", format="%.2f"),
            "fee_round_trip_bps": st.column_config.NumberColumn("Комиссия RT (bps)", format="%.2f"),
            "mid": st.column_config.NumberColumn("Mid", format="%.8f"),
            "l1_max_executable_base": st.column_config.NumberColumn("L1 max база", format="%.6f"),
            "l1_max_notional_quote": st.column_config.NumberColumn("L1 max USDT≈", format="%.2f"),
            "l1_covers_reference_notional": st.column_config.CheckboxColumn("L1 ≥ ref"),
            "volume_24h_base": st.column_config.NumberColumn(vol_base_label, format="%.4f"),
            "volume_24h_quote": st.column_config.NumberColumn(vol_quote_label, format="%.2f"),
            "funding_rate": st.column_config.NumberColumn("Funding", format="%.6f"),
            "bid_qty": st.column_config.NumberColumn("Bid qty", format="%.6f"),
            "ask_qty": st.column_config.NumberColumn("Ask qty", format="%.6f"),
            "observed_at": st.column_config.TextColumn("observed_at (UTC)"),
        }
    col_cfg = {k: v for k, v in col_cfg.items() if k in display_cols}

    st.dataframe(
        view[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
    )


with st.sidebar:
    st.markdown("**Рынок**")
    if "flt_market" not in st.session_state:
        st.session_state["flt_market"] = "spot"

    c_spot, c_fut = st.columns(2)
    with c_spot:
        if st.button(
            "Спот",
            key="mk_spot",
            type="primary" if market_now == "spot" else "secondary",
            use_container_width=True,
        ):
            st.session_state["flt_market"] = "spot"
    with c_fut:
        if st.button(
            "Фьючерсы",
            key="mk_fut",
            type="primary" if market_now == "futures" else "secondary",
            use_container_width=True,
        ):
            st.session_state["flt_market"] = "futures"
    if st.button(
        "Базис (спот ↔ перп)",
        key="mk_cross",
        type="primary" if market_now == "cross" else "secondary",
        use_container_width=True,
    ):
        st.session_state["flt_market"] = "cross"

    market_now = st.session_state.get("flt_market", "spot")
    st.caption(_sidebar_api_note(market_now))

    snap_m = st.session_state.get("_snapshot_for_market")
    if snap_m is not None and snap_m != market_now:
        for k in ("snapshot_df", "snapshot_error", "snapshot_loaded_at", "_last_fetch_mono"):
            st.session_state.pop(k, None)
        st.session_state.pop("_snapshot_for_market", None)
        st.session_state["flt_force_refresh"] = True
        if market_now == "futures":
            st.session_state["flt_quote"] = "_USDT"
            st.session_state["flt_sort"] = "spread_bps"
        elif market_now == "cross":
            st.session_state["flt_quote"] = "USDT"
            st.session_state["flt_sort"] = "basis_mid_bps"
        else:
            st.session_state["flt_quote"] = "USDT"
            st.session_state["flt_sort"] = "spread_bps"

    if "flt_quote" not in st.session_state:
        st.session_state["flt_quote"] = "_USDT" if market_now == "futures" else "USDT"

    st.header("Фильтры")
    st.text_input(
        "Окончание символа (котировка)",
        key="flt_quote",
        help=(
            "Спот / базис: USDT → пары *USDT (спот-нога). "
            "Фьючерсы: ввод USDT даёт суффикс _USDT (как в BTC_USDT)."
        ),
    )
    st.number_input(
        "Мин. спред / |базис| (bps), 0 = без отсечения",
        min_value=0.0,
        value=0.0,
        step=0.1,
        format="%.2f",
        key="flt_min_bps",
        help="Для «Базис» — минимум модуля basis_mid_bps; для спот/фьюч — gross spread_bps.",
    )
    st.number_input(
        "Мин. объём 24h (в котировке / amount24), 0 = без отсечения",
        min_value=0.0,
        value=0.0,
        step=1000.0,
        format="%.0f",
        key="flt_min_vol_quote",
    )
    if market_now != "cross":
        st.subheader("Быстрый поиск спреда (L1)")
        st.caption(
            "Пары с широким спредом и ненулевой «плотностью» на лучшем bid/ask "
            "(из bookTicker: bidQty×bidPrice и askQty×askPrice, ≈ USDT для USDT-пар)."
        )
        st.number_input(
            "Мин. нотация L1 bid (USDT≈), 0 = выкл",
            min_value=0.0,
            value=0.0,
            step=50.0,
            format="%.0f",
            key="flt_min_bid_l1_nq",
        )
        st.number_input(
            "Мин. нотация L1 ask (USDT≈), 0 = выкл",
            min_value=0.0,
            value=0.0,
            step=50.0,
            format="%.0f",
            key="flt_min_ask_l1_nq",
        )
    st.text_input("Поиск по символу (подстрока)", value="", key="flt_search")

    st.header("Сортировка")
    _sort_opts = (
        [
            "basis_mid_bps",
            "basis_mid_abs",
            "spot_mid",
            "fut_mid",
            "spot_spread_bps",
            "fut_spread_bps",
            "volume_24h_quote_spot",
            "volume_24h_quote_fut",
            "funding_rate",
            "symbol_spot",
            "symbol_futures",
            "observed_at",
        ]
        if market_now == "cross"
        else [
            "spread_bps",
            "net_spread_bps",
            "spread_abs",
            "l1_max_notional_quote",
            "volume_24h_quote",
            "volume_24h_base",
            "funding_rate",
            "symbol",
            "mid",
            "bid",
            "ask",
            "observed_at",
        ]
    )
    _cur_sort = st.session_state.get("flt_sort")
    if _cur_sort not in _sort_opts:
        st.session_state["flt_sort"] = _sort_opts[0]
    st.selectbox("Колонка", options=_sort_opts, key="flt_sort")
    st.checkbox("По возрастанию", value=False, key="flt_asc")

    st.header("Обновление")
    st.toggle("Автообновление", value=False, key="flt_auto")
    st.slider(
        "Интервал (с)",
        min_value=10,
        max_value=120,
        value=20,
        step=5,
        key="flt_refresh_sec",
        disabled=not st.session_state.get("flt_auto", False),
    )
    if st.button("Обновить сейчас", type="primary"):
        st.session_state.pop("snapshot_df", None)
        st.session_state.pop("snapshot_error", None)
        st.session_state.pop("snapshot_loaded_at", None)
        st.session_state.pop("_snapshot_for_market", None)
        st.session_state["flt_force_refresh"] = True


auto_on = bool(st.session_state.get("flt_auto", False))
refresh_sec = int(st.session_state.get("flt_refresh_sec") or 20)

if not auto_on:
    if st.session_state.pop("flt_force_refresh", False) or "snapshot_df" not in st.session_state:
        _fetch_and_store()
    _render_main_block()
else:

    @st.fragment(run_every=timedelta(seconds=refresh_sec))
    def _auto_refresh_panel() -> None:
        now = time.monotonic()
        force = bool(st.session_state.pop("flt_force_refresh", False))
        last = st.session_state.get("_last_fetch_mono")
        stale = st.session_state.get("snapshot_df") is None
        interval_ok = last is None or (now - last) >= float(refresh_sec) * 0.95
        if force or stale or interval_ok:
            _fetch_and_store()
            st.session_state["_last_fetch_mono"] = now
        _render_main_block()

    _auto_refresh_panel()
