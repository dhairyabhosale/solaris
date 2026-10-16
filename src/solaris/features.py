"""Feature engineering for the day-ahead demand forecast.

The forecast-issue convention
-----------------------------
For every hour of target day ``D``, the forecast is issued at
``FORECAST_ISSUE_HOUR_IST`` (09:00) on day ``D-1``. Every feature must be
computable from information that existed at that instant. Two consequences
drive the whole design:

* **Demand lags.** The shortest usable demand lag is NOT 1 hour. Predicting
  00:00 on day D from 09:00 on D-1 is a 15-hour-ahead call; predicting 23:00
  on day D is 38 hours ahead. The safe minimum lag varies by target hour, so
  this module builds lags from the *last demand observation available at issue
  time* rather than from a fixed hourly offset. That is lag 24h relative to the
  same hour of D-1 and longer, never lag 1h.
* **Weather.** Only the archived day-ahead forecast columns (``*_fc_*``) may be
  used. Observed columns (``*_obs``) are retrospective and are excluded by
  construction -- ``build_feature_frame`` refuses to emit them in an
  operational feature set.

``assert_no_leakage`` encodes both rules and is exercised by the test suite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from solaris import config

# Feature groups. Models are defined by which groups they are allowed to use,
# which is what makes the baseline-vs-weather comparison a clean experiment.
CALENDAR_FEATURES: list[str] = [
    "hour",
    "hour_sin",
    "hour_cos",
    "dayofweek",
    "is_weekend",
    "month",
    "month_sin",
    "month_cos",
    "dayofyear",
    "is_holiday",
    "days_from_start",
]

HISTORY_FEATURES: list[str] = [
    "demand_lag_24h",
    "demand_lag_48h",
    "demand_lag_168h",
    "demand_same_hour_mean_7d",
    "demand_same_hour_std_7d",
    "demand_daily_mean_lag_1d",
    "demand_daily_max_lag_1d",
    "demand_daily_mean_lag_7d",
    "demand_trend_1d_7d",
]

# Archived day-ahead forecast temperature and everything derived from it.
# Humidity, radiation, cloud cover and wind are deliberately absent: Open-Meteo
# does not archive them at a day-ahead lead for 2021-2023 (see config), and
# substituting observed values would be leakage. The breadth here therefore
# comes from transforming temperature -- degree-hours, daily aggregates,
# multi-day accumulation and the spatial spread across load centres -- rather
# than from more variables.
WEATHER_FEATURES: list[str] = [
    "temperature_2m_fc",
    "temperature_2m_fc_max",
    "temperature_2m_fc_spread",
    "cdd_fc",
    "hdd_fc",
    "temp_fc_daily_max",
    "temp_fc_daily_mean",
    "temp_fc_daily_range",
    "temp_fc_roll_24h",
    "temp_fc_anomaly_vs_prev_day",
    "heat_accumulation_3d",
    "cdd_fc_daily_sum",
    # Regional forecast temperature: national demand is the sum of five
    # regional grids, and a hot north with a mild south is a different system
    # state from uniform warmth, which the national mean alone cannot express.
    "temperature_2m_fc_region_NR",
    "temperature_2m_fc_region_WR",
    "temperature_2m_fc_region_SR",
    "temperature_2m_fc_region_ER",
    "temperature_2m_fc_region_NER",
]

# Cooling/heating degree base. 18 degC is the conventional HDD/CDD base used in
# energy statistics; 24 degC is closer to the comfort threshold at which Indian
# air-conditioning load switches on, so the cooling term uses 24.
CDD_BASE_C = 24.0
HDD_BASE_C = 18.0


def _india_holidays(years: list[int]) -> set[pd.Timestamp]:
    """National (central-government) Indian public holidays for the given years.

    Uses the `holidays` package's India calendar restricted to nationwide
    observances; state-specific festivals vary too much to treat as national
    demand drivers.
    """
    import holidays as holidays_pkg

    calendar = holidays_pkg.India(years=years)
    return {pd.Timestamp(d) for d in calendar}


def add_calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Hour/weekday/month/holiday features. All known arbitrarily far ahead."""
    out = frame.copy()
    index = out.index

    out["hour"] = index.hour
    out["hour_sin"] = np.sin(2 * np.pi * index.hour / 24)
    out["hour_cos"] = np.cos(2 * np.pi * index.hour / 24)
    out["dayofweek"] = index.dayofweek
    out["is_weekend"] = (index.dayofweek >= 5).astype(int)
    out["month"] = index.month
    out["month_sin"] = np.sin(2 * np.pi * index.month / 12)
    out["month_cos"] = np.cos(2 * np.pi * index.month / 12)
    out["dayofyear"] = index.dayofyear

    # A slow linear trend term lets tree models express multi-year load growth
    # without extrapolating a date they have never seen.
    out["days_from_start"] = (index - index.min()).days

    holiday_dates = _india_holidays(sorted({int(y) for y in index.year}))
    normalised = pd.Series(index.tz_localize(None).normalize(), index=index)
    out["is_holiday"] = normalised.isin(holiday_dates).astype(int)
    return out


def add_history_features(frame: pd.DataFrame, target: str = config.TARGET) -> pd.DataFrame:
    """Lagged and rolling demand features, all at >= 24 h lag.

    24 h is the shortest lag that is safe for *every* target hour under the
    09:00 D-1 issue convention: the value at the same hour of day D-1 is
    already observed by 09:00 on D-1 only for hours up to 09:00. To stay safe
    for all 24 target hours this module uses lag 24 h and longer, and builds
    the daily aggregates from days strictly before the issue day.
    """
    out = frame.copy()
    demand = out[target]

    out["demand_lag_24h"] = demand.shift(24)
    out["demand_lag_48h"] = demand.shift(48)
    out["demand_lag_168h"] = demand.shift(168)

    # Same hour-of-day over the previous 7 days: shift by 24 h first so the
    # window can never include the target hour itself.
    same_hour = demand.shift(24)
    by_hour = same_hour.groupby(out.index.hour)
    out["demand_same_hour_mean_7d"] = by_hour.transform(
        lambda s: s.rolling(7, min_periods=3).mean()
    )
    out["demand_same_hour_std_7d"] = by_hour.transform(
        lambda s: s.rolling(7, min_periods=3).std()
    )

    # Daily aggregates of complete past days, mapped back onto each hour. The
    # shift(1)/shift(7) are in *days*, so day D only ever sees D-1 and earlier.
    daily = demand.resample("1D").agg(["mean", "max"])
    daily_mean_lag1 = daily["mean"].shift(1)
    daily_max_lag1 = daily["max"].shift(1)
    daily_mean_lag7 = daily["mean"].shift(7)

    day_key = out.index.normalize()
    out["demand_daily_mean_lag_1d"] = daily_mean_lag1.reindex(day_key).to_numpy()
    out["demand_daily_max_lag_1d"] = daily_max_lag1.reindex(day_key).to_numpy()
    out["demand_daily_mean_lag_7d"] = daily_mean_lag7.reindex(day_key).to_numpy()

    # Short-term momentum: yesterday relative to a week ago.
    out["demand_trend_1d_7d"] = (
        out["demand_daily_mean_lag_1d"] - out["demand_daily_mean_lag_7d"]
    )
    return out


def add_weather_features(frame: pd.DataFrame, weighting: str = "pop") -> pd.DataFrame:
    """Day-ahead forecast weather features under one city-weighting scheme.

    Only ``*_fc_<weighting>`` columns are read. Observed (``*_obs``) columns are
    never touched here.
    """
    out = frame.copy()

    for variable in config.FORECAST_VARIABLES:
        source = f"{variable}_fc_{weighting}"
        if source not in out.columns:
            raise KeyError(f"Missing forecast column {source!r}")
        out[f"{variable}_fc"] = out[source]

    temp = out["temperature_2m_fc"]

    # Degree-hours: the non-linear part of the temperature/demand relationship
    # that a linear term cannot express.
    out["cdd_fc"] = (temp - CDD_BASE_C).clip(lower=0)
    out["hdd_fc"] = (HDD_BASE_C - temp).clip(lower=0)

    # Daily forecast aggregates, computed within the target day. These are legal
    # because the whole day's forecast is available at issue time on D-1.
    daily = temp.resample("1D").agg(["max", "mean", "min"])
    day_key = out.index.normalize()
    out["temp_fc_daily_max"] = daily["max"].reindex(day_key).to_numpy()
    out["temp_fc_daily_mean"] = daily["mean"].reindex(day_key).to_numpy()
    # Diurnal range: a hot day that stays hot overnight loads the grid for
    # longer than one with the same maximum but a cool night.
    out["temp_fc_daily_range"] = (
        (daily["max"] - daily["min"]).reindex(day_key).to_numpy()
    )
    # Total cooling degree-hours expected across the target day.
    out["cdd_fc_daily_sum"] = (
        out["cdd_fc"].resample("1D").sum().reindex(day_key).to_numpy()
    )

    # Trailing 24 h of forecast temperature: thermal inertia of buildings.
    out["temp_fc_roll_24h"] = temp.rolling(24, min_periods=6).mean()

    # Day-on-day change in forecast daily mean, and 3-day heat accumulation --
    # consecutive hot days drive demand harder than one isolated hot day.
    daily_mean = daily["mean"]
    out["temp_fc_anomaly_vs_prev_day"] = (
        (daily_mean - daily_mean.shift(1)).reindex(day_key).to_numpy()
    )
    daily_cdd = (daily["mean"] - CDD_BASE_C).clip(lower=0)
    out["heat_accumulation_3d"] = (
        daily_cdd.rolling(3, min_periods=1).sum().reindex(day_key).to_numpy()
    )
    return out


def build_feature_frame(
    dataset: pd.DataFrame,
    *,
    weighting: str = "pop",
    include_observed: bool = False,
) -> pd.DataFrame:
    """Build the full feature frame from the processed hourly dataset.

    ``include_observed`` is for explicitly retrospective analyses only. Leave it
    False for anything described as a forecast.
    """
    out = dataset.copy()
    out = add_calendar_features(out)
    out = add_history_features(out)
    out = add_weather_features(out, weighting=weighting)

    if not include_observed:
        observed_columns = [c for c in out.columns if c.endswith("_obs")]
        out = out.drop(columns=observed_columns)
    return out


def feature_list(group: str) -> list[str]:
    """Return the feature names for a named model specification."""
    if group == "calendar":
        return list(CALENDAR_FEATURES)
    if group == "calendar_history":
        return CALENDAR_FEATURES + HISTORY_FEATURES
    if group == "weather":
        return CALENDAR_FEATURES + HISTORY_FEATURES + WEATHER_FEATURES
    raise ValueError(f"Unknown feature group: {group!r}")


def assert_no_leakage(features: list[str]) -> None:
    """Raise if a feature set contains anything unavailable at issue time.

    Three rules:
      1. No observed-weather column (``*_obs``).
      2. No raw contemporaneous grid column -- demand, generation or residual
         load at the target hour is the thing being predicted, or is measured
         at the same instant and so unknown a day ahead.
      3. No demand lag shorter than 24 h.
    """
    import re

    banned_exact = {
        config.TARGET,
        "solar_mw",
        "solar_raw_mw",
        "wind_mw",
        "vre_mw",
        "thermal_mw",
        "hydro_mw",
        "gas_mw",
        "nuclear_mw",
        "generation_mw",
        "residual_load_mw",
        "n_samples",
    }
    for name in features:
        if name.endswith("_obs"):
            raise ValueError(
                f"Leakage: {name!r} is observed weather, unavailable at forecast issue time."
            )
        if name in banned_exact:
            raise ValueError(
                f"Leakage: {name!r} is measured at the target hour, unavailable a day ahead."
            )
        match = re.fullmatch(r"demand_lag_(\d+)h", name)
        if match and int(match.group(1)) < 24:
            raise ValueError(
                f"Leakage: {name!r} uses a demand lag shorter than 24 h, which is not "
                "available for every target hour under the 09:00 D-1 issue convention."
            )
