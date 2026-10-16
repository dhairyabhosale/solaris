"""Tests for chronological splitting, metrics and the sliced evaluation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaris import config, evaluate
from solaris.models import baselines, splits


@pytest.fixture
def frame() -> pd.DataFrame:
    index = pd.date_range("2022-01-01", "2023-12-31 23:00", freq="1h", tz=config.IST)
    rng = np.random.default_rng(1)
    hour = index.hour.to_numpy()
    demand = 150_000 + 25_000 * np.sin((hour - 6) / 24 * 2 * np.pi) + rng.normal(0, 800, len(index))
    return pd.DataFrame(
        {
            config.TARGET: demand,
            "is_hot_period": index.month.isin([4, 5, 6]),
            "is_evening": (hour >= 17) & (hour <= 22),
            "solar_mw": np.clip(20_000 * np.sin((hour - 6) / 12 * np.pi), 0, None),
            "wind_mw": np.full(len(index), 8_000.0),
        },
        index=index,
    ).assign(
        residual_load_mw=lambda d: d[config.TARGET] - d["solar_mw"] - d["wind_mw"],
    ).assign(
        residual_ramp_mw_per_h=lambda d: d["residual_load_mw"].diff(),
    )


# --------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------


def test_split_blocks_are_disjoint_and_ordered(frame):
    split = splits.chronological_split(frame)
    assert split.train.max() < split.valid.min()
    assert split.valid.max() < split.test.min()
    assert len(split.train.intersection(split.valid)) == 0
    assert len(split.valid.intersection(split.test)) == 0
    assert len(split.train.intersection(split.test)) == 0


def test_split_covers_every_row_in_range(frame):
    split = splits.chronological_split(frame)
    total = len(split.train) + len(split.valid) + len(split.test)
    in_range = frame.index < pd.Timestamp(config.TEST_END, tz=config.IST)
    assert total == int(in_range.sum())


def test_split_respects_configured_boundaries(frame):
    split = splits.chronological_split(frame)
    assert split.train.max() < pd.Timestamp(config.TRAIN_END, tz=config.IST)
    assert split.valid.min() >= pd.Timestamp(config.TRAIN_END, tz=config.IST)
    assert split.test.min() >= pd.Timestamp(config.VALID_END, tz=config.IST)


def test_split_rejects_out_of_order_boundaries(frame):
    with pytest.raises(ValueError, match="strictly increasing"):
        splits.chronological_split(frame, "2023-06-01", "2023-01-01", "2023-12-01")


def test_split_describe_reports_hours(frame):
    described = splits.chronological_split(frame).describe()
    assert set(described) == {"train", "valid", "test"}
    assert described["train"]["hours"] > 0


# --------------------------------------------------------------------------
# Rolling-origin folds
# --------------------------------------------------------------------------


def test_rolling_folds_train_strictly_before_test(frame):
    folds = splits.rolling_origin_folds(frame, "2023-01-01", "2023-07-01")
    assert folds
    for fold in folds:
        assert fold.train.max() < fold.test.min()


def test_rolling_folds_expand(frame):
    folds = splits.rolling_origin_folds(frame, "2023-01-01", "2023-07-01")
    sizes = [len(f.train) for f in folds]
    assert sizes == sorted(sizes)
    assert sizes[-1] > sizes[0]


def test_rolling_folds_skip_short_training_history(frame):
    """A fold whose training history is below the minimum is not emitted."""
    folds = splits.rolling_origin_folds(
        frame, "2022-02-01", "2022-12-01", min_train_days=300
    )
    for fold in folds:
        assert len(fold.train) >= 300 * 24


# --------------------------------------------------------------------------
# Point metrics
# --------------------------------------------------------------------------


def test_perfect_forecast_has_zero_error():
    index = pd.date_range("2023-01-01", periods=100, freq="1h", tz=config.IST)
    actual = pd.Series(np.linspace(100, 200, 100), index=index)
    metrics = evaluate.point_metrics(actual, actual.copy())
    assert metrics["mae_mw"] == pytest.approx(0.0)
    assert metrics["rmse_mw"] == pytest.approx(0.0)
    assert metrics["r2"] == pytest.approx(1.0)


def test_metrics_match_hand_computation():
    index = pd.date_range("2023-01-01", periods=4, freq="1h", tz=config.IST)
    actual = pd.Series([100.0, 200.0, 300.0, 400.0], index=index)
    predicted = pd.Series([110.0, 190.0, 330.0, 360.0], index=index)
    metrics = evaluate.point_metrics(actual, predicted)

    assert metrics["n"] == 4
    assert metrics["mae_mw"] == pytest.approx((10 + 10 + 30 + 40) / 4)
    assert metrics["rmse_mw"] == pytest.approx(np.sqrt((100 + 100 + 900 + 1600) / 4))
    assert metrics["bias_mw"] == pytest.approx((10 - 10 + 30 - 40) / 4)
    assert metrics["mae_gw"] == pytest.approx(metrics["mae_mw"] / 1000)


def test_metrics_ignore_unmatched_rows():
    index = pd.date_range("2023-01-01", periods=4, freq="1h", tz=config.IST)
    actual = pd.Series([100.0, 200.0, 300.0, np.nan], index=index)
    predicted = pd.Series([110.0, np.nan, 300.0, 400.0], index=index)
    assert evaluate.point_metrics(actual, predicted)["n"] == 2


def test_metrics_on_empty_input():
    empty = pd.Series(dtype=float)
    metrics = evaluate.point_metrics(empty, empty)
    assert metrics["n"] == 0
    assert metrics["mae_mw"] is None


def test_skill_vs_signs():
    better = {"mae_mw": 80.0}
    reference = {"mae_mw": 100.0}
    assert evaluate.skill_vs(better, reference) == pytest.approx(20.0)
    assert evaluate.skill_vs(reference, better) == pytest.approx(-25.0)
    assert evaluate.skill_vs({"mae_mw": None}, reference) is None
    assert evaluate.skill_vs(better, {"mae_mw": 0.0}) is None


# --------------------------------------------------------------------------
# Peak metrics
# --------------------------------------------------------------------------


def test_peak_metrics_detect_level_and_timing_error():
    index = pd.date_range("2023-01-01", periods=48, freq="1h", tz=config.IST)
    actual = pd.Series(100.0, index=index)
    actual.iloc[19] = 200.0   # day 1 peak at 19:00
    actual.iloc[24 + 20] = 210.0  # day 2 peak at 20:00

    predicted = pd.Series(100.0, index=index)
    predicted.iloc[19] = 190.0     # right hour, 10 low
    predicted.iloc[24 + 18] = 215.0  # two hours early, 5 high

    metrics = evaluate.peak_metrics(actual, predicted)
    assert metrics["n_days"] == 2
    assert metrics["peak_mae_mw"] == pytest.approx((10 + 5) / 2)
    assert metrics["peak_hour_mae_h"] == pytest.approx((0 + 2) / 2)
    assert metrics["peak_hour_exact_pct"] == pytest.approx(50.0)


def test_peak_metrics_skip_incomplete_days():
    index = pd.date_range("2023-01-01", periods=30, freq="1h", tz=config.IST)
    series = pd.Series(np.arange(30, dtype=float), index=index)
    # Only the first day has all 24 hours.
    assert evaluate.peak_metrics(series, series)["n_days"] == 1


def test_peak_hour_error_is_circular():
    """23:00 against 00:00 is a one-hour error, not twenty-three."""
    index = pd.date_range("2023-01-01", periods=24, freq="1h", tz=config.IST)
    actual = pd.Series(100.0, index=index)
    actual.iloc[23] = 200.0
    predicted = pd.Series(100.0, index=index)
    predicted.iloc[0] = 200.0
    assert evaluate.peak_metrics(actual, predicted)["peak_hour_mae_h"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Sliced metrics
# --------------------------------------------------------------------------


def test_sliced_metrics_cover_expected_slices(frame):
    predicted = frame[config.TARGET] * 1.01
    out = evaluate.sliced_metrics(frame, predicted)
    for name in ("overall", "hot_period", "non_hot_period", "evening_window",
                 "top_decile_demand", "by_hour"):
        assert name in out


def test_thin_slices_are_flagged_not_reported():
    index = pd.date_range("2023-01-01", periods=240, freq="1h", tz=config.IST)
    frame = pd.DataFrame(
        {
            config.TARGET: np.full(240, 150_000.0),
            # Only 10 hot hours -- well under MIN_SLICE_ROWS.
            "is_hot_period": [True] * 10 + [False] * 230,
            "is_evening": (index.hour >= 17) & (index.hour <= 22),
        },
        index=index,
    )
    out = evaluate.sliced_metrics(frame, frame[config.TARGET] * 1.01)
    assert out["hot_period"]["sufficient"] is False
    assert "mae_mw" not in out["hot_period"]


def test_hourly_breakdown_covers_all_hours(frame):
    out = evaluate.hourly_breakdown(frame[config.TARGET], frame[config.TARGET] * 1.01)
    assert set(out) == set(range(24))


# --------------------------------------------------------------------------
# Quantile metrics
# --------------------------------------------------------------------------


def test_pinball_loss_is_zero_for_a_perfect_median():
    index = pd.date_range("2023-01-01", periods=50, freq="1h", tz=config.IST)
    actual = pd.Series(np.arange(50, dtype=float), index=index)
    assert evaluate.pinball_loss(actual, actual.copy(), 0.5) == pytest.approx(0.0)


def test_pinball_loss_is_asymmetric():
    index = pd.date_range("2023-01-01", periods=2, freq="1h", tz=config.IST)
    actual = pd.Series([100.0, 100.0], index=index)
    over = pd.Series([110.0, 110.0], index=index)
    under = pd.Series([90.0, 90.0], index=index)
    # For q=0.9, under-predicting is penalised more heavily.
    assert evaluate.pinball_loss(actual, under, 0.9) > evaluate.pinball_loss(actual, over, 0.9)


def test_quantile_calibration_recovers_known_coverage():
    index = pd.date_range("2023-01-01", periods=1000, freq="1h", tz=config.IST)
    rng = np.random.default_rng(3)
    actual = pd.Series(rng.normal(150_000, 5_000, 1000), index=index)
    quantiles = pd.DataFrame(
        {
            "q10": np.full(1000, 150_000 + 5_000 * -1.2816),
            "q50": np.full(1000, 150_000.0),
            "q90": np.full(1000, 150_000 + 5_000 * 1.2816),
        },
        index=index,
    )
    out = evaluate.quantile_calibration(actual, quantiles)
    assert out["quantiles"]["q90"]["empirical_coverage"] == pytest.approx(0.9, abs=0.04)
    assert out["quantiles"]["q10"]["empirical_coverage"] == pytest.approx(0.1, abs=0.04)
    assert out["interval_p10_p90"]["empirical_coverage"] == pytest.approx(0.8, abs=0.05)


# --------------------------------------------------------------------------
# Evening ramp
# --------------------------------------------------------------------------


def test_evening_ramp_stats_reports_its_definition(frame):
    stats = evaluate.evening_ramp_stats(frame)
    assert "Not an operator net-load definition" in stats["definition"]
    assert stats["n_days"] > 300
    assert 0 <= stats["modal_ramp_hour_ist"] <= 23


def test_ramp_table_is_indexed_by_hour(frame):
    table = evaluate.ramp_table(frame)
    assert list(table.index) == list(range(24))
    assert "residual_load_mw" in table
    assert "demand_mw_p90" in table


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


def test_seasonal_naive_shifts_by_its_lag(frame):
    model = baselines.SeasonalNaive(24)
    prediction = model.predict(frame)
    assert np.isclose(prediction.iloc[24], frame[config.TARGET].iloc[0])
    assert prediction.iloc[:24].isna().all()


def test_seasonal_naive_rejects_an_unavailable_lag():
    with pytest.raises(ValueError, match="not available at day-ahead"):
        baselines.SeasonalNaive(1)


def test_climatology_is_fitted_only_on_what_it_is_given(frame):
    split = splits.chronological_split(frame)
    model = baselines.HourWeekdayClimatology().fit(frame.loc[split.train])
    prediction = model.predict(frame)
    assert prediction.notna().all()
    # Prediction depends only on (hour, weekday), so it repeats weekly.
    first = prediction.iloc[0]
    assert np.isclose(prediction.iloc[168], first)


def test_ridge_baseline_beats_a_constant(frame):
    from solaris import features as feature_mod

    featured = feature_mod.build_feature_frame(
        frame.assign(
            temperature_2m_fc_pop=25.0, temperature_2m_fc_equal=25.0,
            temperature_2m_fc_region=25.0, temperature_2m_fc_max=30.0,
            temperature_2m_fc_spread=9.0,
        )
    ).dropna()
    split = splits.chronological_split(featured)
    model = baselines.RidgeBaseline().fit(featured.loc[split.train])
    prediction = model.predict(featured.loc[split.test])

    actual = featured.loc[split.test, config.TARGET]
    model_mae = evaluate.point_metrics(actual, prediction)["mae_mw"]
    constant = pd.Series(featured.loc[split.train, config.TARGET].mean(), index=actual.index)
    constant_mae = evaluate.point_metrics(actual, constant)["mae_mw"]
    assert model_mae < constant_mae
