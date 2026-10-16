# Solaris Grid Intelligence

Heat-aware electricity demand forecasting and evening grid stress analysis for India

> Can weather-aware machine learning improve day-ahead electricity-demand
> forecasts in India, particularly during hot periods and the evening
> transition when solar generation declines?

Short answer, measured on ten months of held-out data: yes, by 11.1% in
mean absolute error, and by 21.5% during hot periods.

---

## What this is

A reproducible data-science project that forecasts all-India hourly
electricity demand one day ahead, using archived weather forecasts rather
than observed weather, and evaluates the result honestly against baselines
that are hard to beat.

Everything it reports comes from `reports/evaluation.json`, which is written
by the evaluation code. No metric anywhere in this repository, README,
dashboard, docs, is hand-entered.

| | |
| --- | --- |
| Target | All-India metered demand (`NLDC_DEMAND\|P`), hourly mean, MW |
| Data | 2021-09-01 to 2023-12-31 IST, 20,448 hours, 100% complete |
| Test period | 2023-03-01 to 2023-12-31 (7,344 hours), never used for fitting |
| Headline model | LightGBM on calendar + demand history + day-ahead forecast temperature |
| Headline MAE | 3,398 MW (1.87% MAPE) |

---

## Results

Held-out test period, 2023-03-01 to 2023-12-31. Lower MAE is better.

| Model | MAE (MW) | RMSE (MW) | MAPE (%) | vs naive-24h |
| --- | ---: | ---: | ---: | ---: |
| `lgbm_weather` | 3,398 | 4,565 | 1.87 | +31.2% |
| `lgbm_calendar_history` | 3,822 | 5,067 | 2.09 | +22.6% |
| `ridge_calendar_history` | 4,662 | 6,114 | 2.58 | +5.6% |
| `naive_lag_24h` | 4,938 | 6,691 | 2.72 | n/a |
| `lgbm_calendar_history_level` | 5,443 | 7,524 | 2.89 | -10.2% |
| `lgbm_weather_level` | 5,528 | 7,799 | 2.90 | -11.9% |
| `naive_lag_168h` | 9,807 | 12,346 | 5.43 | -98.6% |
| `climatology_hour_weekday` | 22,386 | 26,167 | 11.71 | -353.3% |

### Does weather actually add information?

`lgbm_weather` and `lgbm_calendar_history` are the same algorithm, the same
hyperparameters and the same target. Only the feature set differs.

- MAE 3,822 to 3,398 MW, an 11.07% reduction
- Paired t-test on daily error: p = 3.2e-10 over 305 days; Wilcoxon
  p = 1.5e-10; 65.6% of days improved
- Confirmed by a 12-fold rolling-origin backtest (monthly refit, expanding
  window): mean fold MAE 3,227 MW with weather against 3,731 MW without,
  improving in 9 of 12 folds

### It helps most when it is hot

| Slice | MAE reduction from weather |
| --- | ---: |
| Research-defined hot hours | 21.45% |
| All other hours | 9.32% |
| Evening window (17:00-22:00 IST) | 12.43% |
| Top decile of demand hours | 16.80% |

Robust to where the hot threshold is drawn: 15.7% / 18.8% / 21.5% / 22.6% at
the p80 / p85 / p90 / p95 thresholds.

### Daily peak

3,281 MW peak MAE (1.65% of peak). The peak hour is called exactly right on
50.7% of days and within +/-1 hour on 77.0%.

### Evening grid stress

Measured over 845 full days, on research residual load (demand minus SCADA
solar and wind):

- Mean steepest hourly ramp 12.4 GW/h; 95th percentile 17.8 GW/h
- Mean steepest 3-hour ramp 26.2 GW
- The steepest ramp most often falls at 19:00 IST; the evening residual
  peak most often at 18:00 IST

### A negative result that is kept, not hidden

The two `_level` models are worse than reusing yesterday's demand. A
gradient-boosted tree cannot predict above its training maximum. Training tops
out at 209.8 GW; the test block reaches 239.4 GW, with 526 test hours above
anything a level model ever saw, so it saturates at 204.7 GW.

The headline models predict the change from the same hour yesterday and add
that anchor back, lifting the ceiling to 237.2 GW. Both variants are reported,
because the failure explains why the fix is needed.

---

## How leakage is prevented

A day-ahead forecast is only meaningful if it uses only what was knowable when
it was issued. The convention here: every hour of day D is predicted from
information available at 09:00 IST on day D-1.

- Weather comes from archived forecasts, never observations. Open-Meteo's
  Previous Runs API returns the value a timestamp was given by the model run
  issued a day earlier. Observed weather is downloaded too, but every such
  column carries an `_obs` suffix and `assert_no_leakage` raises on it.
- No demand lag shorter than 24 hours. At 09:00 on D-1 you do not know
  demand at hour t-1 of day D. Rolling statistics are shifted before
  windowing; daily aggregates are shifted in whole days.
- No contemporaneous grid measurements. Solar, wind, generation and
  residual load at the target hour are rejected by name.
- Preprocessing fitted on training rows only, scalers, and the
  hot-period temperature threshold.
- Chronological splits only. No random splitting anywhere.

`solaris.features.assert_no_leakage` encodes these rules and the test suite
exercises each, including a test that recomputes every history feature on
truncated data and asserts nothing changed.

---

## Data sources

| Source | Use | Licence |
| --- | --- | --- |
| [Grid-India / NERLDC SCADA via Mendeley Data](https://data.mendeley.com/datasets/y58jknpgs8) (doi:[10.17632/y58jknpgs8.1](https://doi.org/10.17632/y58jknpgs8.1)) | Demand, solar, wind, thermal, hydro, gas, nuclear | CC BY 4.0 |
| [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) | Archived day-ahead forecast temperature, the model's only weather input | CC BY 4.0 |
| [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) | Observed weather, retrospective analysis only | CC BY 4.0 |

### Two things worth knowing about the data

The Mendeley dataset is not hourly, despite its title. It is published as
"at 1-hour interval". The files are 5-minute exports up to September 2022 and
10-second exports from October 2022, which the hidden `_osi_config` sheet
records as `"r":300.0` and `"r":10.0`. This project reads that field and
aggregates both regimes to true hourly means. Three of the workbook columns
are Excel formulas rather than data and are recomputed from the raw SCADA
points instead of trusted.

Only temperature is archived at a day-ahead lead. Humidity, dew point,
radiation, cloud cover and wind all return empty series from the Previous Runs
API for 2021-2023. They are therefore excluded from the model rather than
replaced with observed values. The measured 11.07% gain is likely a lower
bound on what weather information could contribute.

Full provenance, licences, verified discrepancies and the peak cross-check
against official reporting: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

---

## Architecture

```
solaris/
├── src/solaris/              research pipeline (importable, tested)
│   ├── config.py               paths, split boundaries, cities, constants
│   ├── features.py             feature engineering + the leakage guard
│   ├── evaluate.py             metrics, slices, peak and ramp statistics
│   ├── experiment.py           the full experiment; writes evaluation.json
│   ├── report.py               figures and tables, from saved results
│   ├── webexport.py            static JSON bundle for the web app
│   ├── cli.py                  every reproducible command
│   ├── data/
│   │   ├── grid.py               download + parse Grid-India SCADA workbooks
│   │   ├── weather.py            archived forecasts, national weather index
│   │   └── build.py              join, quality-flag, label
│   └── models/
│       ├── splits.py             chronological + rolling-origin splits
│       ├── baselines.py          naive, climatology, ridge
│       └── gbm.py                LightGBM point and quantile models
│
├── app/                      FastAPI read-only presentation layer
│   ├── main.py                 app, health check, static mount
│   ├── routes/api.py           JSON API over the exported bundle
│   ├── services/results.py     stdlib-only reader (no pandas/numpy)
│   ├── data/                   exported JSON bundle, written by the pipeline
│   └── static/                 the dashboard (HTML/CSS/vanilla JS + Plotly)
│
├── data/   raw to interim to processed   (raw and interim gitignored)
├── models/ trained boosters + metadata sidecars
├── reports/ evaluation.json, EVALUATION.md, figures/, tables/
├── docs/   DATA_SOURCES, METHODOLOGY, MODEL_CARD, PROJECT_EXPLAINED
└── tests/  104 tests, no network or API keys required
```

Data flow:

```
Mendeley .xlsx (713 MB, 5-min / 10-sec SCADA)
        │  grid.py, parse, localise to IST, aggregate to hourly
        v
Open-Meteo previous-runs (18 cities, day-ahead forecast temperature)
        │  weather.py, population-weighted national index
        v
build.py, inner join, quality flags, hot-period + evening labels
        v
features.py, calendar / history / weather, leakage-guarded
        v
experiment.py, fit 9 models, score on the chronological holdout
        v
reports/evaluation.json ──> report.py ──> figures + tables
                                     └──> webexport.py ──> app/data/*.json
                                                            └──> dashboard
```

---

## Setup

Requires Python 3.11 or later.

```bash
git clone https://github.com/dhairyabhosale/solaris
cd solaris

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Full research pipeline (data, modelling, evaluation, reports):
pip install -r requirements-research.txt

# Or, to run only the dashboard over the committed results:
pip install -r requirements.txt
```

The split is deliberate. `requirements.txt` holds just the four packages the
web app needs, because the dashboard reads a pre-exported JSON bundle with the
standard library and serverless platforms install from the root file. The
modelling stack lives in `requirements-research.txt`.

No API keys are needed. Both data sources are open and unauthenticated,
and nothing in this project calls a third-party LLM.

### Run the dashboard immediately

The evaluation outputs and test-period predictions are committed, so the app
works straight after a clone:

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. `GET /health` reports which artefacts are present.

### Deploying

The repository is configured for Vercel. `pyproject.toml` declares the ASGI
entrypoint (`app.main:app`) and the four runtime dependencies; `vercel.json`
sets `excludeFiles` so the research directories stay out of the function
bundle. The deployed function is about 1.5 MB of app code and exported JSON
plus ~35 MB of dependencies, well inside the 500 MB limit for Python
functions.

Git-based deploy (recommended). Import the repository in the Vercel
dashboard. Because `data/raw/`, `data/interim/` and the trained boosters are
gitignored, the checkout Vercel builds from is only ~2.6 MB and nothing needs
excluding.

CLI deploy.

```bash
vercel login
vercel --prod
```

One caveat if you deploy with the CLI from this working directory: the CLI
builds Python locally and, in the version tested (59.9.1), bundled the whole
project tree including the 719 MB `data/raw/` download, ignoring both
`excludeFiles` and `.vercelignore`. If you hit
`Total bundle size exceeds the maximum function size`, either deploy from Git
instead, or deploy a directory containing only `app/`, `pyproject.toml` and
`vercel.json`, the app needs nothing else at runtime.

### Reproduce the whole thing from source

```bash
python -m solaris.cli fetch-grid      # ~713 MB from Mendeley (slow)
python -m solaris.cli fetch-weather   # archived forecasts for 18 cities
python -m solaris.cli prepare         # parse, join, quality-check
python -m solaris.cli train --rolling # fit and evaluate every model
python -m solaris.cli report          # regenerate figures and tables
```

Or `python -m solaris.cli all` for prepare + train + report once the raw data
is fetched. Parsing the workbooks takes several minutes; the hourly result is
cached in `data/interim/` and reused unless `--reparse-grid` is passed.

### Tests

```bash
pytest
```

108 tests, all offline, they use small synthetic fixtures and stub the
artefact loaders, so no download, network access or API key is required. One
test parses the `app/` source tree and fails if the web layer ever imports
pandas, numpy or the modelling stack, which is what keeps the deployment small.

---

## The dashboard

Seven views, all driven by the API:

1. Overview, purpose, data coverage, model version, four real KPIs
2. Forecast, hourly actual vs predicted with a selectable date window,
   comparison model and uncertainty band; jump to the peak-demand day
3. Heat and demand, temperature against demand, hot vs non-hot error,
   threshold sensitivity
4. Evening grid stress, the average day, ramp statistics, when the
   steepest ramp occurs
5. Model performance, every model scored, error by hour, residuals, the
   extrapolation failure, uncertainty calibration
6. Explainability, feature importance with its caveats
7. Data and methodology, sources, splits, sensitivity checks, limitations

The dashboard states on every page that the data is historical and does not
update. It shows no invented metrics, no fake live indicators, and surfaces
the calibration failure rather than hiding it.

---

## Limitations

- Only forecast temperature is available at a genuine day-ahead lead;
  humidity and radiation forecasts are not, and are excluded rather than
  substituted with observations.
- Population-weighted weather is a proxy for demand-weighted weather. The
  conclusion survives all three weighting schemes tested; the magnitude varies
  (11.07% / 10.52% / 7.29%).
- The uncertainty band is not calibrated. P10-P90 covers 57.5% of actuals
  against a nominal 80% on the test period (72.0% on validation). It is
  reported as measured and should not be used as a risk band.
- Hot periods are research-defined by a temperature percentile, not IMD
  heatwave declarations.
- Residual load is an indicator, not an operator net load: it excludes
  behind-the-meter solar, interchange and storage.
- Associations, not causation. Nothing here identifies a causal effect of
  temperature on demand.
- The data ends 31 December 2023. No 2024-2026 demand record has been
  evaluated and no claim is made about one.
- Not for operational use. This is a research artefact.

---

## Documentation

| Document | What it covers |
| --- | --- |
| [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) | Provenance, licences, coverage, verified discrepancies, peak cross-check |
| [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) | Task definition, leakage safeguards, splits, features, models, evaluation |
| [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) | Intended and non-intended use, metrics, failure modes, limitations |
| [`docs/PROJECT_EXPLAINED.txt`](docs/PROJECT_EXPLAINED.txt) | Plain-language guide for presenting and defending the project |
| [`reports/EVALUATION.md`](reports/EVALUATION.md) | Generated evaluation report, rebuilt by `solaris.cli report` |

---

## Attribution

- Electricity data: Mukherjee, D.; Kalita, K.; Kumar, S. (2024), Electricity
  Demand, Solar and Wind Generation Data (2021-2023) of India at 1-hour
  interval, Mendeley Data V1, doi:10.17632/y58jknpgs8.1, CC BY 4.0.
  Originating source: NERLDC, Grid-India, Ministry of Power, Government of
  India.
- Weather data by [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0.

This project is independent research and is not affiliated with or endorsed by
Grid-India, the Central Electricity Authority or the Ministry of Power.
