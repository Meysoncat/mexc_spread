"""JSON Schema для setup/v1 и событий потока — контракт для внешних систем."""

from __future__ import annotations

from mexc_monitor.setups.model import SETUP_TYPES

_NUM_OR_NULL = {"type": ["number", "null"]}

LEG_SCHEMA = {
    "type": "object",
    "required": ["venue", "market", "symbol", "side", "price", "price_per_coin", "taker_fee_bps"],
    "properties": {
        "venue": {"type": "string", "description": "binance, bybit, okx, gateio, bitget, htx, mexc, asterdex, dydx, hyperliquid, lighter"},
        "market": {"enum": ["perp", "spot"]},
        "symbol": {"type": "string", "description": "Символ в API биржи (BTC-USDT-SWAP, BTC_USDT, 1000PEPEUSDT, kPEPE)"},
        "side": {"enum": ["buy", "sell"]},
        "price": {"type": "number", "description": "Ориентир цены за единицу инструмента: ask для buy, bid для sell"},
        "price_per_coin": {"type": "number", "description": "Та же цена за одну монету базового актива"},
        "l1_qty": {**_NUM_OR_NULL, "description": "Монет на лучшей цене; null — неизвестно"},
        "l1_notional_usd": _NUM_OR_NULL,
        "taker_fee_bps": {"type": "number"},
    },
}

SETUP_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "setup/v1",
    "title": "Trade setup",
    "type": "object",
    "required": ["schema", "id", "instance", "type", "category", "status", "base", "legs", "score", "opened_at", "updated_at"],
    "properties": {
        "schema": {"const": "setup/v1"},
        "id": {"type": "string", "description": "Стабильный ключ идеи: type:BASE:venues[:direction]. Повторно открытая идея сохраняет id"},
        "instance": {"type": "string", "description": "Уникален для каждого открытия; используйте для идемпотентности"},
        "type": {"enum": sorted(SETUP_TYPES)},
        "category": {"enum": ["arbitrage", "directional"]},
        "status": {"enum": ["active", "closed"]},
        "base": {"type": "string", "description": "Базовый актив (BTC, PEPE)"},
        "direction": {"enum": ["long", "short", None], "description": "Только для directional"},
        "legs": {"type": "array", "items": LEG_SCHEMA, "minItems": 1},
        "entry": {**_NUM_OR_NULL, "description": "Цена входа за монету (directional)"},
        "stop": _NUM_OR_NULL,
        "targets": {"type": "array", "items": {"type": "number"}},
        "risk_reward": _NUM_OR_NULL,
        "edge_bps": {**_NUM_OR_NULL, "description": "Ожидаемая прибыль после комиссий и спредов, bps (arbitrage)"},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "horizon_sec": {"type": "integer"},
        "valid_until": {"type": ["string", "null"], "format": "date-time"},
        "opened_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "closed_at": {"type": ["string", "null"], "format": "date-time"},
        "close_reason": {"enum": [None, "condition_gone", "stop_hit", "target_hit", "expired"]},
        "data_age_ms": {"type": "integer", "description": "Возраст самых старых данных среди ног"},
        "metrics": {"type": "object", "description": "Метрики конкретного типа (см. docs/INTEGRATION.md)"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "outcome": {
            "type": "object",
            "description": "Только у закрытых: исход идеи",
            "properties": {
                "result": {"enum": ["target", "stop", "expired"]},
                "targets_hit": {"type": "integer"},
                "return_pct": _NUM_OR_NULL,
                "r_multiple": _NUM_OR_NULL,
                "mfe_pct": {"type": "number"},
                "mae_pct": {"type": "number"},
                "duration_sec": {"type": "number"},
                "max_edge_bps": _NUM_OR_NULL,
                "edge_at_close_bps": _NUM_OR_NULL,
            },
        },
    },
}

EVENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "setup-event/v1",
    "type": "object",
    "required": ["seq", "event", "ts", "setup"],
    "properties": {
        "seq": {"type": "integer", "description": "Монотонный номер события; передайте как Last-Event-ID / since для дочитки"},
        "event": {"enum": ["opened", "updated", "closed"]},
        "ts": {"type": "string", "format": "date-time"},
        "setup": {"$ref": "setup/v1"},
    },
}
