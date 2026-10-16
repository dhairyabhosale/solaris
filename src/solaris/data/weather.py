"""Retrieve archived weather *forecasts* and build a national weather index.

Leakage policy
--------------
The forecasting task is day-ahead: at 09:00 IST on day D-1 we predict every
hour of day D. Observed weather for day D does not exist at that moment, so it
must never enter the model. This module therefore fetches Open-Meteo's
**Previous Runs API**, whose ``<variable>_previous_day1`` fields give the value
a timestamp was assigned by the model run issued one day earlier -- that is, a
genuine archived day-ahead forecast.

Observed weather is fetched too, from the Historical Weather (archive) API, but
only so that forecast error can be characterised and so that explicitly
retrospective, explanatory analyses are possible. It is tagged with an
``obs_`` prefix and is excluded from every operational feature set by
``solaris.features``.

Spatial aggregation
-------------------
All-India demand is a single national series; weather is local. Using one
city's temperature as a national driver would be wrong, so this module builds a
weighted national index over 18 load-centre cities across all five regional
grids. Three weightings are produced so the choice can be audited:

* ``pop``    -- Census of India 2011 urban agglomeration population (default)
* ``equal``  -- every city counts the same
* ``region`` -- each of the five regional grids counts equally, cities within a
                region weighted by population

None of these is a measured demand weight; see the caveat in ``solaris.config``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from solaris import config

logger = logging.getLogger(__name__)

WEIGHTINGS = ("pop", "equal", "region")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def _request(url: str, params: dict, *, retries: int = 4) -> dict:
    """GET with linear backoff; Open-Meteo rate-limits free use per minute."""
    import requests

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=180)
            if response.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning("rate limited, sleeping %ss", wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_error = exc
            logger.warning("request failed (attempt %d/%d): %s", attempt + 1, retries, exc)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Open-Meteo request failed after {retries} attempts") from last_error


def fetch_city_forecast(city: config.City, start: str, end: str) -> pd.DataFrame:
    """Archived day-ahead forecasts for one city, hourly, IST."""
    variables = [v + config.FORECAST_LEAD_SUFFIX for v in config.FORECAST_VARIABLES]
    payload = _request(
        config.OPEN_METEO_PREVIOUS_RUNS_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "start_date": start,
            "end_date": end,
            "hourly": ",".join(variables),
            "timezone": config.IST_NAME,
        },
    )
    hourly = payload["hourly"]
    frame = pd.DataFrame(hourly)
    frame["time"] = pd.to_datetime(frame["time"]).dt.tz_localize(config.IST)
    frame = frame.set_index("time")
    # Drop the `_previous_day1` suffix; these are the forecast features.
    frame.columns = [c.replace(config.FORECAST_LEAD_SUFFIX, "") for c in frame.columns]
    return frame


def fetch_city_observed(city: config.City, start: str, end: str) -> pd.DataFrame:
    """Observed/analysed weather for one city. Retrospective analysis only."""
    payload = _request(
        config.OPEN_METEO_ARCHIVE_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "start_date": start,
            "end_date": end,
            "hourly": ",".join(config.OBSERVED_VARIABLES),
            "timezone": config.IST_NAME,
        },
    )
    hourly = payload["hourly"]
    frame = pd.DataFrame(hourly)
    frame["time"] = pd.to_datetime(frame["time"]).dt.tz_localize(config.IST)
    frame = frame.set_index("time")
    frame.columns = ["obs_" + c for c in frame.columns]
    return frame


def download_all(
    start: str | None = None,
    end: str | None = None,
    *,
    force: bool = False,
) -> Path:
    """Fetch forecast and observed weather for every configured city.

    Each city is cached as its own parquet under ``data/raw/open_meteo`` so an
    interrupted run resumes instead of re-fetching.
    """
    start = start or config.WEATHER_START_DATE
    end = end or config.WEATHER_END_DATE
    config.ensure_dirs()

    for city in config.CITIES:
        target = config.WEATHER_RAW_DIR / f"{city.name.lower()}.parquet"
        if target.exists() and not force:
            logger.info("skip %s (cached)", city.name)
            continue
        logger.info("fetching %s", city.name)
        forecast = fetch_city_forecast(city, start, end)
        observed = fetch_city_observed(city, start, end)
        merged = forecast.join(observed, how="outer")
        merged.index.name = "timestamp_ist"
        merged.to_parquet(target)
        time.sleep(2)  # be polite to a free API

    meta = {
        "start": start,
        "end": end,
        "cities": [c.name for c in config.CITIES],
        "forecast_variables": list(config.FORECAST_VARIABLES),
        "observed_variables": list(config.OBSERVED_VARIABLES),
        "forecast_source": config.OPEN_METEO_PREVIOUS_RUNS_URL,
        "forecast_lead": "day-ahead (previous_day1 run)",
        "observed_source": config.OPEN_METEO_ARCHIVE_URL,
        "attribution": config.OPEN_METEO_ATTRIBUTION,
    }
    meta_path = config.WEATHER_RAW_DIR / "_manifest.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return config.WEATHER_RAW_DIR


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------


def city_weights(scheme: str = "pop") -> dict[str, float]:
    """Return normalised per-city weights for one of the three schemes."""
    if scheme == "equal":
        raw = {c.name: 1.0 for c in config.CITIES}
    elif scheme == "pop":
        raw = {c.name: float(c.population) for c in config.CITIES}
    elif scheme == "region":
        # Each regional grid gets 1/5 of the total, split within the region by
        # population. This stops the four mega-cities of NR/WR dominating.
        region_totals = {
            r: sum(c.population for c in config.CITIES if c.region == r)
            for r in config.REGIONS
        }
        raw = {
            c.name: (1.0 / len(config.REGIONS)) * c.population / region_totals[c.region]
            for c in config.CITIES
        }
    else:
        raise ValueError(f"Unknown weighting scheme: {scheme!r}")

    total = sum(raw.values())
    return {name: value / total for name, value in raw.items()}


def load_city_frames(raw_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load every cached per-city weather parquet."""
    raw_dir = raw_dir or config.WEATHER_RAW_DIR
    frames: dict[str, pd.DataFrame] = {}
    for city in config.CITIES:
        path = raw_dir / f"{city.name.lower()}.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing weather cache for {city.name}. "
                "Run `python -m solaris.cli fetch-weather` first."
            )
        frames[city.name] = pd.read_parquet(path)
    return frames


def build_national_index(raw_dir: Path | None = None) -> pd.DataFrame:
    """Combine per-city weather into weighted national hourly indices.

    Produces, for every forecast variable, one column per weighting scheme
    (``temp_fc_pop``, ``temp_fc_equal``, ...), plus the national maximum and the
    population-weighted observed counterparts used for retrospective work.
    """
    frames = load_city_frames(raw_dir)
    index = None
    for frame in frames.values():
        index = frame.index if index is None else index.union(frame.index)

    out = pd.DataFrame(index=index)
    out.index.name = "timestamp_ist"

    weights = {scheme: city_weights(scheme) for scheme in WEIGHTINGS}

    for variable in config.FORECAST_VARIABLES:
        # Stack this variable across cities into one aligned matrix.
        forecast_matrix = pd.DataFrame(
            {name: frame[variable].reindex(index) for name, frame in frames.items()}
        )
        for scheme, weight_map in weights.items():
            weight_vector = pd.Series(weight_map).reindex(forecast_matrix.columns)
            out[f"{variable}_fc_{scheme}"] = _weighted_mean(forecast_matrix, weight_vector)

    # Observed weather, population-weighted. Retrospective analysis only -- the
    # `_obs` suffix is what `solaris.features.assert_no_leakage` screens on.
    default_weights = pd.Series(weights["pop"])
    for variable in config.OBSERVED_VARIABLES:
        observed_matrix = pd.DataFrame(
            {
                name: frame["obs_" + variable].reindex(index)
                for name, frame in frames.items()
            }
        )
        out[f"{variable}_obs"] = _weighted_mean(
            observed_matrix, default_weights.reindex(observed_matrix.columns)
        )

    # Hottest load centre in the country at each hour: a national index smooths
    # away regional extremes, and peak stress is often driven by one region.
    temp_matrix = pd.DataFrame(
        {name: frame["temperature_2m"].reindex(index) for name, frame in frames.items()}
    )
    out["temperature_2m_fc_max"] = temp_matrix.max(axis=1)
    out["temperature_2m_fc_spread"] = temp_matrix.max(axis=1) - temp_matrix.min(axis=1)

    # Population-weighted forecast temperature per regional grid, so regional
    # heat can be separated from the national average.
    for region in config.REGIONS:
        members = [c.name for c in config.CITIES if c.region == region]
        region_matrix = temp_matrix[members]
        region_weights = pd.Series(
            {c.name: float(c.population) for c in config.CITIES if c.region == region}
        )
        region_weights = region_weights / region_weights.sum()
        out[f"temperature_2m_fc_region_{region}"] = _weighted_mean(
            region_matrix, region_weights.reindex(region_matrix.columns)
        )

    # Per-city forecast temperature is kept so the frontend can offer a city view
    # and so regional analyses are possible without re-reading the raw cache.
    for name, frame in frames.items():
        out[f"city_temp_fc__{name}"] = frame["temperature_2m"].reindex(index)

    out = _fill_short_forecast_gaps(out)
    return out


# The archived forecast series has a small number of isolated missing hours
# where a model run was not retained. Gaps up to this many hours are filled by
# time interpolation; anything longer is left as NaN and excluded downstream,
# because inventing half a day of weather would be fabrication.
MAX_INTERPOLATED_GAP_HOURS = 3


def _fill_short_forecast_gaps(frame: pd.DataFrame) -> pd.DataFrame:
    """Interpolate isolated missing forecast hours; leave long gaps as NaN."""
    out = frame.copy()
    targets = [c for c in out.columns if "_fc" in c or c.startswith("city_temp_fc")]
    for column in targets:
        series = out[column]
        if not series.isna().any():
            continue
        out[column] = series.interpolate(
            method="time", limit=MAX_INTERPOLATED_GAP_HOURS, limit_area="inside"
        )
    return out


def _weighted_mean(matrix: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Weighted row-mean that renormalises over whichever cities are present.

    A city missing an hour must not drag the national index toward zero, so the
    weights are renormalised across the non-null cities for that hour.
    """
    values = matrix.to_numpy(dtype=float)
    weight_row = weights.to_numpy(dtype=float)[None, :]
    mask = ~np.isnan(values)
    weighted = np.nansum(np.where(mask, values, 0.0) * weight_row, axis=1)
    norm = (mask * weight_row).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(norm > 0, weighted / norm, np.nan)
    return pd.Series(result, index=matrix.index)


def save_national_index(frame: pd.DataFrame) -> Path:
    config.ensure_dirs()
    frame.to_parquet(config.WEATHER_HOURLY_PARQUET)
    return config.WEATHER_HOURLY_PARQUET
