"""Export the research results as a static JSON bundle for the web app.

Why this exists
---------------
The dashboard is a read-only view of results the pipeline has already
computed. Reading them straight from parquet would force the deployed web
function to carry pandas, numpy and pyarrow -- roughly 250 MB of wheels for
work that amounts to slicing a table that never changes.

So the pipeline writes everything the app needs as plain JSON into
``app/data/``, and the web layer reads it with nothing but the standard
library. That keeps the serverless bundle small and cold starts fast, and it
makes the no-hand-written-numbers rule structural: the app can only serve what
this module exported from ``reports/evaluation.json``.

Run via ``python -m solaris.cli report`` (or ``export-web``).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd

from solaris import config

logger = logging.getLogger(__name__)

WEB_DATA_DIR = config.PROJECT_ROOT / "app" / "data"

HEADLINE_MODEL = "lgbm_weather"
BASELINE_MODEL = "lgbm_calendar_history"
NAIVE_MODEL = "naive_lag_24h"

# Columns the dashboard plots. Anything not listed is not shipped.
SERIES_COLUMNS = (
    config.TARGET,
    HEADLINE_MODEL,
    BASELINE_MODEL,
    NAIVE_MODEL,
    "q10",
    "q50",
    "q90",
    "temperature_2m_fc",
    "solar_mw",
    "wind_mw",
    "residual_load_mw",
    "residual_ramp_mw_per_h",
    "is_hot_period",
    "is_evening",
)

# Hourly values are rounded before export. Demand is measured in megawatts on a
# ~200,000 MW system, so a tenth of a megawatt is far below measurement
# resolution; rounding roughly halves the payload with no visible effect.
ROUND_DECIMALS = 1


def _clean(value: Any) -> Any:
    """JSON-safe scalar: NaN becomes null, numpy scalars become Python types."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, ROUND_DECIMALS)
    return value


def _write(name: str, payload: Any) -> Path:
    WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = WEB_DATA_DIR / f"{name}.json"
    path.write_text(
        json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8"
    )
    return path


def _evaluation() -> dict:
    path = config.REPORTS_DIR / "evaluation.json"
    if not path.exists():
        raise FileNotFoundError(
            "reports/evaluation.json not found. Run `python -m solaris.cli train`."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _data_quality() -> dict:
    return json.loads(config.DATA_QUALITY_JSON.read_text(encoding="utf-8"))


def _model_version() -> dict:
    path = config.MODELS_DIR / f"{HEADLINE_MODEL}.meta.json"
    if not path.exists():
        return {"name": HEADLINE_MODEL, "available": False}
    meta = json.loads(path.read_text(encoding="utf-8"))
    return {
        "name": meta["name"],
        "available": True,
        "target_mode": meta.get("target_mode"),
        "n_features": len(meta.get("features", [])),
        "best_iteration": meta.get("best_iteration"),
        "train_range_ist": meta.get("train_range_ist"),
        "n_train_rows": meta.get("n_train_rows"),
        "random_seed": meta.get("random_seed"),
    }


# --------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------


def build_overview(metrics: dict, quality: dict) -> dict:
    headline = metrics["models"][HEADLINE_MODEL]
    overall = headline["test"]["overall"]
    peak = headline["test_peak"]
    contribution = metrics["weather_contribution"]
    coverage = quality["coverage"]

    return {
        "purpose": (
            "Day-ahead hourly forecasting of all-India electricity demand using "
            "archived weather forecasts, evaluated on a held-out period."
        ),
        "data_coverage": {
            "first_hour_ist": coverage["first_hour_ist"],
            "last_hour_ist": coverage["last_hour_ist"],
            "hours": coverage["hours_present"],
            "pct_complete": coverage["pct_complete"],
            "is_historical": True,
            "refresh_note": (
                "This is a fixed historical research dataset covering "
                f"{coverage['first_hour_ist'][:10]} to {coverage['last_hour_ist'][:10]}. "
                "It is not a live feed and does not update."
            ),
        },
        "test_period": metrics["split"]["test"],
        "kpis": [
            {
                "label": "Day-ahead MAE",
                "value": overall["mae_mw"],
                "unit": "MW",
                "detail": f"{overall['mape_pct']:.2f}% MAPE on "
                          f"{overall['n']:,} held-out hours",
            },
            {
                "label": "Better than same-hour-yesterday",
                "value": headline["skill_vs_naive_24h_pct"],
                "unit": "%",
                "detail": "Reduction in MAE against the naive 24-hour "
                          "persistence baseline",
            },
            {
                "label": "Gain from weather features",
                "value": contribution["mae_reduction_pct"],
                "unit": "%",
                "detail": "MAE reduction against the identical model without "
                          "weather inputs",
            },
            {
                "label": "Daily peak error",
                "value": peak["peak_mae_mw"],
                "unit": "MW",
                "detail": f"{peak['peak_mape_pct']:.2f}% of peak; peak hour "
                          f"correct within 1 h on "
                          f"{peak['peak_hour_within_1h_pct']:.0f}% of days",
            },
        ],
        "model_version": _model_version(),
        "forecast_convention": metrics["forecast_convention"],
    }


def _family(name: str) -> str:
    if name.startswith("naive") or name.startswith("climatology"):
        return "naive baseline"
    if name.startswith("ridge"):
        return "linear baseline"
    if name.endswith("_level"):
        return "gradient boosting (level target)"
    if "weather" in name:
        return "gradient boosting + weather"
    return "gradient boosting"


def build_models(metrics: dict) -> dict:
    rows = []
    for name, entry in metrics["models"].items():
        overall = entry["test"]["overall"]
        if overall.get("mae_mw") is None:
            continue
        hot = entry["test"].get("hot_period", {})
        evening = entry["test"].get("evening_window", {})
        peak = entry.get("test_peak", {})
        rows.append({
            "model": name,
            "family": _family(name),
            "mae_mw": overall["mae_mw"],
            "rmse_mw": overall["rmse_mw"],
            "mape_pct": overall["mape_pct"],
            "bias_mw": overall["bias_mw"],
            "r2": overall["r2"],
            "skill_vs_naive_pct": entry.get("skill_vs_naive_24h_pct"),
            "mae_hot_mw": hot.get("mae_mw") if hot.get("sufficient") else None,
            "mae_evening_mw": evening.get("mae_mw") if evening.get("sufficient") else None,
            "peak_mae_mw": peak.get("peak_mae_mw"),
            "peak_hour_mae_h": peak.get("peak_hour_mae_h"),
            "n_hours": overall["n"],
        })
    return {
        "models": sorted(rows, key=lambda r: r["mae_mw"]),
        "weather_contribution": metrics["weather_contribution"],
        "extrapolation": metrics.get("extrapolation"),
    }


def build_series() -> dict:
    """The full test-period series, as parallel arrays.

    Exported once as a single document. At ~7,300 hours and 14 columns this is
    a few hundred kilobytes gzipped, which is cheaper to ship whole than to
    paginate -- and it lets the date-window control in the dashboard filter
    client-side with no round trip.
    """
    path = config.PROCESSED_DIR / "test_predictions.parquet"
    if not path.exists():
        raise FileNotFoundError(
            "data/processed/test_predictions.parquet not found. "
            "Run `python -m solaris.cli train`."
        )
    frame = pd.read_parquet(path)
    columns = [c for c in SERIES_COLUMNS if c in frame.columns]

    payload: dict = {
        "timestamps": [t.isoformat() for t in frame.index],
        "unit": config.POWER_UNIT,
        "timezone": config.IST_NAME,
        "first": frame.index.min().isoformat(),
        "last": frame.index.max().isoformat(),
        "hours": int(len(frame)),
        "columns": {},
    }
    for name in columns:
        series = frame[name]
        if series.dtype == bool:
            payload["columns"][name] = [bool(v) for v in series]
        else:
            payload["columns"][name] = [_clean(v) for v in series]
    return payload


def build_daily_profile() -> dict:
    path = config.TABLES_DIR / "daily_profile.csv"
    if not path.exists():
        raise FileNotFoundError(
            "reports/tables/daily_profile.csv not found. "
            "Run `python -m solaris.cli report`."
        )
    frame = pd.read_csv(path, index_col=0)
    return {
        "hours": [int(h) for h in frame.index],
        "columns": {c: [_clean(v) for v in frame[c]] for c in frame.columns},
        "unit": "MW",
    }


def build_evening_stress(metrics: dict) -> dict:
    return {
        "full_period": metrics.get("evening_ramp_full_period", {}),
        "test_period": metrics.get("evening_ramp", {}),
    }


def build_explainability(metrics: dict) -> dict:
    return {
        "gain_importance": metrics.get("feature_importance_gain", {}),
        "model": HEADLINE_MODEL,
        "caveat": (
            "Split gain measures how much each feature improved the training "
            "objective. It shows what the model relies on, not what causes "
            "demand. Correlated features share credit arbitrarily, and nothing "
            "here identifies a causal effect of temperature on demand."
        ),
    }


def build_methodology(metrics: dict, quality: dict) -> dict:
    return {
        "forecast_convention": metrics["forecast_convention"],
        "split": metrics["split"],
        "hot_period": metrics.get("hot_period"),
        "evening_ramp_definition": metrics.get("evening_ramp_full_period", {}).get(
            "definition"
        ),
        "extrapolation": metrics.get("extrapolation"),
        "source_sampling": quality.get("source_sampling", {}).get("note"),
        "weighting_sensitivity": metrics.get("weighting_sensitivity"),
        "hot_threshold_sensitivity": metrics.get("hot_threshold_sensitivity"),
        "rolling_backtest": metrics.get("rolling_backtest", {}).get("mean_mae_mw"),
        "quantile": metrics.get("quantile"),
        "sources": [
            {
                "name": "Grid-India / NERLDC SCADA demand and generation",
                "via": "Mendeley Data, doi:10.17632/y58jknpgs8.1",
                "licence": config.MENDELEY_LICENCE,
                "url": config.MENDELEY_LANDING_URL,
                "note": (
                    "Published as '1-hour interval'; the files are actually "
                    "5-minute and 10-second SCADA exports. This project "
                    "aggregates them to true hourly means."
                ),
            },
            {
                "name": "Open-Meteo Previous Runs API (archived day-ahead forecasts)",
                "via": config.OPEN_METEO_PREVIOUS_RUNS_URL,
                "licence": "CC BY 4.0",
                "url": "https://open-meteo.com/en/docs/previous-runs-api",
                "note": (
                    "Only temperature_2m is archived at a day-ahead lead for "
                    "2021-2023, so it is the only weather input the model uses."
                ),
            },
            {
                "name": "Open-Meteo Historical Weather API (observed)",
                "via": config.OPEN_METEO_ARCHIVE_URL,
                "licence": "CC BY 4.0",
                "url": "https://open-meteo.com/en/docs/historical-weather-api",
                "note": "Retrospective analysis only; never a model input.",
            },
        ],
        "limitations": [
            "Only forecast temperature is available at a genuine day-ahead lead "
            "from the archived-forecast source; humidity, radiation, cloud cover "
            "and wind forecasts are not, so they are excluded rather than "
            "replaced with observations.",
            "The national weather index is population-weighted across 18 load "
            "centres. Population is a proxy for, not a measurement of, each "
            "region's share of electricity demand.",
            "Hot periods are research-defined by a temperature percentile. They "
            "are not India Meteorological Department heatwave declarations.",
            "Residual load is metered demand minus SCADA solar and wind. It "
            "excludes behind-the-meter rooftop solar, interchange and storage, "
            "so it indicates the evening ramp rather than an operator net load.",
            "Relationships shown between temperature and demand are predictive "
            "associations, not causal estimates.",
            "The P10-P90 interval is under-dispersed on the test period and "
            "should not be read as a calibrated operational risk band.",
            "The dataset ends on 31 December 2023. Nothing here is live, and no "
            "2024-2026 demand record has been evaluated.",
        ],
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

# Saved Plotly figures the dashboard renders directly.
EXPORTED_FIGURES = (
    "temperature_vs_demand",
    "evening_ramp",
    "error_by_hour",
    "residuals",
    "quantile_calibration",
    "peak_scatter",
)


def export_all() -> list[Path]:
    """Write every JSON document the web app reads."""
    metrics = _evaluation()
    quality = _data_quality()

    written: list[Path] = [
        _write("overview", build_overview(metrics, quality)),
        _write("models", build_models(metrics)),
        _write("series", build_series()),
        _write("daily_profile", build_daily_profile()),
        _write("evening_stress", build_evening_stress(metrics)),
        _write("explainability", build_explainability(metrics)),
        _write("methodology", build_methodology(metrics, quality)),
    ]

    figures_dir = WEB_DATA_DIR / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    available: list[str] = []
    for name in EXPORTED_FIGURES:
        source = config.FIGURES_DIR / f"{name}.json"
        if not source.exists():
            logger.warning("figure %s not found, skipping", name)
            continue
        target = figures_dir / f"{name}.json"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)
        available.append(name)

    written.append(_write("figures_index", {"figures": available}))
    return written
