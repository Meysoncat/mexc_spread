"""Unit tests for mexc_monitor.trading.exchanges module."""

from __future__ import annotations

import pytest

from mexc_monitor.trading.exchanges import (
    EngineKey,
    Exchange,
    Market,
    OrderSide,
    OrderType,
)


class TestExchangeEnum:
    def test_all_exchanges_defined(self):
        expected = {"mexc", "binance", "bybit", "okx", "gateio", "htx", "bitget"}
        assert {e.value for e in Exchange} == expected

    def test_exchange_is_str_enum(self):
        assert Exchange.MEXC == "mexc"
        assert isinstance(Exchange.MEXC, str)

    def test_exchange_from_value(self):
        assert Exchange("binance") is Exchange.BINANCE


class TestMarketEnum:
    def test_all_markets_defined(self):
        assert {m.value for m in Market} == {"spot", "futures"}

    def test_market_is_str_enum(self):
        assert Market.SPOT == "spot"
        assert isinstance(Market.SPOT, str)


class TestOrderTypeEnum:
    def test_all_order_types_defined(self):
        assert {o.value for o in OrderType} == {"LIMIT", "MARKET"}

    def test_order_type_is_str_enum(self):
        assert OrderType.LIMIT == "LIMIT"
        assert isinstance(OrderType.LIMIT, str)


class TestOrderSideEnum:
    def test_all_order_sides_defined(self):
        assert {o.value for o in OrderSide} == {"BUY", "SELL"}

    def test_order_side_is_str_enum(self):
        assert OrderSide.BUY == "BUY"
        assert isinstance(OrderSide.BUY, str)


class TestEngineKey:
    def test_creation(self):
        key = EngineKey(exchange=Exchange.MEXC, market=Market.SPOT)
        assert key.exchange is Exchange.MEXC
        assert key.market is Market.SPOT

    def test_str_representation(self):
        key = EngineKey(exchange=Exchange.BINANCE, market=Market.FUTURES)
        assert str(key) == "binance:futures"

    def test_frozen_immutability(self):
        key = EngineKey(exchange=Exchange.OKX, market=Market.SPOT)
        with pytest.raises(Exception):
            key.exchange = Exchange.MEXC  # type: ignore[misc]

    def test_equality(self):
        key1 = EngineKey(Exchange.BYBIT, Market.SPOT)
        key2 = EngineKey(Exchange.BYBIT, Market.SPOT)
        assert key1 == key2

    def test_inequality_different_exchange(self):
        key1 = EngineKey(Exchange.BYBIT, Market.SPOT)
        key2 = EngineKey(Exchange.HTX, Market.SPOT)
        assert key1 != key2

    def test_inequality_different_market(self):
        key1 = EngineKey(Exchange.GATEIO, Market.SPOT)
        key2 = EngineKey(Exchange.GATEIO, Market.FUTURES)
        assert key1 != key2

    def test_hashable_for_dict_key(self):
        key1 = EngineKey(Exchange.BITGET, Market.SPOT)
        key2 = EngineKey(Exchange.BITGET, Market.SPOT)
        d = {key1: "engine"}
        assert d[key2] == "engine"

    def test_hash_consistency(self):
        key1 = EngineKey(Exchange.MEXC, Market.FUTURES)
        key2 = EngineKey(Exchange.MEXC, Market.FUTURES)
        assert hash(key1) == hash(key2)
