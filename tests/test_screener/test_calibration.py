"""Unit tests for the calibration controller (pure functions)."""

from __future__ import annotations

import pytest

from mexc_monitor.screener.config import ScreenerConfig
from mexc_monitor.screener.engine import _calibrate_step, _median


def _cfg(**overrides) -> ScreenerConfig:
    return ScreenerConfig(**overrides)


# ── _median ─────────────────────────────────────────────────────────────────


def test_median_empty_is_zero():
    assert _median([]) == 0.0


def test_median_odd():
    assert _median([3.0, 1.0, 2.0]) == 2.0


def test_median_even():
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5


# ── _calibrate_step ─────────────────────────────────────────────────────────


def test_calibrate_raises_percentile_when_too_many():
    # default target 2..5 (band width 3); count 20 → excess (20-5)/3 = 5 →
    # proportional step 1.0 * 5 = +5, clamped at max 99.
    nxt = _calibrate_step(95.0, 20.0, _cfg())
    assert nxt == pytest.approx(99.0)


def test_calibrate_lowers_percentile_when_too_few():
    nxt = _calibrate_step(95.0, 1.0, _cfg())
    assert nxt == pytest.approx(94.0)


def test_calibrate_unchanged_when_in_band():
    nxt = _calibrate_step(95.0, 3.0, _cfg())
    assert nxt == pytest.approx(95.0)


def test_calibrate_clamps_to_max():
    # at max already and still too many → stays at max
    nxt = _calibrate_step(99.0, 50.0, _cfg())
    assert nxt == pytest.approx(99.0)


def test_calibrate_clamps_to_min():
    nxt = _calibrate_step(80.0, 0.0, _cfg())
    assert nxt == pytest.approx(80.0)


def test_calibrate_respects_custom_step():
    cfg = _cfg(calibration_step=2.5)
    # Just outside the band (excess < band) → exactly one step.
    assert _calibrate_step(90.0, 6.0, cfg) == pytest.approx(92.5)


def test_calibrate_respects_custom_target_band():
    cfg = _cfg(target_opportunity_min=8, target_opportunity_max=12)
    # 5 is below min(8) → lower percentile
    assert _calibrate_step(95.0, 5.0, cfg) == pytest.approx(94.0)
    # 10 is in band [8,12] → unchanged
    assert _calibrate_step(95.0, 10.0, cfg) == pytest.approx(95.0)


def test_calibrate_step_scales_with_excess():
    cfg = _cfg(calibration_step=1.0)
    # One band-width above the band (8 = 5 + 3) → 1 step; two widths (11) → 2.
    assert _calibrate_step(90.0, 8.0, cfg) == pytest.approx(91.0)
    assert _calibrate_step(90.0, 11.0, cfg) == pytest.approx(92.0)
    # Below the band works symmetrically (-1 → deficit (2-(-1))/3 = 1 → 1 step).
    assert _calibrate_step(90.0, -1.0, cfg) == pytest.approx(89.0)


def test_calibrate_never_less_than_one_step():
    cfg = _cfg(calibration_step=1.0)
    # A count barely outside the band still moves by a full step (no dead zone).
    assert _calibrate_step(90.0, 5.5, cfg) == pytest.approx(91.0)
