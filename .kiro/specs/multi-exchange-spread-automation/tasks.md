# Implementation Plan: multi-exchange-spread-automation

## Overview

Implementation plan for extending MEXC Spread Monitor into a multi-exchange automation platform. Covers Telegram alerts, AsterDEX WebSocket integration, multi-exchange spread capture, cross-exchange arbitrage engine, cross-spread history persistence, and corresponding frontend panels.

## Tasks

- [x] 1. Telegram Alert Service — Core
  - [x] 1.1 Create `mexc_monitor/alerts/__init__.py` with module exports
  - [x] 1.2 Create `mexc_monitor/alerts/telegram.py` — HTTP client for Telegram Bot API (sendMessage, retry with exponential back-off up to 3 attempts)
  - [x] 1.3 Create `mexc_monitor/alerts/config.py` — AlertConfig dataclass, load/save from JSON (`config/alerts.json`), mask bot_token helper
  - [x] 1.4 Create `mexc_monitor/alerts/service.py` — AlertService class with rate limiting (1 msg per type+symbol per 60s), methods: send_spread_alert, send_arbitrage_alert, send_trade_alert, test_connection, update_config, get_config
  - [x] 1.5 Add alert endpoints to `backend/main.py`: GET /api/alerts/settings, PATCH /api/alerts/settings, POST /api/alerts/test (protected by admin token)
  - [x] 1.6 Integrate AlertService with SpreadCaptureEngine — send trade_alert on position_opened/position_closed events
- [x] 2. AsterDEX WebSocket Client
  - [x] 2.1 Create `mexc_monitor/aster/ws_client.py` — AsterWebSocketClient class: connect to wss://fstream.asterdex.com/ws, subscribe/unsubscribe bookTicker streams, parse Binance-format messages
  - [x] 2.2 Implement reconnection logic with exponential back-off (1s base, 60s max), ping every 30s
  - [x] 2.3 Integrate with Spread_Buffer: on bookTicker update, call push_tick with symbol prefixed as "ASTER:SYMBOL"
  - [x] 2.4 Implement cross-spread real-time calculation: when both MEXC and ASTER ticks available for same asset, compute basis_bps and push to buffer as "CROSS:SYMBOL"
  - [x] 2.5 Add WS management endpoints to `backend/main.py`: GET /api/aster/ws/status, POST /api/aster/ws/subscribe, POST /api/aster/ws/unsubscribe
  - [x] 2.6 Add `asterdex_ws` config section to `config/external_apis.json` and load in Settings
  - [x] 2.7 Start AsterDEX WS on FastAPI startup if enabled in config
- [x] 3. Multi-Exchange Spread Capture
  - [x] 3.1 Create `mexc_monitor/arbitrage/adapters.py` — ExchangeAdapter protocol and implementations: MexcSpotAdapter, AsterDexAdapter
  - [x] 3.2 Add `exchange` field to CaptureSettings ("mexc_spot" | "mexc_futures" | "asterdex"), update update_settings to validate exchange switching (reject if position open)
  - [x] 3.3 Modify SpreadCaptureEngine._check_entry and _check_exit to use correct Spread_Buffer key based on exchange (SYMBOL for mexc_spot, SYMBOL for mexc_futures, ASTER:SYMBOL for asterdex)
  - [x] 3.4 Implement live order placement through ExchangeAdapter in SpreadCaptureEngine for AsterDEX (place_limit_order, cancel_order via AsterPrivateClient)
  - [x] 3.5 Add per-exchange PNL tracking: extend CaptureStats with per_exchange_stats dict, update trade recording
  - [x] 3.6 Update /api/capture/status and /api/capture/trades to include exchange field in responses
- [x] 4. Cross-Exchange Arbitrage Engine — Core
  - [x] 4.1 Create `mexc_monitor/arbitrage/__init__.py` and `mexc_monitor/arbitrage/models.py` — ArbitrageSettings, ArbPosition, ArbTradeRecord, ArbStats dataclasses
  - [x] 4.2 Create `mexc_monitor/arbitrage/engine.py` — ArbitrageEngine class: init, start/stop thread, get_status, get_positions, get_trades, set_kill_switch, update_settings
  - [x] 4.3 Implement ArbitrageEngine._step: for each symbol, get ticks from both exchanges, compute executable spread, check entry conditions
  - [x] 4.4 Implement position opening logic: determine direction (which exchange is cheaper), place orders on both exchanges (paper mode: simulate, live mode: use adapters)
  - [x] 4.5 Implement position monitoring and exit: check spread convergence, timeout, kill switch; close positions on both legs
  - [x] 4.6 Implement one-leg protection: if one order not filled within max_pending_sec, cancel unfilled order and close filled leg
  - [x] 4.7 Add arbitrage config section to `config/external_apis.json`, load in Settings
- [x] 5. Arbitrage Engine — API & Integration
  - [x] 5.1 Add arbitrage endpoints to `backend/main.py`: GET /api/arbitrage/status, POST /api/arbitrage/start, POST /api/arbitrage/stop, POST /api/arbitrage/kill-switch (all admin-protected)
  - [x] 5.2 Add GET /api/arbitrage/trades?limit=50 and GET /api/arbitrage/positions endpoints
  - [x] 5.3 Add PATCH /api/arbitrage/settings endpoint for runtime configuration updates
  - [x] 5.4 Integrate ArbitrageEngine with AlertService — send arbitrage_alert when opportunity detected and position opened
  - [x] 5.5 Start ArbitrageEngine on FastAPI startup if enabled in config, stop on shutdown
- [x] 6. Cross-Spread History Store
  - [x] 6.1 Add CrossSpreadSnapshot model to `mexc_monitor/orm/models.py` with fields: symbol, mexc_bid, mexc_ask, mexc_mid, aster_bid, aster_ask, aster_mid, basis_abs, basis_bps, funding_rate, observed_at
  - [x] 6.2 Create `mexc_monitor/cross_spread_store.py` — CrossSpreadWorker: periodic snapshot from Spread_Buffer to SQLite, retention cleanup
  - [x] 6.3 Implement query function: filter by symbol, time range, limit; downsampling for periods > 24h (max 2000 points)
  - [x] 6.4 Add GET /api/cross-spread/history endpoint to `backend/main.py` with query params: symbol, since, until, limit
  - [x] 6.5 Add `cross_spread_history` config section to `config/external_apis.json`
  - [x] 6.6 Start CrossSpreadWorker on FastAPI startup if enabled, stop on shutdown
- [x] 7. Frontend — Alerts Panel
  - [x] 7.1 Create `frontend/src/AlertsSettingsPanel.tsx` — form with fields: bot_token (masked), chat_id, toggle per alert type, threshold inputs
  - [x] 7.2 Implement load settings on mount (GET /api/alerts/settings), save on submit (PATCH /api/alerts/settings)
  - [x] 7.3 Add "Test" button that calls POST /api/alerts/test and shows success/error toast
  - [x] 7.4 Integrate AlertsSettingsPanel into App.tsx (accessible from settings/admin area)
- [x] 8. Frontend — Arbitrage Panel
  - [x] 8.1 Create `frontend/src/ArbitragePanel.tsx` — status display (running/stopped, PNL stats, open positions table)
  - [x] 8.2 Add start/stop/kill-switch controls with confirmation dialogs
  - [x] 8.3 Add settings form (thresholds, max position, max concurrent) with PATCH /api/arbitrage/settings
  - [x] 8.4 Add trades history table with pagination (GET /api/arbitrage/trades)
  - [x] 8.5 Integrate ArbitragePanel into App.tsx
- [x] 9. Frontend — Cross-Spread History Chart
  - [x] 9.1 Create `frontend/src/CrossSpreadHistoryChart.tsx` — lightweight-charts time series of basis_bps over time
  - [x] 9.2 Add symbol selector and time range picker (1h, 6h, 24h, 7d, 30d)
  - [x] 9.3 Fetch data from GET /api/cross-spread/history and render chart
  - [x] 9.4 Integrate into AsterDexPanel or as standalone modal

## Task Dependency Graph

```json
{
  "waves": [
    ["1. Telegram Alert Service", "2. AsterDEX WebSocket Client"],
    ["3. Multi-Exchange Spread Capture", "6. Cross-Spread History Store"],
    ["4. Cross-Exchange Arbitrage Engine", "7. Frontend — Alerts Panel"],
    ["5. Arbitrage Engine — API & Integration", "9. Frontend — Cross-Spread History Chart"],
    ["8. Frontend — Arbitrage Panel"]
  ]
}
```

## Notes

- Tasks 1 and 2 can be developed in parallel as they have no mutual dependencies.
- Task 3 depends on Task 2 (AsterDEX WebSocket) for the AsterDEX adapter integration.
- Task 4 depends on both Task 2 and Task 3 for exchange adapters and real-time data.
- Task 5 depends on Tasks 1 and 4 for alert integration and the arbitrage engine itself.
- Frontend tasks (7, 8, 9) depend on their respective backend tasks being complete.
- All trading functionality should be tested in paper mode before enabling live mode.
- Kill switch should be enabled by default in all environments.
