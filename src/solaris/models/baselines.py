"""Naive and linear baselines the weather model has to beat.

A forecasting result only means something relative to what a despatch engineer
could do with no model at all. Three reference points are provided:

* ``SeasonalNaive`` -- the same hour of the previous day, or the previous week.
  Free, instant, and surprisingly hard to beat on a smooth national load curve.
* ``HourWeekdayClimatology`` -- the mean demand for this hour-of-week over a
  trailing window.
* ``RidgeBaseline`` -- a linear model on calendar and demand-history features,
  scaled with statistics fitted on the training rows only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from solaris import config


@dataclass
class SeasonalNaive:
    """Predict the value observed ``lag_hours`` earlier.

    Lag 24 (yesterday, same hour) and lag 168 (last week, same hour) are both
    legal under the 09:00 D-1 issue convention: both reference an hour that has
    already happened when the forecast is made.
    """

    lag_hours: int = 24
    name: str = ""

    def __post_init__(self) -> None:
        if self.lag_hours < 24:
            raise ValueError(
                "A lag below 24 h is not available at day-ahead forecast issue time."
            )
        if not self.name:
            self.name = f"naive_lag_{self.lag_hours}h"

    def fit(self, *_args, **_kwargs) -> "SeasonalNaive":
        """Nothing to fit; present for interface symmetry with the other models."""
        return self

    def predict(self, frame: pd.DataFrame, target: str = config.TARGET) -> pd.Series:
        return frame[target].shift(self.lag_hours).reindex(frame.index)


@dataclass
class HourWeekdayClimatology:
    """Mean demand per (hour-of-day, weekday), fitted on training rows only."""

    name: str = "climatology_hour_weekday"

    def fit(self, frame: pd.DataFrame, target: str = config.TARGET) -> "HourWeekdayClimatology":
        key = [frame.index.hour, frame.index.dayofweek]
        self._table = frame[target].groupby(key).mean()
        self._global_mean = float(frame[target].mean())
        return self

    def predict(self, frame: pd.DataFrame, **_kwargs) -> pd.Series:
        keys = pd.MultiIndex.from_arrays([frame.index.hour, frame.index.dayofweek])
        values = self._table.reindex(keys).to_numpy()
        values = np.where(np.isnan(values), self._global_mean, values)
        return pd.Series(values, index=frame.index)


@dataclass
class RidgeBaseline:
    """Ridge regression on calendar + demand-history features.

    The scaler is fitted on the training slice only and then applied unchanged
    to validation and test, which is what keeps the evaluation honest.
    """

    features: list[str] | None = None
    alpha: float = 1.0
    name: str = "ridge_calendar_history"

    def fit(self, frame: pd.DataFrame, target: str = config.TARGET) -> "RidgeBaseline":
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        from solaris import features as feature_mod

        self.features = self.features or feature_mod.feature_list("calendar_history")
        feature_mod.assert_no_leakage(self.features)

        X, y = _xy(frame, self.features, target)
        self._pipeline = Pipeline(
            [("scale", StandardScaler()), ("model", Ridge(alpha=self.alpha, random_state=None))]
        )
        self._pipeline.fit(X, y)
        return self

    def predict(self, frame: pd.DataFrame, **_kwargs) -> pd.Series:
        X = frame[self.features].astype(float)
        mask = X.notna().all(axis=1)
        out = pd.Series(np.nan, index=frame.index)
        if mask.any():
            out.loc[mask] = self._pipeline.predict(X.loc[mask])
        return out


def _xy(frame: pd.DataFrame, features: list[str], target: str) -> tuple[pd.DataFrame, pd.Series]:
    """Drop rows with any missing feature or target; return aligned X and y."""
    subset = frame[features + [target]].astype(float).dropna()
    return subset[features], subset[target]
