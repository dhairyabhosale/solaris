"""Tests for feature engineering, with the leakage rules as first-class checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaris import config, features


@pytest.fixture
def frame() -> pd.DataFrame:
    """Thirty days of synthetic hourly data with a realistic daily shape."""
    index = pd.date_range("2023-01-01", periods=24 * 30, freq="1h", tz=config.IST)
    hour = index.hour.to_numpy()
    rng = np.random.default_rng(0)
    demand = (
        150_000
        + 20_000 * np.sin((hour - 6) / 24 * 2 * np.pi)
        + rng.normal(0, 1_000, len(index))
    )
    temperature = 25 + 8 * np.sin((hour - 9) / 24 * 2 * np.pi)
    columns = {
        config.TARGET: demand,
        "temperature_2m_fc_pop": temperature,
        "temperature_2m_fc_equal": temperature - 0.5,
        "temperature_2m_fc_region": temperature - 0.3,
        "temperature_2m_fc_max": temperature + 5,
        "temperature_2m_fc_spread": np.full(len(index), 9.0),
        "temperature_2m_obs": temperature + 1,
    }
    # Per-region forecast temperature, offset so the regions are distinguishable.
    for offset, region in enumerate(config.REGIONS):
        columns[f"temperature_2m_fc_region_{region}"] = temperature + offset
    return pd.DataFrame(columns, index=index)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def test_calendar_features_present(frame):
    out = features.add_calendar_features(frame)
    for name in features.CALENDAR_FEATURES:
        assert name in out, name


def test_calendar_cyclic_encoding(frame):
    out = features.add_calendar_features(frame)
    # sin^2 + cos^2 == 1 for every row.
    total = out["hour_sin"] ** 2 + out["hour_cos"] ** 2
    assert np.allclose(total, 1.0)


def test_weekend_flag(frame):
    out = features.add_calendar_features(frame)
    assert set(out["is_weekend"].unique()) <= {0, 1}
    saturday = out[out.index.dayofweek == 5]
    assert (saturday["is_weekend"] == 1).all()
    monday = out[out.index.dayofweek == 0]
    assert (monday["is_weekend"] == 0).all()


def test_holiday_flag_marks_republic_day(frame):
    """26 January is a national holiday and must be flagged."""
    out = features.add_calendar_features(frame)
    republic_day = out[out.index.normalize() == pd.Timestamp("2023-01-26", tz=config.IST)]
    assert len(republic_day) == 24
    assert (republic_day["is_holiday"] == 1).all()
    ordinary = out[out.index.normalize() == pd.Timestamp("2023-01-24", tz=config.IST)]
    assert (ordinary["is_holiday"] == 0).all()


# --------------------------------------------------------------------------
# History features and their lag discipline
# --------------------------------------------------------------------------


def test_history_lags_are_exact(frame):
    out = features.add_history_features(frame)
    assert np.isclose(out["demand_lag_24h"].iloc[24], frame[config.TARGET].iloc[0])
    assert np.isclose(out["demand_lag_168h"].iloc[168], frame[config.TARGET].iloc[0])


def test_history_lags_leading_nans(frame):
    out = features.add_history_features(frame)
    assert out["demand_lag_24h"].iloc[:24].isna().all()
    assert out["demand_lag_168h"].iloc[:168].isna().all()


def test_same_hour_rolling_never_includes_target_hour(frame):
    """The 7-day same-hour mean must be built only from already-observed hours."""
    out = features.add_history_features(frame)
    row = 24 * 10 + 13  # an hour well past the warm-up
    timestamp = out.index[row]
    value = out["demand_same_hour_mean_7d"].iloc[row]

    same_hour_history = frame[config.TARGET][
        (frame.index.hour == timestamp.hour) & (frame.index < timestamp)
    ]
    expected = same_hour_history.iloc[-7:].mean()
    assert np.isclose(value, expected)


def test_daily_aggregates_use_only_past_days(frame):
    out = features.add_history_features(frame)
    day = pd.Timestamp("2023-01-15", tz=config.IST)
    rows = out[out.index.normalize() == day]
    previous = frame[config.TARGET][frame.index.normalize() == day - pd.Timedelta(days=1)]

    # Every hour of 15 Jan sees exactly the 14 Jan aggregate, and nothing newer.
    assert np.allclose(rows["demand_daily_mean_lag_1d"], previous.mean())
    assert np.allclose(rows["demand_daily_max_lag_1d"], previous.max())


def test_history_features_do_not_see_the_future(frame):
    """Truncating the future must not change any already-computed feature."""
    full = features.add_history_features(frame)
    cutoff = frame.index[24 * 20]
    truncated = features.add_history_features(frame[frame.index <= cutoff])

    for name in features.HISTORY_FEATURES:
        a = full.loc[truncated.index, name]
        b = truncated[name]
        pd.testing.assert_series_equal(a, b, check_names=False, rtol=1e-9)


# --------------------------------------------------------------------------
# Weather features
# --------------------------------------------------------------------------


def test_weather_features_present(frame):
    out = features.add_weather_features(frame)
    for name in features.WEATHER_FEATURES:
        assert name in out, name


def test_degree_hours(frame):
    out = features.add_weather_features(frame)
    temp = out["temperature_2m_fc"]
    assert np.allclose(out["cdd_fc"], np.clip(temp - features.CDD_BASE_C, 0, None))
    assert np.allclose(out["hdd_fc"], np.clip(features.HDD_BASE_C - temp, 0, None))
    assert (out["cdd_fc"] >= 0).all()
    assert (out["hdd_fc"] >= 0).all()


def test_weather_weighting_selects_the_right_column(frame):
    pop = features.add_weather_features(frame, weighting="pop")
    equal = features.add_weather_features(frame, weighting="equal")
    assert np.allclose(pop["temperature_2m_fc"], frame["temperature_2m_fc_pop"])
    assert np.allclose(equal["temperature_2m_fc"], frame["temperature_2m_fc_equal"])


def test_weather_features_reject_missing_column(frame):
    with pytest.raises(KeyError, match="temperature_2m_fc_nonsense"):
        features.add_weather_features(frame, weighting="nonsense")


def test_daily_max_is_constant_within_a_day(frame):
    out = features.add_weather_features(frame)
    for _, group in out.groupby(out.index.normalize()):
        assert group["temp_fc_daily_max"].nunique() == 1


# --------------------------------------------------------------------------
# Feature-set assembly
# --------------------------------------------------------------------------


def test_build_feature_frame_drops_observed_weather(frame):
    out = features.build_feature_frame(frame)
    assert not [c for c in out.columns if c.endswith("_obs")]


def test_build_feature_frame_can_keep_observed_for_retrospective_work(frame):
    out = features.build_feature_frame(frame, include_observed=True)
    assert "temperature_2m_obs" in out


def test_feature_list_groups_nest():
    calendar = set(features.feature_list("calendar"))
    history = set(features.feature_list("calendar_history"))
    weather = set(features.feature_list("weather"))
    assert calendar < history < weather


def test_feature_list_rejects_unknown_group():
    with pytest.raises(ValueError, match="Unknown feature group"):
        features.feature_list("magic")


# --------------------------------------------------------------------------
# Leakage guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["calendar", "calendar_history", "weather"])
def test_declared_feature_sets_are_leakage_free(group):
    features.assert_no_leakage(features.feature_list(group))


def test_leakage_guard_rejects_observed_weather():
    with pytest.raises(ValueError, match="observed weather"):
        features.assert_no_leakage(["hour", "temperature_2m_obs"])


@pytest.mark.parametrize(
    "name",
    ["demand_mw", "solar_mw", "wind_mw", "residual_load_mw", "generation_mw", "vre_mw"],
)
def test_leakage_guard_rejects_contemporaneous_grid_columns(name):
    with pytest.raises(ValueError, match="measured at the target hour"):
        features.assert_no_leakage(["hour", name])


@pytest.mark.parametrize("lag", [1, 2, 6, 23])
def test_leakage_guard_rejects_short_demand_lags(lag):
    with pytest.raises(ValueError, match="shorter than 24 h"):
        features.assert_no_leakage([f"demand_lag_{lag}h"])


@pytest.mark.parametrize("lag", [24, 48, 168])
def test_leakage_guard_allows_day_ahead_safe_lags(lag):
    features.assert_no_leakage([f"demand_lag_{lag}h"])


def test_build_feature_frame_output_passes_the_guard(frame):
    """The assembled weather feature set must survive its own guard."""
    out = features.build_feature_frame(frame)
    selected = features.feature_list("weather")
    features.assert_no_leakage(selected)
    for name in selected:
        assert name in out.columns
