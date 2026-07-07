"""Unit tests for lead-lag configuration loader and validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mexc_monitor.lead_lag.config import (
    LeadLagConfig,
    load_lead_lag_config,
    validate_config,
)


class TestValidateConfig:
    """Tests for validate_config()."""

    def _valid_config(self) -> LeadLagConfig:
        """Return a fully valid config for testing."""
        return LeadLagConfig(
            enabled=True,
            leader_exchange="binance",
            lagger_exchanges=["mexc", "bybit"],
            symbols=["BTCUSDT", "ETHUSDT"],
            market="futures",
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            signal_timeout_sec=10.0,
            rolling_window_sec=300.0,
            min_spread_bps=3.0,
            lag_estimation_interval_sec=30.0,
            price_buffer_history_sec=60.0,
            db_path="data/lead_lag_signals.sqlite",
            assumed_taker_fee_bps=2.0,
            ws_urls={
                "binance_futures": "wss://fstream.binance.com/ws",
                "mexc_futures": "wss://contract.mexc.com/edge",
                "bybit": "wss://stream.bybit.com/v5/public/linear",
            },
        )

    def test_valid_config_passes(self):
        """A fully valid config should produce no errors."""
        config = self._valid_config()
        errors = validate_config(config)
        assert errors == []

    def test_z_score_entry_must_be_positive(self):
        """z_score_entry_threshold must be > 0."""
        config = self._valid_config()
        config.z_score_entry_threshold = 0.0
        errors = validate_config(config)
        assert any("z_score_entry_threshold" in e and "> 0" in e for e in errors)

    def test_z_score_entry_negative_fails(self):
        """Negative z_score_entry_threshold should fail."""
        config = self._valid_config()
        config.z_score_entry_threshold = -1.0
        errors = validate_config(config)
        assert any("z_score_entry_threshold" in e for e in errors)

    def test_z_score_exit_must_be_non_negative(self):
        """z_score_exit_threshold must be >= 0."""
        config = self._valid_config()
        config.z_score_exit_threshold = -0.1
        errors = validate_config(config)
        assert any("z_score_exit_threshold" in e and ">= 0" in e for e in errors)

    def test_z_score_exit_zero_is_valid(self):
        """z_score_exit_threshold = 0 should be valid."""
        config = self._valid_config()
        config.z_score_exit_threshold = 0.0
        errors = validate_config(config)
        assert not any("z_score_exit_threshold" in e and ">= 0" in e for e in errors)

    def test_entry_must_exceed_exit(self):
        """z_score_entry_threshold must be > z_score_exit_threshold."""
        config = self._valid_config()
        config.z_score_entry_threshold = 1.0
        config.z_score_exit_threshold = 1.0
        errors = validate_config(config)
        assert any("must be > z_score_exit_threshold" in e for e in errors)

    def test_entry_less_than_exit_fails(self):
        """z_score_entry_threshold < z_score_exit_threshold should fail."""
        config = self._valid_config()
        config.z_score_entry_threshold = 0.5
        config.z_score_exit_threshold = 2.0
        errors = validate_config(config)
        assert any("must be > z_score_exit_threshold" in e for e in errors)

    def test_signal_timeout_must_be_positive(self):
        """signal_timeout_sec must be > 0."""
        config = self._valid_config()
        config.signal_timeout_sec = 0.0
        errors = validate_config(config)
        assert any("signal_timeout_sec" in e for e in errors)

    def test_rolling_window_must_be_positive(self):
        """rolling_window_sec must be > 0."""
        config = self._valid_config()
        config.rolling_window_sec = -1.0
        errors = validate_config(config)
        assert any("rolling_window_sec" in e for e in errors)

    def test_min_spread_bps_must_be_non_negative(self):
        """min_spread_bps must be >= 0."""
        config = self._valid_config()
        config.min_spread_bps = -0.5
        errors = validate_config(config)
        assert any("min_spread_bps" in e for e in errors)

    def test_min_spread_bps_zero_is_valid(self):
        """min_spread_bps = 0 should be valid."""
        config = self._valid_config()
        config.min_spread_bps = 0.0
        errors = validate_config(config)
        assert not any("min_spread_bps" in e for e in errors)

    def test_lag_estimation_interval_must_be_positive(self):
        """lag_estimation_interval_sec must be > 0."""
        config = self._valid_config()
        config.lag_estimation_interval_sec = 0.0
        errors = validate_config(config)
        assert any("lag_estimation_interval_sec" in e for e in errors)

    def test_price_buffer_history_must_be_positive(self):
        """price_buffer_history_sec must be > 0."""
        config = self._valid_config()
        config.price_buffer_history_sec = 0.0
        errors = validate_config(config)
        assert any("price_buffer_history_sec" in e for e in errors)

    def test_symbols_must_be_non_empty(self):
        """symbols must not be empty."""
        config = self._valid_config()
        config.symbols = []
        errors = validate_config(config)
        assert any("symbols" in e and "non-empty" in e for e in errors)

    def test_lagger_exchanges_must_be_non_empty(self):
        """lagger_exchanges must not be empty."""
        config = self._valid_config()
        config.lagger_exchanges = []
        errors = validate_config(config)
        assert any("lagger_exchanges" in e and "non-empty" in e for e in errors)

    def test_leader_not_in_laggers(self):
        """leader_exchange must not be in lagger_exchanges."""
        config = self._valid_config()
        config.leader_exchange = "mexc"
        config.lagger_exchanges = ["mexc", "bybit"]
        errors = validate_config(config)
        assert any("leader_exchange" in e and "must not be in" in e for e in errors)

    def test_assumed_taker_fee_must_be_non_negative(self):
        """assumed_taker_fee_bps must be >= 0."""
        config = self._valid_config()
        config.assumed_taker_fee_bps = -1.0
        errors = validate_config(config)
        assert any("assumed_taker_fee_bps" in e for e in errors)

    def test_assumed_taker_fee_zero_is_valid(self):
        """assumed_taker_fee_bps = 0 should be valid."""
        config = self._valid_config()
        config.assumed_taker_fee_bps = 0.0
        errors = validate_config(config)
        assert not any("assumed_taker_fee_bps" in e for e in errors)

    def test_market_must_be_spot_or_futures(self):
        """market must be 'spot' or 'futures'."""
        config = self._valid_config()
        config.market = "options"
        errors = validate_config(config)
        assert any("market" in e and "'spot' or 'futures'" in e for e in errors)

    def test_market_spot_is_valid(self):
        """market = 'spot' should be valid."""
        config = self._valid_config()
        config.market = "spot"
        errors = validate_config(config)
        assert not any("market" in e for e in errors)

    def test_ws_urls_must_contain_leader(self):
        """ws_urls must have a URL for the leader exchange."""
        config = self._valid_config()
        config.ws_urls = {
            "mexc_futures": "wss://contract.mexc.com/edge",
            "bybit": "wss://stream.bybit.com/v5/public/linear",
        }
        errors = validate_config(config)
        assert any("leader_exchange" in e and "binance" in e for e in errors)

    def test_ws_urls_must_contain_all_laggers(self):
        """ws_urls must have URLs for all lagger exchanges."""
        config = self._valid_config()
        config.ws_urls = {
            "binance_futures": "wss://fstream.binance.com/ws",
            "mexc_futures": "wss://contract.mexc.com/edge",
            # missing bybit
        }
        errors = validate_config(config)
        assert any("lagger_exchange" in e and "bybit" in e for e in errors)

    def test_ws_urls_empty_fails(self):
        """Empty ws_urls should fail."""
        config = self._valid_config()
        config.ws_urls = {}
        errors = validate_config(config)
        assert any("ws_urls" in e for e in errors)

    def test_ws_urls_prefix_matching(self):
        """ws_urls keys with exchange prefix (e.g. 'binance_futures') should match."""
        config = self._valid_config()
        config.lagger_exchanges = ["mexc"]
        config.ws_urls = {
            "binance_spot": "wss://stream.binance.com:9443/ws",
            "mexc_spot": "wss://wbs.mexc.com/ws",
        }
        errors = validate_config(config)
        # Should pass since binance_spot matches "binance" and mexc_spot matches "mexc"
        assert not any("ws_urls" in e for e in errors)

    def test_multiple_errors_reported(self):
        """All validation errors should be reported at once."""
        config = LeadLagConfig(
            z_score_entry_threshold=-1.0,
            z_score_exit_threshold=-2.0,
            signal_timeout_sec=0.0,
            rolling_window_sec=0.0,
            symbols=[],
            lagger_exchanges=[],
            market="invalid",
            ws_urls={},
        )
        errors = validate_config(config)
        # Should have multiple errors
        assert len(errors) >= 5

    def test_error_messages_contain_actual_values(self):
        """Error messages should include the actual invalid value."""
        config = self._valid_config()
        config.signal_timeout_sec = -5.0
        errors = validate_config(config)
        assert any("-5.0" in e for e in errors)


class TestLoadLeadLagConfig:
    """Tests for load_lead_lag_config()."""

    def test_returns_defaults_when_file_missing(self, tmp_path: Path):
        """Should return defaults when config file doesn't exist."""
        config = load_lead_lag_config(tmp_path / "nonexistent.json")
        assert config.enabled is False
        assert config.leader_exchange == "binance"
        assert config.lagger_exchanges == ["mexc"]
        assert config.symbols == ["BTCUSDT", "ETHUSDT"]
        assert config.market == "futures"
        assert config.z_score_entry_threshold == 2.0
        assert config.z_score_exit_threshold == 0.5
        assert config.signal_timeout_sec == 10.0
        assert config.rolling_window_sec == 300.0
        assert config.min_spread_bps == 3.0
        assert config.lag_estimation_interval_sec == 30.0
        assert config.price_buffer_history_sec == 60.0
        assert config.db_path == "data/lead_lag_signals.sqlite"
        assert config.assumed_taker_fee_bps == 2.0

    def test_returns_defaults_on_invalid_json(self, tmp_path: Path):
        """Should return defaults when file contains invalid JSON."""
        config_file = tmp_path / "external_apis.json"
        config_file.write_text("not valid json {{{", encoding="utf-8")
        config = load_lead_lag_config(config_file)
        assert config.leader_exchange == "binance"
        assert config.z_score_entry_threshold == 2.0

    def test_returns_defaults_when_section_missing(self, tmp_path: Path):
        """Should return defaults when 'lead_lag' section is absent."""
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps({"mexc": {}}), encoding="utf-8")
        config = load_lead_lag_config(config_file)
        assert config.leader_exchange == "binance"
        assert config.z_score_entry_threshold == 2.0

    def test_loads_values_from_lead_lag_section(self, tmp_path: Path):
        """Should load values from the lead_lag section."""
        data = {
            "lead_lag": {
                "enabled": True,
                "leader_exchange": "binance",
                "lagger_exchanges": ["mexc", "bybit"],
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "market": "spot",
                "z_score_entry_threshold": 3.0,
                "z_score_exit_threshold": 1.0,
                "signal_timeout_sec": 15.0,
                "rolling_window_sec": 600.0,
                "min_spread_bps": 5.0,
                "lag_estimation_interval_sec": 60.0,
                "price_buffer_history_sec": 120.0,
                "db_path": "data/custom.sqlite",
                "assumed_taker_fee_bps": 3.0,
                "ws_urls": {
                    "binance_futures": "wss://fstream.binance.com/ws",
                    "mexc_futures": "wss://contract.mexc.com/edge",
                    "bybit": "wss://stream.bybit.com/v5/public/linear",
                },
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.enabled is True
        assert config.leader_exchange == "binance"
        assert config.lagger_exchanges == ["mexc", "bybit"]
        assert config.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        assert config.market == "spot"
        assert config.z_score_entry_threshold == 3.0
        assert config.z_score_exit_threshold == 1.0
        assert config.signal_timeout_sec == 15.0
        assert config.rolling_window_sec == 600.0
        assert config.min_spread_bps == 5.0
        assert config.lag_estimation_interval_sec == 60.0
        assert config.price_buffer_history_sec == 120.0
        assert config.db_path == "data/custom.sqlite"
        assert config.assumed_taker_fee_bps == 3.0
        assert "binance_futures" in config.ws_urls

    def test_partial_section_uses_defaults_for_missing(self, tmp_path: Path):
        """Partial lead_lag section should use defaults for missing fields."""
        data = {
            "lead_lag": {
                "enabled": True,
                "z_score_entry_threshold": 3.5,
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.enabled is True
        assert config.z_score_entry_threshold == 3.5
        # Defaults for unspecified fields
        assert config.leader_exchange == "binance"
        assert config.signal_timeout_sec == 10.0

    def test_invalid_float_uses_default(self, tmp_path: Path):
        """Non-numeric float fields should fall back to defaults."""
        data = {
            "lead_lag": {
                "z_score_entry_threshold": "not_a_number",
                "signal_timeout_sec": None,
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.z_score_entry_threshold == 2.0
        assert config.signal_timeout_sec == 10.0

    def test_non_list_symbols_uses_default(self, tmp_path: Path):
        """Non-list symbols field should fall back to default."""
        data = {
            "lead_lag": {
                "symbols": "BTCUSDT",  # string instead of list
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.symbols == ["BTCUSDT", "ETHUSDT"]

    def test_empty_symbols_list_uses_default(self, tmp_path: Path):
        """Empty symbols list should fall back to default."""
        data = {
            "lead_lag": {
                "symbols": [],
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.symbols == ["BTCUSDT", "ETHUSDT"]

    def test_non_dict_ws_urls_uses_default(self, tmp_path: Path):
        """Non-dict ws_urls should fall back to default (empty dict)."""
        data = {
            "lead_lag": {
                "ws_urls": "not_a_dict",
            }
        }
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.ws_urls == {}

    def test_lead_lag_section_not_dict_uses_defaults(self, tmp_path: Path):
        """If lead_lag section is not a dict, use defaults."""
        data = {"lead_lag": "not_a_dict"}
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps(data), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.leader_exchange == "binance"

    def test_root_not_dict_uses_defaults(self, tmp_path: Path):
        """If JSON root is not a dict (e.g. array), use defaults."""
        config_file = tmp_path / "external_apis.json"
        config_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        config = load_lead_lag_config(config_file)
        assert config.leader_exchange == "binance"
