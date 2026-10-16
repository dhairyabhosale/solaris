"""Evaluation metrics and sliced reporting for the demand forecast.

Everything here reports in megawatts, the unit of the source SCADA data, with
a GW figure alongside where a number is large enough that MW is hard to read.
No metric in this module is hard-coded or hand-entered; every table the project
publishes is produced by these functions from held-out predictions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from solaris import config


# --------------------------------------------------------------------------
# Point-forecast metrics
# --------------------------------------------------------------------------


def point_metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    """MAE, RMSE, MAPE, bias and R^2 over the rows where both are present."""
    joined = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    if joined.empty:
        return {"n": 0, "mae_mw": None, "rmse_mw": None, "mape_pct": None,
                "bias_mw": None, "r2": None}

    error = joined["predicted"] - joined["actual"]
    mae = float(error.abs().mean())
    rmse = float(np.sqrt((error ** 2).mean()))
    mape = float((error.abs() / joined["actual"].abs()).mean() * 100)
    bias = float(error.mean())

    total_ss = float(((joined["actual"] - joined["actual"].mean()) ** 2).sum())
    resid_ss = float((error ** 2).sum())
    r2 = 1 - resid_ss / total_ss if total_ss > 0 else None

    return {
        "n": int(len(joined)),
        "mae_mw": mae,
        "mae_gw": mae / 1000,
        "rmse_mw": rmse,
        "rmse_gw": rmse / 1000,
        "mape_pct": mape,
        "bias_mw": bias,
        "r2": r2,
    }


def skill_vs(metrics: dict, reference: dict, key: str = "mae_mw") -> float | None:
    """Percentage error reduction of ``metrics`` against ``reference``.

    Positive means the model is better than the reference. Returns None when
    either side is missing, rather than a misleading zero.
    """
    a, b = metrics.get(key), reference.get(key)
    if a is None or b is None or not b:
        return None
    return float((b - a) / b * 100)


# --------------------------------------------------------------------------
# Daily peak metrics
# --------------------------------------------------------------------------


def peak_metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    """Error in the daily peak demand level and in the hour it occurs.

    Only days with a full 24 hours of matched actual and predicted values are
    scored; a partial day would make the peak meaningless.
    """
    joined = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    if joined.empty:
        return {"n_days": 0}

    joined["date"] = joined.index.normalize()
    complete = joined.groupby("date").filter(lambda g: len(g) == 24)
    if complete.empty:
        return {"n_days": 0}

    rows = []
    for date, group in complete.groupby("date"):
        actual_peak = group["actual"].max()
        predicted_peak = group["predicted"].max()
        actual_hour = group["actual"].idxmax().hour
        predicted_hour = group["predicted"].idxmax().hour
        # Hour-of-day is circular: 23:00 vs 00:00 is one hour apart, not 23.
        diff = abs(predicted_hour - actual_hour)
        rows.append(
            {
                "date": date,
                "actual_peak_mw": actual_peak,
                "predicted_peak_mw": predicted_peak,
                "peak_error_mw": predicted_peak - actual_peak,
                "actual_peak_hour": actual_hour,
                "predicted_peak_hour": predicted_hour,
                "peak_hour_error_h": min(diff, 24 - diff),
            }
        )

    table = pd.DataFrame(rows)
    peak_mae = float(table["peak_error_mw"].abs().mean())
    return {
        "n_days": int(len(table)),
        "peak_mae_mw": peak_mae,
        "peak_mae_gw": peak_mae / 1000,
        "peak_bias_mw": float(table["peak_error_mw"].mean()),
        "peak_mape_pct": float(
            (table["peak_error_mw"].abs() / table["actual_peak_mw"]).mean() * 100
        ),
        "peak_hour_mae_h": float(table["peak_hour_error_h"].mean()),
        "peak_hour_exact_pct": float((table["peak_hour_error_h"] == 0).mean() * 100),
        "peak_hour_within_1h_pct": float((table["peak_hour_error_h"] <= 1).mean() * 100),
    }


def peak_table(actual: pd.Series, predicted: pd.Series) -> pd.DataFrame:
    """Per-day peak comparison, for plotting and for the worst-case listing."""
    joined = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    joined["date"] = joined.index.normalize()
    complete = joined.groupby("date").filter(lambda g: len(g) == 24)
    if complete.empty:
        return pd.DataFrame()

    grouped = complete.groupby("date")
    table = pd.DataFrame(
        {
            "actual_peak_mw": grouped["actual"].max(),
            "predicted_peak_mw": grouped["predicted"].max(),
            "actual_peak_hour": grouped["actual"].idxmax().dt.hour,
            "predicted_peak_hour": grouped["predicted"].idxmax().dt.hour,
        }
    )
    table["peak_error_mw"] = table["predicted_peak_mw"] - table["actual_peak_mw"]
    return table


# --------------------------------------------------------------------------
# Sliced evaluation
# --------------------------------------------------------------------------

MIN_SLICE_ROWS = 200  # below this a sliced metric is too noisy to report


def sliced_metrics(
    frame: pd.DataFrame,
    predicted: pd.Series,
    target: str = config.TARGET,
) -> dict:
    """Metrics overall and on the slices the research question asks about.

    Slices with fewer than ``MIN_SLICE_ROWS`` matched hours are reported with
    their row count and a ``sufficient: False`` flag rather than a metric, so a
    thin slice can never be quoted as a headline result.
    """
    actual = frame[target]
    out: dict = {"overall": point_metrics(actual, predicted)}

    slices: dict[str, pd.Series] = {}
    if "is_hot_period" in frame:
        hot = frame["is_hot_period"].fillna(False).astype(bool)
        slices["hot_period"] = hot
        slices["non_hot_period"] = ~hot
    if "is_evening" in frame:
        evening = frame["is_evening"].fillna(False).astype(bool)
        slices["evening_window"] = evening
        slices["non_evening"] = ~evening
    if "is_hot_period" in frame and "is_evening" in frame:
        slices["hot_evening"] = (
            frame["is_hot_period"].fillna(False).astype(bool)
            & frame["is_evening"].fillna(False).astype(bool)
        )

    # Top-decile demand hours: the hours a system operator actually worries
    # about. The threshold comes from the evaluated period itself and is
    # reported alongside the metric.
    threshold = actual.quantile(0.90)
    slices["top_decile_demand"] = actual >= threshold

    for name, mask in slices.items():
        subset_actual = actual[mask]
        subset_pred = predicted.reindex(subset_actual.index)
        matched = pd.concat([subset_actual, subset_pred], axis=1).dropna()
        if len(matched) < MIN_SLICE_ROWS:
            out[name] = {"n": int(len(matched)), "sufficient": False}
            continue
        metrics = point_metrics(subset_actual, subset_pred)
        metrics["sufficient"] = True
        out[name] = metrics

    out["top_decile_demand"]["threshold_mw"] = float(threshold)
    out["by_hour"] = hourly_breakdown(actual, predicted)
    return out


def hourly_breakdown(actual: pd.Series, predicted: pd.Series) -> dict:
    """MAE for each hour of the day, to expose when the model struggles."""
    joined = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    if joined.empty:
        return {}
    error = (joined["predicted"] - joined["actual"]).abs()
    by_hour = error.groupby(joined.index.hour).agg(["mean", "size"])
    return {
        int(hour): {"mae_mw": float(row["mean"]), "n": int(row["size"])}
        for hour, row in by_hour.iterrows()
    }


# --------------------------------------------------------------------------
# Quantile / probabilistic metrics
# --------------------------------------------------------------------------


def pinball_loss(actual: pd.Series, predicted: pd.Series, quantile: float) -> float | None:
    """Mean pinball (quantile) loss in MW."""
    joined = pd.DataFrame({"a": actual, "p": predicted}).dropna()
    if joined.empty:
        return None
    error = joined["a"] - joined["p"]
    loss = np.maximum(quantile * error, (quantile - 1) * error)
    return float(loss.mean())


def quantile_calibration(
    actual: pd.Series,
    quantile_frame: pd.DataFrame,
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
) -> dict:
    """Empirical coverage and pinball loss for each fitted quantile.

    A well-calibrated P90 should sit above the actual value 90% of the time.
    The gap between empirical and nominal coverage is the calibration error.
    """
    out: dict = {"quantiles": {}}
    for q in quantiles:
        column = f"q{int(q * 100)}"
        if column not in quantile_frame:
            continue
        joined = pd.DataFrame({"a": actual, "p": quantile_frame[column]}).dropna()
        if joined.empty:
            out["quantiles"][column] = {"n": 0}
            continue
        empirical = float((joined["a"] <= joined["p"]).mean())
        out["quantiles"][column] = {
            "n": int(len(joined)),
            "nominal_coverage": q,
            "empirical_coverage": empirical,
            "calibration_error": empirical - q,
            "pinball_loss_mw": pinball_loss(joined["a"], joined["p"], q),
        }

    # Central interval coverage, if both tails were fitted.
    if "q10" in quantile_frame and "q90" in quantile_frame:
        joined = pd.DataFrame(
            {"a": actual, "lo": quantile_frame["q10"], "hi": quantile_frame["q90"]}
        ).dropna()
        if not joined.empty:
            inside = ((joined["a"] >= joined["lo"]) & (joined["a"] <= joined["hi"])).mean()
            out["interval_p10_p90"] = {
                "nominal_coverage": 0.8,
                "empirical_coverage": float(inside),
                "mean_width_mw": float((joined["hi"] - joined["lo"]).mean()),
            }
    return out


# --------------------------------------------------------------------------
# Residual load and evening ramp
# --------------------------------------------------------------------------


def evening_ramp_stats(frame: pd.DataFrame) -> dict:
    """Describe the evening residual-load ramp actually present in the data.

    Reported on the RESEARCH residual load defined in ``solaris.data.grid``:
    metered all-India demand minus SCADA solar and wind. It is not an
    operator's net-load definition and is labelled as such everywhere.
    """
    if "residual_load_mw" not in frame:
        return {}

    daily_rows = []
    for date, group in frame.groupby(frame.index.normalize()):
        if len(group) != 24:
            continue
        ramp = group["residual_ramp_mw_per_h"]
        # The steepest 3-hour rise in residual load anywhere in the evening.
        evening = group[group["is_evening"]] if "is_evening" in group else group
        if evening.empty:
            continue
        rolling3 = group["residual_load_mw"].diff(3)
        daily_rows.append(
            {
                "date": date,
                "max_hourly_ramp_mw": float(ramp.max()) if ramp.notna().any() else np.nan,
                "max_hourly_ramp_hour": int(ramp.idxmax().hour) if ramp.notna().any() else -1,
                "max_3h_ramp_mw": float(rolling3.max()) if rolling3.notna().any() else np.nan,
                "evening_peak_residual_mw": float(evening["residual_load_mw"].max()),
                "evening_peak_hour": int(evening["residual_load_mw"].idxmax().hour),
                "daily_peak_demand_mw": float(group[config.TARGET].max()),
                "solar_at_1700_mw": float(
                    group.between_time("17:00", "17:00")["solar_mw"].mean()
                ),
            }
        )

    if not daily_rows:
        return {}

    table = pd.DataFrame(daily_rows)
    hour_counts = table["max_hourly_ramp_hour"].value_counts().sort_index()
    return {
        "n_days": int(len(table)),
        "definition": (
            "Research residual load = metered all-India demand (NLDC_DEMAND|P) "
            "minus SCADA all-India solar (clipped at 0) and wind. Not an "
            "operator net-load definition; excludes behind-the-meter solar, "
            "interchange and storage."
        ),
        "mean_max_hourly_ramp_mw": float(table["max_hourly_ramp_mw"].mean()),
        "p95_max_hourly_ramp_mw": float(table["max_hourly_ramp_mw"].quantile(0.95)),
        "max_observed_hourly_ramp_mw": float(table["max_hourly_ramp_mw"].max()),
        "mean_max_3h_ramp_mw": float(table["max_3h_ramp_mw"].mean()),
        "p95_max_3h_ramp_mw": float(table["max_3h_ramp_mw"].quantile(0.95)),
        "modal_ramp_hour_ist": int(hour_counts.idxmax()),
        "ramp_hour_distribution": {int(h): int(n) for h, n in hour_counts.items()},
        "modal_evening_peak_hour_ist": int(table["evening_peak_hour"].mode().iloc[0]),
    }


def ramp_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Mean residual load, demand, solar and wind by hour of day."""
    columns = [c for c in ("demand_mw", "solar_mw", "wind_mw", "residual_load_mw",
                           "residual_ramp_mw_per_h") if c in frame]
    grouped = frame.groupby(frame.index.hour)
    table = grouped[columns].mean()
    table.index.name = "hour_ist"
    for column in columns:
        table[f"{column}_p10"] = grouped[column].quantile(0.10)
        table[f"{column}_p90"] = grouped[column].quantile(0.90)
    return table
