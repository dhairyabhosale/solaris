"""Join grid and weather data into the processed analysis dataset, with checks."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from solaris import config
from solaris.data import grid, weather

logger = logging.getLogger(__name__)

# Plausibility bounds for the all-India demand signal, in MW. All-India peak
# demand has never approached 400 GW and the grid never falls below ~70 GW;
# values outside this range are SCADA dropouts, not real operating states.
DEMAND_MIN_MW = 70_000.0
DEMAND_MAX_MW = 400_000.0


def build_dataset(
    grid_hourly: pd.DataFrame | None = None,
    weather_hourly: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Inner-join grid and weather on the hourly IST index.

    An inner join is deliberate: an hour without both sides cannot support a
    weather-aware forecast, and silently carrying half-populated rows would
    distort every metric computed later.
    """
    if grid_hourly is None:
        grid_hourly = pd.read_parquet(config.GRID_HOURLY_PARQUET)
    if weather_hourly is None:
        weather_hourly = pd.read_parquet(config.WEATHER_HOURLY_PARQUET)

    for name, frame in (("grid", grid_hourly), ("weather", weather_hourly)):
        if frame.index.tz is None:
            raise ValueError(f"{name} index must be timezone-aware (expected IST)")
        if str(frame.index.tz) != config.IST_NAME:
            raise ValueError(f"{name} index tz is {frame.index.tz}, expected {config.IST_NAME}")

    joined = grid_hourly.join(weather_hourly, how="inner")
    joined.index.name = "timestamp_ist"
    joined = joined.sort_index()

    joined = flag_quality(joined)
    return joined


def flag_quality(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark implausible or thinly-sampled hours instead of dropping them silently."""
    out = frame.copy()

    demand = out[config.TARGET]
    out["demand_implausible"] = (
        demand.isna() | (demand < DEMAND_MIN_MW) | (demand > DEMAND_MAX_MW)
    )

    # An hour is thinly sampled if far fewer raw SCADA samples backed it than
    # the modal count for its sampling regime (5-minute -> 12, 10-second -> 360).
    samples = out["n_samples"]
    expected = samples.replace(0, np.nan).groupby(out.index.normalize()).transform("median")
    out["hour_undersampled"] = (samples < 0.5 * expected) | (samples == 0)

    # The single usable-row flag the rest of the pipeline filters on.
    out["is_valid"] = ~(out["demand_implausible"] | out["hour_undersampled"])
    return out


def add_hot_period_label(
    frame: pd.DataFrame,
    percentile: float = config.HOT_PERIOD_PERCENTILE,
    weighting: str = "pop",
    *,
    reference: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Label RESEARCH-DEFINED hot days. Not an official IMD heatwave.

    A day is hot when its population-weighted national daily maximum forecast
    temperature exceeds the given percentile of the daily maxima in the
    *reference* period. The reference defaults to the training period so the
    threshold is never fitted on validation or test data.

    This is explicitly not an IMD heatwave label: IMD declares heatwaves per
    station against that station's own normal, which cannot be reconstructed
    from a national weighted index.
    """
    out = frame.copy()
    column = f"temperature_2m_fc_{weighting}"
    daily_max = out[column].resample("1D").max()

    reference_daily = daily_max if reference is None else reference[column].resample("1D").max()
    threshold = float(reference_daily.quantile(percentile))

    hot_days = daily_max >= threshold
    out["is_hot_period"] = hot_days.reindex(out.index.normalize()).to_numpy()
    out.attrs["hot_period_threshold_c"] = threshold
    out.attrs["hot_period_percentile"] = percentile
    out.attrs["hot_period_definition"] = (
        "Research-defined: population-weighted national daily maximum day-ahead "
        f"forecast temperature >= {threshold:.2f} C (the p{percentile:.0%} of daily "
        "maxima over the training period). NOT an India Meteorological Department "
        "heatwave declaration."
    )
    return out


def add_evening_window(frame: pd.DataFrame) -> pd.DataFrame:
    """Flag the evening grid-stress window and compute residual-load ramps."""
    out = frame.copy()
    start, end = config.EVENING_WINDOW
    out["is_evening"] = (out.index.hour >= start) & (out.index.hour <= end)

    # Hour-on-hour change in research residual load. Positive means the
    # non-variable fleet has to pick up load within the hour.
    out["residual_ramp_mw_per_h"] = out["residual_load_mw"].diff()
    # Guard against the diff spanning a gap in the index.
    gap = out.index.to_series().diff() != pd.Timedelta("1h")
    out.loc[gap.to_numpy(), "residual_ramp_mw_per_h"] = np.nan
    return out


def finalise(frame: pd.DataFrame, train_end: str | None = None) -> pd.DataFrame:
    """Apply labels that depend on a reference period, then save-ready output."""
    train_end = train_end or config.TRAIN_END
    train_slice = frame.loc[frame.index < pd.Timestamp(train_end, tz=config.IST)]
    out = add_hot_period_label(frame, reference=train_slice)
    out = add_evening_window(out)
    return out


def save_dataset(frame: pd.DataFrame) -> Path:
    config.ensure_dirs()
    # Parquet drops `.attrs`; persist the hot-period definition alongside it.
    meta = {
        "rows": int(len(frame)),
        "start": str(frame.index.min()),
        "end": str(frame.index.max()),
        "timezone": config.IST_NAME,
        "target": config.TARGET,
        "target_unit": config.POWER_UNIT,
        **{k: v for k, v in frame.attrs.items()},
    }
    frame.to_parquet(config.DATASET_PARQUET)
    (config.PROCESSED_DIR / "dataset_meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )
    return config.DATASET_PARQUET


def load_dataset() -> pd.DataFrame:
    """Load the processed dataset and restore its metadata attributes."""
    frame = pd.read_parquet(config.DATASET_PARQUET)
    meta_path = config.PROCESSED_DIR / "dataset_meta.json"
    if meta_path.exists():
        frame.attrs.update(json.loads(meta_path.read_text(encoding="utf-8")))
    return frame


# --------------------------------------------------------------------------
# Quality report
# --------------------------------------------------------------------------


def quality_report(frame: pd.DataFrame, workbook_infos: list | None = None) -> dict:
    """Summarise coverage, gaps, outliers and sampling-regime changes."""
    index = frame.index
    expected = pd.date_range(index.min(), index.max(), freq="1h", tz=config.IST)
    missing = expected.difference(index)

    demand = frame[config.TARGET]
    report: dict = {
        "coverage": {
            "first_hour_ist": str(index.min()),
            "last_hour_ist": str(index.max()),
            "hours_present": int(len(index)),
            "hours_expected": int(len(expected)),
            "hours_missing": int(len(missing)),
            "pct_complete": round(100 * len(index) / len(expected), 3),
            "duplicate_timestamps": int(index.duplicated().sum()),
        },
        "target": {
            "name": config.TARGET,
            "unit": config.POWER_UNIT,
            "n_nan": int(demand.isna().sum()),
            "min": _safe(demand.min()),
            "p01": _safe(demand.quantile(0.01)),
            "median": _safe(demand.median()),
            "p99": _safe(demand.quantile(0.99)),
            "max": _safe(demand.max()),
            "n_below_plausible_min": int((demand < DEMAND_MIN_MW).sum()),
            "n_above_plausible_max": int((demand > DEMAND_MAX_MW).sum()),
        },
        "validity_flags": {
            "n_demand_implausible": int(frame.get("demand_implausible", pd.Series(dtype=bool)).sum()),
            "n_hour_undersampled": int(frame.get("hour_undersampled", pd.Series(dtype=bool)).sum()),
            "n_valid": int(frame.get("is_valid", pd.Series(dtype=bool)).sum()),
        },
        "missingness_by_column": {
            col: int(frame[col].isna().sum())
            for col in frame.columns
            if frame[col].dtype.kind in "fc"
        },
    }

    # Longest run of consecutive missing hours -- a few scattered gaps are
    # harmless, a multi-day hole is not.
    if len(missing):
        runs, current, previous = [], 1, missing[0]
        for timestamp in missing[1:]:
            if timestamp - previous == pd.Timedelta("1h"):
                current += 1
            else:
                runs.append(current)
                current = 1
            previous = timestamp
        runs.append(current)
        report["coverage"]["longest_missing_run_hours"] = int(max(runs))
        report["coverage"]["missing_runs"] = int(len(runs))
        report["coverage"]["first_missing_hours"] = [str(t) for t in missing[:20]]
    else:
        report["coverage"]["longest_missing_run_hours"] = 0
        report["coverage"]["missing_runs"] = 0

    # Monthly row counts expose partial months at the edges.
    monthly = frame.groupby([index.year, index.month]).size()
    report["rows_per_month"] = {f"{y}-{m:02d}": int(n) for (y, m), n in monthly.items()}

    if workbook_infos:
        periods = sorted({i.sample_period_seconds for i in workbook_infos})
        report["source_sampling"] = {
            "distinct_sample_periods_seconds": [
                None if p is None else float(p) for p in periods
            ],
            "per_workbook": [
                {
                    "file": i.filename,
                    "sample_period_seconds": i.sample_period_seconds,
                    "raw_rows": i.raw_rows,
                    "hours": i.hours,
                }
                for i in workbook_infos
            ],
            "note": (
                "The Mendeley dataset title claims a 1-hour interval. It is not "
                "hourly: the workbooks are 5-minute (r=300) for the earlier "
                "months and 10-second (r=10) for the later ones. This pipeline "
                "aggregates both regimes to true hourly means."
            ),
        }

    return report


def _safe(value) -> float | None:
    """Convert numpy scalars to JSON-safe floats, mapping NaN to None."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return float(value)


def save_quality_report(report: dict) -> Path:
    config.ensure_dirs()
    config.DATA_QUALITY_JSON.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return config.DATA_QUALITY_JSON
