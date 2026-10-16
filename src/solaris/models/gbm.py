"""LightGBM point and quantile models for day-ahead demand.

Two specifications are trained from the same code path so that the only thing
separating them is the feature set:

* ``calendar_history`` -- hour, weekday, holiday, lagged and rolling demand.
* ``weather``          -- the above plus day-ahead forecast temperature and the
                          degree-hour / heat-accumulation terms derived from it.

The difference between the two on the held-out test block is the project's
answer to "does weather add information beyond calendar and load history?".

Hyperparameters are fixed, modest and chosen on the *validation* block only.
Nothing is tuned against the test block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from solaris import config, features as feature_mod

# Deliberately conservative. The dataset is ~20k hourly rows, which is small
# enough that a deep, heavily-tuned ensemble would overfit the calendar.
DEFAULT_PARAMS: dict = {
    "objective": "regression",
    "metric": "l1",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.85,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": config.RANDOM_SEED,
    "deterministic": True,
    "force_row_wise": True,
}

MAX_ROUNDS = 3000
EARLY_STOPPING_ROUNDS = 100


# The anchor for the differenced target. It must itself be a legal day-ahead
# feature; `demand_lag_24h` is the same hour of the previous day, which is
# observed well before the 09:00 D-1 issue time.
DELTA_ANCHOR = "demand_lag_24h"


@dataclass
class LightGBMForecaster:
    """A single-output LightGBM regressor over one named feature group.

    ``target_mode`` controls what the booster actually learns:

    * ``"level"`` -- predict demand directly. Simple, but a gradient-boosted
      tree cannot predict outside the range of targets it was trained on. All-
      India demand grew steadily across 2021-2023 and the held-out test block
      contains hours above the training maximum, so a level model is
      structurally incapable of reaching them. It is kept and reported because
      that failure is a real property of the method, not something to hide.

    * ``"delta"`` -- predict ``demand(t) - demand(t-24h)`` and add the anchor
      back at prediction time. The difference is far closer to stationary, so
      the model can follow the load growth instead of saturating at the
      training maximum. This is standard practice in load forecasting and is
      the specification used for the headline results.
    """

    feature_group: str = "weather"
    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    name: str = ""
    quantile: float | None = None
    target_mode: str = "delta"

    def __post_init__(self) -> None:
        if self.target_mode not in ("level", "delta"):
            raise ValueError(f"Unknown target_mode: {self.target_mode!r}")
        self.features = feature_mod.feature_list(self.feature_group)
        feature_mod.assert_no_leakage(self.features)
        if self.target_mode == "delta" and DELTA_ANCHOR not in self.features:
            raise ValueError(
                f"target_mode='delta' needs {DELTA_ANCHOR!r} in the feature set."
            )
        if not self.name:
            suffix = "" if self.quantile is None else f"_q{int(self.quantile * 100)}"
            mode = "" if self.target_mode == "delta" else "_level"
            self.name = f"lgbm_{self.feature_group}{mode}{suffix}"
        if self.quantile is not None:
            self.params = dict(self.params)
            self.params.update(
                {"objective": "quantile", "alpha": self.quantile, "metric": "quantile"}
            )
        self.best_iteration_: int | None = None

    def fit(
        self,
        train: pd.DataFrame,
        valid: pd.DataFrame | None = None,
        target: str = config.TARGET,
    ) -> "LightGBMForecaster":
        """Fit on ``train``, early-stopping on ``valid`` when one is supplied."""
        import lightgbm as lgb

        X_train, y_train = _xy(train, self.features, target, self.target_mode)
        datasets = [lgb.Dataset(X_train, y_train, free_raw_data=False)]
        names = ["train"]

        callbacks = [lgb.log_evaluation(period=0)]
        if valid is not None and len(valid):
            X_valid, y_valid = _xy(valid, self.features, target, self.target_mode)
            if len(X_valid):
                datasets.append(lgb.Dataset(X_valid, y_valid, reference=datasets[0]))
                names.append("valid")
                callbacks.append(
                    lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)
                )

        self.booster_ = lgb.train(
            self.params,
            datasets[0],
            num_boost_round=MAX_ROUNDS,
            valid_sets=datasets[1:] or None,
            valid_names=names[1:] or None,
            callbacks=callbacks,
        )
        self.best_iteration_ = self.booster_.best_iteration or MAX_ROUNDS
        self.n_train_rows_ = int(len(X_train))
        self.train_range_ = (str(train.index.min()), str(train.index.max()))
        return self

    def predict(self, frame: pd.DataFrame, **_kwargs) -> pd.Series:
        X = frame[self.features].astype(float)
        mask = X.notna().all(axis=1)
        out = pd.Series(np.nan, index=frame.index)
        if mask.any():
            raw = self.booster_.predict(X.loc[mask], num_iteration=self.best_iteration_)
            if self.target_mode == "delta":
                # The booster learned a change; add the persistence anchor back.
                raw = raw + frame.loc[mask, DELTA_ANCHOR].to_numpy()
            out.loc[mask] = raw
        return out

    # ---- explainability -------------------------------------------------

    def gain_importance(self) -> pd.Series:
        """Total split gain per feature, normalised to sum to 1."""
        gains = pd.Series(
            self.booster_.feature_importance(importance_type="gain"),
            index=self.booster_.feature_name(),
        ).sort_values(ascending=False)
        total = gains.sum()
        return gains / total if total else gains

    def shap_values(self, frame: pd.DataFrame, max_rows: int = 2000) -> tuple:
        """SHAP values on a deterministic subsample of ``frame``.

        Returns ``(shap_matrix, sampled_X)``. Sampling is evenly spaced in time
        rather than random, so the sample spans the whole evaluation period.
        """
        import shap

        X = frame[self.features].astype(float).dropna()
        if len(X) > max_rows:
            step = max(1, len(X) // max_rows)
            X = X.iloc[::step][:max_rows]
        explainer = shap.TreeExplainer(self.booster_)
        return explainer.shap_values(X), X

    # ---- persistence ----------------------------------------------------

    def save(self, directory: Path | None = None) -> Path:
        """Write the booster plus a metadata sidecar describing how it was fitted."""
        directory = directory or config.MODELS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        model_path = directory / f"{self.name}.txt"
        self.booster_.save_model(str(model_path), num_iteration=self.best_iteration_)

        meta = {
            "name": self.name,
            "feature_group": self.feature_group,
            "features": self.features,
            "target_mode": self.target_mode,
            "delta_anchor": DELTA_ANCHOR if self.target_mode == "delta" else None,
            "quantile": self.quantile,
            "params": self.params,
            "best_iteration": self.best_iteration_,
            "n_train_rows": getattr(self, "n_train_rows_", None),
            "train_range_ist": getattr(self, "train_range_", None),
            "target": config.TARGET,
            "target_unit": config.POWER_UNIT,
            "random_seed": config.RANDOM_SEED,
        }
        (directory / f"{self.name}.meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return model_path


@dataclass
class QuantileEnsemble:
    """P10/P50/P90 band from three independently fitted quantile models.

    Quantile regression fits each level separately, so the fitted quantiles can
    in principle cross. ``predict`` sorts the three predictions per row to
    enforce monotonicity, and records how often that was necessary.
    """

    feature_group: str = "weather"
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)
    name: str = "lgbm_quantile"
    target_mode: str = "delta"

    def fit(
        self,
        train: pd.DataFrame,
        valid: pd.DataFrame | None = None,
        target: str = config.TARGET,
    ) -> "QuantileEnsemble":
        self.models_ = {}
        for q in self.quantiles:
            model = LightGBMForecaster(
                feature_group=self.feature_group,
                quantile=q,
                target_mode=self.target_mode,
            )
            model.fit(train, valid, target=target)
            self.models_[q] = model
        return self

    def predict(self, frame: pd.DataFrame, **_kwargs) -> pd.DataFrame:
        raw = pd.DataFrame(
            {f"q{int(q * 100)}": m.predict(frame) for q, m in self.models_.items()},
            index=frame.index,
        )
        columns = list(raw.columns)
        sorted_values = np.sort(raw.to_numpy(), axis=1)
        out = pd.DataFrame(sorted_values, index=raw.index, columns=columns)
        self.n_crossings_ = int((raw.to_numpy() != sorted_values).any(axis=1).sum())
        return out

    def save(self, directory: Path | None = None) -> list[Path]:
        return [m.save(directory) for m in self.models_.values()]


def _xy(
    frame: pd.DataFrame,
    features: list[str],
    target: str,
    target_mode: str = "level",
) -> tuple[pd.DataFrame, pd.Series]:
    """Aligned X and y, dropping rows with any missing feature, target or anchor."""
    needed = list(dict.fromkeys(features + [target]))
    if target_mode == "delta" and DELTA_ANCHOR not in needed:
        needed.append(DELTA_ANCHOR)
    subset = frame[needed].astype(float).dropna()
    y = subset[target]
    if target_mode == "delta":
        y = y - subset[DELTA_ANCHOR]
    return subset[features], y
