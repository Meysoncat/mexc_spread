"""
Unit tests for futures_arb config loader and validation.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mexc_monitor.futures_arb.config import (
    ConfigValidationError,
    load_futures_arb_settings,
    validate_settings,
)
from mexc_monitor.futures_arb.models import FuturesArbSettings


class TestValidateSettings:
    """Tests for validate_settings()."""

    def test_valid_defaults(self):
        """Default settings should pass validation."""
        settings = FuturesArbSettings()
        errors = validate_settings(settings)
        assert errors == []

    def test_entry_threshold_must_exceed_exit(self):
        """entry_threshold_bps must be > exit_threshold_bps."""
        settings = FuturesArbSettings(entry_threshold_bps=5.0, exit_threshold_bps=10.0)
        errors = validate_settings(settings)
        assert len(errors) == 1
        assert "entry_threshold_bps" in errors[0]

    def test_entry_equals_exit_fails(self):
        """entry_threshold_bps == exit_threshold_bps should fail."""
        settings = FuturesArbSettings(entry_threshold_bps=10.0, exit_threshold_bps=10.0)
        errors = validate_settings(settings)
        assert len(errors) == 1
        assert "entry_threshold_bps" in errors[0]

    def test_position_notional_must_be_positive(self):
        """position_notional_usdt must be > 0."""
        settings = FuturesArbSettings(position_notional_usdt=0.0)
        errors = validate_settings(settings)
        assert any("position_notional_usdt" in e for e in errors)

    def test_position_notional_negative_fails(self):
        """Negative position_notional_usdt should fail."""
        settings = FuturesArbSettings(position_notional_usdt=-100.0)
        errors = validate_settings(settings)
        assert any("position_notional_usdt" in e for e in errors)

    def test_max_concurrent_positions_lower_bound(self):
        """max_concurrent_positions must be >= 1."""
        settings = FuturesArbSettings(max_concurrent_positions=0)
        errors = validate_settings(settings)
        assert any("max_concurrent_positions" in e for e in errors)

    def test_max_concurrent_positions_upper_bound(self):
        """max_concurrent_positions must be <= 20."""
        settings = FuturesArbSettings(max_concurrent_positions=21)
        errors = validate_settings(settings)
        assert any("max_concurrent_positions" in e for e in errors)

    def test_max_concurrent_positions_valid_bounds(self):
        """max_concurrent_positions at boundaries (1, 20) should pass."""
        for val in (1, 20):
            settings = FuturesArbSettings(max_concurrent_positions=val)
            errors = validate_settings(settings)
            assert not any("max_concurrent_positions" in e for e in errors)

    def test_futures_leverage_lower_bound(self):
        """futures_leverage must be >= 1."""
        settings = FuturesArbSettings(futures_leverage=0)
        errors = validate_settings(settings)
        assert any("futures_leverage" in e for e in errors)

    def test_futures_leverage_upper_bound(self):
        """futures_leverage must be <= 20."""
        settings = FuturesArbSettings(futures_leverage=21)
        errors = validate_settings(settings)
        assert any("futures_leverage" in e for e in errors)

    def test_futures_leverage_valid_bounds(self):
        """futures_leverage at boundaries (1, 20) should pass."""
        for val in (1, 20):
            settings = FuturesArbSettings(futures_leverage=val)
            errors = validate_settings(settings)
            assert not any("futures_leverage" in e for e in errors)

    def test_multiple_errors_reported(self):
        """All validation errors should be reported at once."""
        settings = FuturesArbSettings(
            entry_threshold_bps=1.0,
            exit_threshold_bps=10.0,
            position_notional_usdt=-5.0,
            max_concurrent_positions=0,
            futures_leverage=25,
        )
        errors = validate_settings(settings)
        assert len(errors) == 4


class TestLoadFuturesArbSettings:
    """Tests for load_futures_arb_settings()."""

    def test_load_defaults_when_no_file(self, tmp_path: Path):
        """Should return defaults when config file doesn't exist."""
        settings = load_futures_arb_settings(tmp_path / "nonexistent.json")
        assert settings.entry_threshold_bps == 30.0
        assert settings.mode == "paper"
        assert settings.symbols == ["BTCUSDT", "ETHUSDT"]

    def test_load_from_json_file(self, tmp_path: Path):
        """Should load values from JSON file."""
        config = {
            "entry_threshold_bps": 50.0,
            "exit_threshold_bps": 10.0,
            "position_notional_usdt": 2000.0,
            "max_concurrent_positions": 10,
            "futures_leverage": 5,
            "mode": "live",
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 50.0
        assert settings.exit_threshold_bps == 10.0
        assert settings.position_notional_usdt == 2000.0
        assert settings.max_concurrent_positions == 10
        assert settings.futures_leverage == 5
        assert settings.mode == "live"

    def test_json_partial_override(self, tmp_path: Path):
        """JSON should only override specified fields, rest stay default."""
        config = {"entry_threshold_bps": 45.0}
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 45.0
        # Defaults preserved
        assert settings.exit_threshold_bps == 5.0
        assert settings.futures_leverage == 3

    def test_env_override_takes_precedence(self, tmp_path: Path, monkeypatch):
        """Environment variables should override JSON values."""
        config = {"entry_threshold_bps": 50.0, "exit_threshold_bps": 10.0}
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        monkeypatch.setenv("FUTURES_ARB_ENTRY_THRESHOLD_BPS", "60.0")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 60.0
        assert settings.exit_threshold_bps == 10.0

    def test_env_override_bool(self, tmp_path: Path, monkeypatch):
        """Boolean env vars should be parsed correctly."""
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text("{}", encoding="utf-8")

        monkeypatch.setenv("FUTURES_ARB_ENABLED", "true")
        settings = load_futures_arb_settings(config_file)
        assert settings.enabled is True

    def test_env_override_int(self, tmp_path: Path, monkeypatch):
        """Integer env vars should be parsed correctly."""
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text("{}", encoding="utf-8")

        monkeypatch.setenv("FUTURES_ARB_MAX_CONCURRENT_POSITIONS", "8")
        settings = load_futures_arb_settings(config_file)
        assert settings.max_concurrent_positions == 8

    def test_invalid_json_uses_defaults(self, tmp_path: Path):
        """Malformed JSON should fall back to defaults."""
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text("not valid json {{{", encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 30.0

    def test_validation_raises_on_invalid(self, tmp_path: Path):
        """Should raise ConfigValidationError when validate=True and settings invalid."""
        config = {
            "entry_threshold_bps": 5.0,
            "exit_threshold_bps": 10.0,
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_futures_arb_settings(config_file)
        assert "entry_threshold_bps" in str(exc_info.value)

    def test_validation_skipped_when_disabled(self, tmp_path: Path):
        """Should not raise when validate=False even with invalid settings."""
        config = {
            "entry_threshold_bps": 5.0,
            "exit_threshold_bps": 10.0,
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file, validate=False)
        assert settings.entry_threshold_bps == 5.0

    def test_comments_in_json_ignored(self, tmp_path: Path):
        """Fields starting with _ should be ignored."""
        config = {
            "_comment": "This is a comment",
            "entry_threshold_bps": 40.0,
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 40.0

    def test_unknown_fields_ignored(self, tmp_path: Path):
        """Unknown fields in JSON should be silently ignored."""
        config = {
            "entry_threshold_bps": 40.0,
            "unknown_field": "should be ignored",
            "another_unknown": 123,
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.entry_threshold_bps == 40.0

    def test_symbols_list_from_json(self, tmp_path: Path):
        """List fields should be loaded from JSON."""
        config = {
            "symbols": ["SOLUSDT", "AVAXUSDT", "DOTUSDT"],
        }
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        settings = load_futures_arb_settings(config_file)
        assert settings.symbols == ["SOLUSDT", "AVAXUSDT", "DOTUSDT"]

    def test_symbols_from_env_comma_separated(self, tmp_path: Path, monkeypatch):
        """List env vars should be parsed as comma-separated."""
        config_file = tmp_path / "futures_arb.json"
        config_file.write_text("{}", encoding="utf-8")

        monkeypatch.setenv("FUTURES_ARB_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
        settings = load_futures_arb_settings(config_file)
        assert settings.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    def test_custom_config_path_via_env(self, tmp_path: Path, monkeypatch):
        """FUTURES_ARB_CONFIG_PATH env var should override default path."""
        config = {"entry_threshold_bps": 99.0, "exit_threshold_bps": 1.0}
        config_file = tmp_path / "custom_config.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        monkeypatch.setenv("FUTURES_ARB_CONFIG_PATH", str(config_file))

        from mexc_monitor.futures_arb.config import default_config_path
        assert default_config_path() == config_file
