"""Central configuration: paths, constants, source URLs, weather locations.

Every tunable the pipeline depends on lives here so that scripts, notebooks and
the web app all agree on one definition. Nothing in this module performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"

MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
TABLES_DIR = REPORTS_DIR / "tables"

GRID_RAW_DIR = RAW_DIR / "grid_india_mendeley"
WEATHER_RAW_DIR = RAW_DIR / "open_meteo"

# Files the pipeline produces.
GRID_HOURLY_PARQUET = INTERIM_DIR / "grid_hourly.parquet"
WEATHER_HOURLY_PARQUET = INTERIM_DIR / "weather_hourly.parquet"
DATASET_PARQUET = PROCESSED_DIR / "solaris_hourly.parquet"
DATA_QUALITY_JSON = REPORTS_DIR / "data_quality.json"


def ensure_dirs() -> None:
    """Create every directory the pipeline writes into."""
    for d in (
        RAW_DIR, INTERIM_DIR, PROCESSED_DIR, GRID_RAW_DIR, WEATHER_RAW_DIR,
        MODELS_DIR, REPORTS_DIR, FIGURES_DIR, TABLES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

# Grid-India despatch centres operate on Indian Standard Time. India observes no
# daylight saving, so IST is a fixed UTC+05:30 and local timestamps are never
# ambiguous or non-existent -- which is why the raw naive timestamps can be
# localised directly without a DST resolution policy.
IST = ZoneInfo("Asia/Kolkata")
IST_NAME = "Asia/Kolkata"


# --------------------------------------------------------------------------
# Source: Mendeley mirror of Grid-India / NERLDC SCADA data
# --------------------------------------------------------------------------

MENDELEY_DOI = "10.17632/y58jknpgs8.1"
MENDELEY_DATASET_ID = "y58jknpgs8"
MENDELEY_VERSION = 1
MENDELEY_LANDING_URL = "https://data.mendeley.com/datasets/y58jknpgs8"
MENDELEY_FILES_API = (
    "https://data.mendeley.com/public-api/datasets/"
    f"{MENDELEY_DATASET_ID}/files?folder_id=root&version={MENDELEY_VERSION}"
)
MENDELEY_LICENCE = "CC BY 4.0"

# Column letters in Sheet1 of every monthly workbook, keyed by the SCADA point
# name that the workbook's second header row carries. Columns H, I and J of the
# source workbooks hold Excel *formulas* (clipped solar, wind+solar, total
# generation); the pipeline recomputes those from the raw columns instead of
# relying on cached formula results, so the derivation is explicit and testable.
SCADA_COLUMNS: dict[str, str] = {
    "TOTAL_THM_ONLY|P": "thermal_mw",
    "TOTAL_NLDC_HYD|P": "hydro_mw",
    "TOTAL_NLDC_GAS|P": "gas_mw",
    "TOTAL_NLDC_NPC|P": "nuclear_mw",
    "ALL_INDIA_WIND|P": "wind_mw",
    "ALL_IND_SOLAR|P": "solar_raw_mw",
    "NLDC_DEMAND|P": "demand_mw",
}

# Zero-based positions of those points in Sheet1 (column A is the timestamp).
SCADA_COLUMN_INDEX: dict[str, int] = {
    "thermal_mw": 1,
    "hydro_mw": 2,
    "gas_mw": 3,
    "nuclear_mw": 4,
    "wind_mw": 5,
    "solar_raw_mw": 6,
    "demand_mw": 10,
}

# All SCADA power points above are megawatts.
POWER_UNIT = "MW"


# --------------------------------------------------------------------------
# Source: Open-Meteo
# --------------------------------------------------------------------------

# Archived *forecasts* as they were issued, used for the day-ahead model inputs.
OPEN_METEO_PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
# Observed/analysed weather, used only for retrospective, explanatory work.
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Variables taken at a day-ahead lead time. Open-Meteo exposes these as
# `<variable>_previous_day1`: the value this timestamp was given by the model
# run issued one day earlier, which is what a day-ahead forecaster would have
# had.
#
# VERIFIED SOURCE LIMITATION. Only `temperature_2m` is actually archived with
# `_previous_day1` coverage over 2021-2023. Every other variable that was
# probed (relative_humidity_2m, dew_point_2m, apparent_temperature,
# shortwave_radiation, cloud_cover, wind_speed_10m, precipitation,
# surface_pressure, direct_radiation, weather_code) returns HTTP 200 with an
# all-null series for this period. They are therefore NOT available at a
# genuine day-ahead lead and are excluded from every operational feature set.
# Substituting observed values for them would be exactly the leakage this
# project is built to avoid. See docs/DATA_SOURCES.md.
FORECAST_VARIABLES: tuple[str, ...] = ("temperature_2m",)
FORECAST_LEAD_SUFFIX = "_previous_day1"

# Observed / analysed weather, retrieved for retrospective and explanatory
# analysis and for characterising forecast error. NEVER used as a model input.
OBSERVED_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "shortwave_radiation",
    "cloud_cover",
    "wind_speed_10m",
)

OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"


@dataclass(frozen=True)
class City:
    """A weather sampling point standing in for a regional load centre."""

    name: str
    region: str  # Grid-India regional grid: NR / WR / SR / ER / NER
    latitude: float
    longitude: float
    population: int  # Census of India 2011 urban agglomeration population


# Eighteen major load centres spanning all five Grid-India regional grids.
#
# WEIGHTING CAVEAT: `population` is the Census of India 2011 urban agglomeration
# count. It is used as a *transparent proxy* for each city's share of national
# electricity demand because a reproducible, machine-readable series of actual
# city-level demand weights could not be obtained from Grid-India or CEA. It is
# a proxy, not a measured demand weight: it understates industrial and
# agricultural load, which is not proportional to urban population. The
# sensitivity analysis recomputes the national index under equal city weights
# and under region-balanced weights so the effect of this choice is visible.
CITIES: tuple[City, ...] = (
    # Northern Region
    City("Delhi", "NR", 28.6139, 77.2090, 16_349_831),
    City("Jaipur", "NR", 26.9124, 75.7873, 3_046_163),
    City("Lucknow", "NR", 26.8467, 80.9462, 2_902_920),
    City("Ludhiana", "NR", 30.9010, 75.8573, 1_613_878),
    # Western Region
    City("Mumbai", "WR", 19.0760, 72.8777, 18_414_288),
    City("Ahmedabad", "WR", 23.0225, 72.5714, 6_357_693),
    City("Pune", "WR", 18.5204, 73.8567, 5_049_968),
    City("Surat", "WR", 21.1702, 72.8311, 4_585_367),
    City("Nagpur", "WR", 21.1458, 79.0882, 2_497_777),
    City("Indore", "WR", 22.7196, 75.8577, 2_167_447),
    # Southern Region
    City("Bengaluru", "SR", 12.9716, 77.5946, 8_499_399),
    City("Chennai", "SR", 13.0827, 80.2707, 8_653_521),
    City("Hyderabad", "SR", 17.3850, 78.4867, 7_749_334),
    City("Coimbatore", "SR", 11.0168, 76.9558, 2_151_466),
    # Eastern Region
    City("Kolkata", "ER", 22.5726, 88.3639, 14_057_991),
    City("Patna", "ER", 25.5941, 85.1376, 2_046_652),
    City("Bhubaneswar", "ER", 20.2961, 85.8245, 1_110_875),
    # North-Eastern Region
    City("Guwahati", "NER", 26.1445, 91.7362, 968_549),
)

CITY_BY_NAME: dict[str, City] = {c.name: c for c in CITIES}
REGIONS: tuple[str, ...] = ("NR", "WR", "SR", "ER", "NER")

# Date range to retrieve weather for: the span the grid data actually covers,
# padded one day either side so that lag features at the boundaries resolve.
WEATHER_START_DATE = "2021-08-31"
WEATHER_END_DATE = "2024-01-01"


# --------------------------------------------------------------------------
# Modelling
# --------------------------------------------------------------------------

TARGET = "demand_mw"
RANDOM_SEED = 42

# Chronological split boundaries (IST dates, half-open on the right).
#
# Train: 2021-09-01 -> 2022-12-31 (16 months, a complete annual cycle plus four
#        months, so every season is represented in fitting)
# Valid: 2023-01-01 -> 2023-02-28 (early stopping and model selection only)
# Test : 2023-03-01 -> 2023-12-31 (10 months, never touched until final scoring)
#
# The test block is deliberately placed to contain India's March-June
# pre-monsoon hot season, the monsoon, the all-time peak demand hour of
# 1 September 2023, and the winter trough. An earlier split that ended training
# in April 2023 was rejected because its test block (July-December) contained
# *zero* hours above the hot-period threshold, which would have made the
# hot-period analysis the brief requires impossible to carry out honestly.
#
# The trade-off is a shorter training block than the test block. That is
# accepted deliberately: a test period without a hot season cannot answer the
# research question. The rolling-origin backtest refits monthly over an
# expanding window and is the check that this single split is not a fluke.
TRAIN_END = "2023-01-01"
VALID_END = "2023-03-01"
TEST_END = "2024-01-01"

# Evening grid-stress window (IST hours, inclusive) -- the solar-to-evening-peak
# transition during which residual load ramps hardest.
EVENING_WINDOW = (17, 22)

# A research-defined hot period, NOT an official IMD heatwave declaration.
# IMD's criteria are station-based departures from a station normal; they cannot
# be reconstructed faithfully from a population-weighted national index, so this
# project uses an explicitly separate, percentile-based label and says so
# everywhere it is reported. The sensitivity analysis sweeps the percentile.
HOT_PERIOD_PERCENTILE = 0.90
HOT_PERIOD_SENSITIVITY = (0.80, 0.85, 0.90, 0.95)

# Day-ahead forecast issue convention: forecasts for every hour of day D are
# issued at 09:00 IST on day D-1. Every feature must therefore be derivable from
# information timestamped at or before that moment.
FORECAST_ISSUE_HOUR_IST = 9
