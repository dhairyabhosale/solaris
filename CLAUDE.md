# Solaris Grid Intelligence — notes for coding agents

Day-ahead all-India electricity demand forecasting with archived weather
forecasts, plus evening grid-stress analysis. See `README.md` for the project
overview and `docs/METHODOLOGY.md` for the full method.

## Layout

- `src/solaris/` — the research pipeline. Importable, tested, no web imports.
- `app/` — FastAPI read-only presentation layer. It must never fit a model or
  compute a metric. It reads **only** the static JSON bundle in `app/data/`
  that `solaris.webexport` writes, using the standard library.
  **Do not import pandas, numpy or the modelling stack anywhere under `app/`** —
  the root `requirements.txt` is the deployment manifest and holds just four
  packages. A test parses the tree and fails if this is broken.
- `tests/` — must run offline, with no network, no downloads and no API keys.

## Standing rules

**No metric is ever hand-written.** `reports/evaluation.json` is the single
source of every number the project publishes. The README, the docs, the
figures and the dashboard all render from it. If you need to change a reported
number, change the code that computes it and re-run
`python -m solaris.cli train && python -m solaris.cli report`.

**Leakage safeguards are load-bearing — do not weaken them.**
`solaris.features.assert_no_leakage` rejects observed weather (`*_obs`),
contemporaneous grid measurements, and any demand lag under 24 hours. The
forecast-issue convention is 09:00 IST on day D−1 for every hour of day D. If
you add a feature, it must be derivable from information available at that
moment, and it must pass the guard. Splits are chronological; never introduce
a random split.

**Negative results stay in.** The `_level` model variants lose to the naive
baseline because gradient-boosted trees cannot extrapolate past their training
maximum. That is reported deliberately, on the dashboard and in the docs. The
quantile band is poorly calibrated and is labelled as such. Do not quietly
drop either.

**No LLM dependency.** The analysis, the model and the dashboard must work
with no third-party model provider and no API key. There are currently no
required environment variables.

## Commands

```bash
python -m solaris.cli fetch-grid      # ~713 MB from Mendeley
python -m solaris.cli fetch-weather   # archived forecasts, 18 cities
python -m solaris.cli prepare         # parse, join, quality-check
python -m solaris.cli train --rolling # fit and evaluate every model
python -m solaris.cli report          # regenerate figures and tables

pytest
uvicorn app.main:app --reload
vercel --prod                         # deploy (needs `vercel login` first)
```

Dependencies are split: `requirements.txt` is the web app only (what Vercel
installs); `requirements-research.txt` is the full pipeline.

## Things that will trip you up

- The Mendeley workbooks are **not hourly** despite the dataset title: 5-minute
  before October 2022, 10-second after. The sample period is read from each
  file's hidden `_osi_config` sheet.
- Columns H/I/J of those workbooks are **Excel formulas**, not values. They are
  recomputed from the raw SCADA points; do not read them.
- Raw solar goes **negative at night** (SCADA offset). Always use `solar_mw`,
  which is clipped at zero, not `solar_raw_mw`.
- Mendeley's edge returns **403 to `requests`** regardless of User-Agent — it
  appears to fingerprint the TLS handshake. Use `httpx`.
- Only `temperature_2m` is archived at a day-ahead lead by Open-Meteo for
  2021–2023. Other variables return HTTP 200 with an all-null series. Do not
  "fix" this by substituting observed values.
- The test period is placed to contain a hot season on purpose. An earlier
  split had zero hot test hours, which made the central analysis impossible.
