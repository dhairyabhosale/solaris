"""The end-to-end modelling experiment: fit every model, evaluate, write results.

This module is the single source of every number the project reports. Running
``python -m solaris.cli train`` executes it and rewrites ``reports/``; nothing
downstream (documentation, frontend) may contain a metric that this run did not
produce.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from solaris import config, evaluate, features as feature_mod
from solaris.data import build as build_mod
from solaris.models import baselines, gbm, splits

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Everything one run produced, in memory."""

    frame: pd.DataFrame
    split: splits.Split
    predictions: pd.DataFrame
    quantiles: pd.DataFrame
    metrics: dict
    models: dict


def prepare_frame(weighting: str = "pop") -> pd.DataFrame:
    """Load the processed dataset, build features, keep only usable rows."""
    dataset = build_mod.load_dataset()
    frame = feature_mod.build_feature_frame(dataset, weighting=weighting)

    # Rows flagged invalid by the data-quality checks (SCADA dropouts, thinly
    # sampled hours) are dropped from modelling but were already counted in the
    # quality report, so the exclusion is visible rather than silent.
    if "is_valid" in frame:
        frame = frame[frame["is_valid"]]

    # The target itself must be present.
    frame = frame[frame[config.TARGET].notna()]
    return frame


def run(weighting: str = "pop", *, fit_quantiles: bool = True) -> ExperimentResult:
    """Fit every model on the training block and evaluate on the held-out test block."""
    frame = prepare_frame(weighting)
    split = splits.chronological_split(frame)

    train = frame.loc[split.train]
    valid = frame.loc[split.valid]
    test = frame.loc[split.test]

    logger.info(
        "split: train=%d valid=%d test=%d hours", len(train), len(valid), len(test)
    )
    if len(test) < 24 * 30:
        raise ValueError(
            f"Test block has only {len(test)} hours; refusing to report metrics on it."
        )

    predictions = pd.DataFrame(index=frame.index)
    models: dict = {}

    # -- Naive baselines. They need the full history to look back into, so they
    #    are evaluated against `frame`, not just the test slice.
    for lag in (24, 168):
        model = baselines.SeasonalNaive(lag_hours=lag)
        predictions[model.name] = model.predict(frame)
        models[model.name] = model

    # -- Climatology and ridge, fitted on training rows only.
    climatology = baselines.HourWeekdayClimatology().fit(train)
    predictions[climatology.name] = climatology.predict(frame)
    models[climatology.name] = climatology

    ridge = baselines.RidgeBaseline().fit(train)
    predictions[ridge.name] = ridge.predict(frame)
    models[ridge.name] = ridge

    # -- The experiment proper: identical algorithm and hyperparameters, two
    #    feature sets, two target parameterisations. The feature-set contrast
    #    answers "does weather help?"; the target contrast documents why a
    #    level-target tree fails on a growing load series.
    for mode in ("level", "delta"):
        for group in ("calendar_history", "weather"):
            model = gbm.LightGBMForecaster(feature_group=group, target_mode=mode)
            model.fit(train, valid)
            predictions[model.name] = model.predict(frame)
            models[model.name] = model
            logger.info(
                "%s: best_iteration=%s", model.name, model.best_iteration_
            )

    # -- Probabilistic forecast.
    quantiles = pd.DataFrame(index=frame.index)
    if fit_quantiles:
        ensemble = gbm.QuantileEnsemble(feature_group="weather")
        ensemble.fit(train, valid)
        quantiles = ensemble.predict(frame)
        models["lgbm_quantile"] = ensemble

    metrics = _build_metrics(frame, split, predictions, quantiles, models, weighting)
    return ExperimentResult(frame, split, predictions, quantiles, metrics, models)


def _build_metrics(
    frame: pd.DataFrame,
    split: splits.Split,
    predictions: pd.DataFrame,
    quantiles: pd.DataFrame,
    models: dict,
    weighting: str,
) -> dict:
    """Assemble the full metrics document from held-out predictions."""
    test = frame.loc[split.test]
    valid = frame.loc[split.valid]
    actual = test[config.TARGET]

    results: dict = {
        "dataset": {
            "weighting_scheme": weighting,
            "hours_modelled": int(len(frame)),
            "first_hour_ist": str(frame.index.min()),
            "last_hour_ist": str(frame.index.max()),
            "target": config.TARGET,
            "unit": config.POWER_UNIT,
            "timezone": config.IST_NAME,
        },
        "split": split.describe(),
        "forecast_convention": (
            f"Day-ahead: every hour of day D is predicted from information "
            f"available at {config.FORECAST_ISSUE_HOUR_IST:02d}:00 IST on day D-1. "
            "Weather inputs are archived day-ahead forecasts, never observations."
        ),
        "models": {},
    }

    # Hot-period definition travels with the metrics so no table can be quoted
    # without it.
    if "is_hot_period" in test:
        hot_hours = int(test["is_hot_period"].fillna(False).sum())
        results["hot_period"] = {
            "definition": frame.attrs.get(
                "hot_period_definition", "research-defined percentile threshold"
            ),
            "threshold_c": frame.attrs.get("hot_period_threshold_c"),
            "percentile": frame.attrs.get("hot_period_percentile"),
            "test_hours_hot": hot_hours,
            "test_hours_total": int(len(test)),
            "test_pct_hot": round(100 * hot_hours / len(test), 2) if len(test) else None,
        }

    for name in predictions.columns:
        test_pred = predictions.loc[split.test, name]
        valid_pred = predictions.loc[split.valid, name]
        entry = {
            "test": evaluate.sliced_metrics(test, test_pred),
            "test_peak": evaluate.peak_metrics(actual, test_pred),
            "valid": evaluate.point_metrics(valid[config.TARGET], valid_pred),
        }
        model = models.get(name)
        if isinstance(model, gbm.LightGBMForecaster):
            entry["best_iteration"] = model.best_iteration_
            entry["n_features"] = len(model.features)
            entry["n_train_rows"] = model.n_train_rows_
        results["models"][name] = entry

    # -- Headline comparisons, computed rather than asserted.
    reference = results["models"]["naive_lag_24h"]["test"]["overall"]
    for name, entry in results["models"].items():
        entry["skill_vs_naive_24h_pct"] = evaluate.skill_vs(
            entry["test"]["overall"], reference
        )

    # Why the level-target variant underperforms: trees cannot predict above
    # the largest target they were trained on. Quantify that directly.
    train_max = float(frame.loc[split.train, config.TARGET].max())
    test_actual = frame.loc[split.test, config.TARGET]
    results["extrapolation"] = {
        "train_max_demand_mw": train_max,
        "test_max_demand_mw": float(test_actual.max()),
        "test_hours_above_train_max": int((test_actual > train_max).sum()),
        "test_hours_total": int(len(test_actual)),
        "level_model_max_prediction_mw": float(
            predictions.loc[split.test, "lgbm_weather_level"].max()
        ),
        "delta_model_max_prediction_mw": float(
            predictions.loc[split.test, "lgbm_weather"].max()
        ),
        "note": (
            "A gradient-boosted tree predicts a weighted average of training "
            "leaf values, so a level-target model cannot exceed the maximum "
            "demand it saw in training. The delta-target model predicts the "
            "change from the previous day's same hour and adds that anchor "
            "back, so it follows load growth beyond the training range."
        ),
    }

    baseline_overall = results["models"]["lgbm_calendar_history"]["test"]["overall"]
    weather_overall = results["models"]["lgbm_weather"]["test"]["overall"]
    results["weather_contribution"] = {
        "comparison": "lgbm_weather vs lgbm_calendar_history (identical algorithm, "
                      "hyperparameters and delta target; only the feature set differs)",
        "mae_mw_without_weather": baseline_overall["mae_mw"],
        "mae_mw_with_weather": weather_overall["mae_mw"],
        "mae_reduction_mw": (
            baseline_overall["mae_mw"] - weather_overall["mae_mw"]
            if baseline_overall["mae_mw"] and weather_overall["mae_mw"] else None
        ),
        "mae_reduction_pct": evaluate.skill_vs(weather_overall, baseline_overall),
        "rmse_reduction_pct": evaluate.skill_vs(
            weather_overall, baseline_overall, key="rmse_mw"
        ),
    }
    for slice_name in ("hot_period", "non_hot_period", "evening_window", "top_decile_demand"):
        with_weather = results["models"]["lgbm_weather"]["test"].get(slice_name, {})
        without = results["models"]["lgbm_calendar_history"]["test"].get(slice_name, {})
        if with_weather.get("sufficient") and without.get("sufficient"):
            results["weather_contribution"][f"mae_reduction_pct__{slice_name}"] = (
                evaluate.skill_vs(with_weather, without)
            )
        else:
            results["weather_contribution"][f"mae_reduction_pct__{slice_name}"] = None

    # -- Paired significance test on the daily MAE difference. Hourly errors are
    #    strongly autocorrelated, so the test is run on daily means, which are
    #    far closer to independent.
    results["weather_contribution"]["significance"] = _paired_test(
        actual,
        predictions.loc[split.test, "lgbm_calendar_history"],
        predictions.loc[split.test, "lgbm_weather"],
    )

    # -- Quantile calibration, measured on the test block and, for diagnosis,
    #    on the validation block the quantile models were early-stopped against.
    #    A model that is calibrated on validation but not on test is suffering
    #    distribution shift, not a coding error, and the two numbers together
    #    are what distinguishes the two cases.
    if not quantiles.empty:
        results["quantile"] = evaluate.quantile_calibration(
            actual, quantiles.loc[split.test]
        )
        results["quantile"]["validation_calibration"] = evaluate.quantile_calibration(
            valid[config.TARGET], quantiles.loc[split.valid]
        )
        ensemble = models.get("lgbm_quantile")
        if ensemble is not None:
            results["quantile"]["n_rows_with_crossing_before_sort"] = getattr(
                ensemble, "n_crossings_", None
            )
        # Demand variability in each block, which is what an interval has to
        # span. If the test block is far more variable than validation, an
        # interval tuned on validation will be too narrow.
        results["quantile"]["target_volatility"] = {
            block: {
                "std_mw": float(frame.loc[index, config.TARGET].std()),
                "daily_range_mean_mw": float(
                    frame.loc[index, config.TARGET].resample("1D").agg(
                        lambda s: s.max() - s.min()
                    ).mean()
                ),
            }
            for block, index in (
                ("train", split.train), ("valid", split.valid), ("test", split.test)
            )
        }

    # -- Evening residual-load behaviour, measured not modelled.
    results["evening_ramp"] = evaluate.evening_ramp_stats(test)
    results["evening_ramp_full_period"] = evaluate.evening_ramp_stats(frame)

    # -- Feature importance for the headline model.
    weather_model = models["lgbm_weather"]
    importance = weather_model.gain_importance()
    results["feature_importance_gain"] = {
        k: float(v) for k, v in importance.head(25).items()
    }

    return results


def _paired_test(actual: pd.Series, pred_a: pd.Series, pred_b: pd.Series) -> dict:
    """Paired test on daily mean absolute error: does B beat A reliably?

    Uses both a paired t-test and a Wilcoxon signed-rank test, because daily
    MAE differences are not guaranteed to be normal.
    """
    from scipy import stats

    joined = pd.DataFrame({"a": actual, "pa": pred_a, "pb": pred_b}).dropna()
    if len(joined) < 48:
        return {"n_days": 0, "note": "insufficient matched rows"}

    joined["err_a"] = (joined["pa"] - joined["a"]).abs()
    joined["err_b"] = (joined["pb"] - joined["a"]).abs()
    daily = joined.groupby(joined.index.normalize())[["err_a", "err_b"]].mean()
    if len(daily) < 10:
        return {"n_days": int(len(daily)), "note": "insufficient days"}

    difference = daily["err_a"] - daily["err_b"]  # positive => B better
    t_stat, t_p = stats.ttest_rel(daily["err_a"], daily["err_b"])
    try:
        w_stat, w_p = stats.wilcoxon(daily["err_a"], daily["err_b"])
    except ValueError:
        w_stat, w_p = float("nan"), float("nan")

    # 95% CI on the mean daily MAE improvement.
    se = difference.std(ddof=1) / np.sqrt(len(difference))
    half_width = float(stats.t.ppf(0.975, len(difference) - 1) * se)
    mean_difference = float(difference.mean())

    return {
        "n_days": int(len(daily)),
        "mean_daily_mae_improvement_mw": mean_difference,
        "ci95_mw": [mean_difference - half_width, mean_difference + half_width],
        "days_improved_pct": float((difference > 0).mean() * 100),
        "paired_t_statistic": float(t_stat),
        "paired_t_pvalue": float(t_p),
        "wilcoxon_statistic": float(w_stat),
        "wilcoxon_pvalue": float(w_p),
        "note": (
            "Computed on daily mean absolute error to reduce the effect of "
            "hour-to-hour error autocorrelation. Positive improvement means the "
            "weather model has lower error."
        ),
    }


# --------------------------------------------------------------------------
# Rolling-origin backtest
# --------------------------------------------------------------------------


def rolling_backtest(
    weighting: str = "pop",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Refit monthly over an expanding window and score each month out-of-sample.

    This is a stricter test than the single holdout: every month is predicted by
    a model that saw only data before it.
    """
    frame = prepare_frame(weighting)
    start = start or config.TRAIN_END
    end = end or config.TEST_END
    folds = splits.rolling_origin_folds(frame, start, end)

    rows = []
    for fold in folds:
        train = frame.loc[fold.train]
        test = frame.loc[fold.test]
        # Hold back the last 14 days of training for early stopping.
        cutoff = train.index.max() - pd.Timedelta(days=14)
        inner_train = train[train.index <= cutoff]
        inner_valid = train[train.index > cutoff]

        fold_row: dict = {"fold": fold.label, "test_hours": int(len(test)),
                          "train_hours": int(len(train))}
        for group in ("calendar_history", "weather"):
            model = gbm.LightGBMForecaster(feature_group=group)
            model.fit(inner_train, inner_valid)
            prediction = model.predict(test)
            metrics = evaluate.point_metrics(test[config.TARGET], prediction)
            fold_row[f"{group}_mae_mw"] = metrics["mae_mw"]
            fold_row[f"{group}_rmse_mw"] = metrics["rmse_mw"]

        naive = baselines.SeasonalNaive(24).predict(frame).reindex(test.index)
        fold_row["naive_24h_mae_mw"] = evaluate.point_metrics(
            test[config.TARGET], naive
        )["mae_mw"]
        rows.append(fold_row)
        logger.info("fold %s done", fold.label)

    table = pd.DataFrame(rows)
    summary = {
        "n_folds": int(len(table)),
        "scheme": "expanding window, monthly refit, out-of-sample month scored",
        "folds": table.to_dict(orient="records"),
    }
    if not table.empty:
        summary["mean_mae_mw"] = {
            "naive_lag_24h": float(table["naive_24h_mae_mw"].mean()),
            "lgbm_calendar_history": float(table["calendar_history_mae_mw"].mean()),
            "lgbm_weather": float(table["weather_mae_mw"].mean()),
        }
        improvement = (
            table["calendar_history_mae_mw"] - table["weather_mae_mw"]
        ) / table["calendar_history_mae_mw"] * 100
        summary["weather_mae_reduction_pct_per_fold"] = {
            "mean": float(improvement.mean()),
            "min": float(improvement.min()),
            "max": float(improvement.max()),
            "folds_improved": int((improvement > 0).sum()),
        }
    return summary


# --------------------------------------------------------------------------
# Sensitivity analyses
# --------------------------------------------------------------------------


def weighting_sensitivity() -> dict:
    """Refit the weather model under each city-weighting scheme.

    Answers "how much does the choice of weather aggregation change the result?"
    """
    from solaris.data.weather import WEIGHTINGS

    out: dict = {}
    for scheme in WEIGHTINGS:
        frame = prepare_frame(scheme)
        split = splits.chronological_split(frame)
        train, valid, test = (
            frame.loc[split.train], frame.loc[split.valid], frame.loc[split.test]
        )
        model = gbm.LightGBMForecaster(feature_group="weather")
        model.fit(train, valid)
        metrics = evaluate.point_metrics(test[config.TARGET], model.predict(test))

        reference = gbm.LightGBMForecaster(feature_group="calendar_history")
        reference.fit(train, valid)
        reference_metrics = evaluate.point_metrics(
            test[config.TARGET], reference.predict(test)
        )
        out[scheme] = {
            "mae_mw": metrics["mae_mw"],
            "rmse_mw": metrics["rmse_mw"],
            "mae_reduction_vs_no_weather_pct": evaluate.skill_vs(
                metrics, reference_metrics
            ),
        }
    return out


def hot_threshold_sensitivity(result: ExperimentResult) -> dict:
    """Recompute hot-period metrics across several percentile thresholds."""
    frame = result.frame
    split = result.split
    test = frame.loc[split.test]
    column = "temperature_2m_fc_pop"
    if column not in frame:
        return {}

    train_daily_max = frame.loc[split.train, column].resample("1D").max()
    test_daily_max = test[column].resample("1D").max()

    out: dict = {}
    for percentile in config.HOT_PERIOD_SENSITIVITY:
        threshold = float(train_daily_max.quantile(percentile))
        hot_days = test_daily_max >= threshold
        mask = pd.Series(
            hot_days.reindex(test.index.normalize()).to_numpy(), index=test.index
        ).fillna(False)

        entry: dict = {
            "threshold_c": threshold,
            "test_hours_hot": int(mask.sum()),
        }
        if mask.sum() >= evaluate.MIN_SLICE_ROWS:
            for name in ("lgbm_calendar_history", "lgbm_weather", "naive_lag_24h"):
                prediction = result.predictions.loc[test.index[mask], name]
                entry[name] = evaluate.point_metrics(
                    test.loc[mask, config.TARGET], prediction
                )["mae_mw"]
            entry["sufficient"] = True
        else:
            entry["sufficient"] = False
        out[f"p{int(percentile * 100)}"] = entry
    return out


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save(result: ExperimentResult, extras: dict | None = None) -> Path:
    """Write metrics, predictions and model artefacts to disk."""
    config.ensure_dirs()
    payload = dict(result.metrics)
    if extras:
        payload.update(extras)

    metrics_path = config.REPORTS_DIR / "evaluation.json"
    metrics_path.write_text(json.dumps(payload, indent=2, default=_json_default),
                            encoding="utf-8")

    # Test-period predictions, for the figures and the web app.
    test_index = result.split.test
    output = result.frame.loc[test_index, [config.TARGET]].copy()
    for column in result.predictions.columns:
        output[column] = result.predictions.loc[test_index, column]
    for column in result.quantiles.columns:
        output[column] = result.quantiles.loc[test_index, column]
    for column in ("is_hot_period", "is_evening", "temperature_2m_fc",
                   "solar_mw", "wind_mw", "residual_load_mw",
                   "residual_ramp_mw_per_h"):
        if column in result.frame:
            output[column] = result.frame.loc[test_index, column]
    output.to_parquet(config.PROCESSED_DIR / "test_predictions.parquet")

    for model in result.models.values():
        if hasattr(model, "save"):
            model.save()

    return metrics_path


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return str(value)
    return str(value)
