# Methodology

How the forecast is defined, what prevents leakage, how the data is split, what
is modelled and how it is evaluated.

---

## 1. The forecasting task

> For every hour of target day D, predict all-India electricity demand
> using only information available at 09:00 IST on day D-1.

Target: `demand_mw`, the `NLDC_DEMAND|P` SCADA point averaged to the hour, in
megawatts, timestamped in Asia/Kolkata.

This convention is stated in `solaris.config.FORECAST_ISSUE_HOUR_IST` and is
carried into `reports/evaluation.json`, so no table can be quoted without it.

Two things follow directly, and they shape the whole design:

Forecast horizons vary across the day. Predicting 00:00 on day D from
09:00 on D-1 is a 15-hour-ahead call. Predicting 23:00 on day D is 38 hours
ahead. There is no single horizon.

The shortest safe demand lag is 24 hours, not 1. Demand at hour t-1 is
not known when the forecast is issued for most target hours. Every demand
feature in this project is therefore at a lag of 24 hours or more, and the
leakage guard enforces it.

---

## 2. Timezone handling

Grid-India despatch centres run on Indian Standard Time. The source workbooks
carry naive wall-clock timestamps, which this project localises directly to
`Asia/Kolkata`.

India observes no daylight saving, so IST is a fixed UTC+05:30. There are
no ambiguous or non-existent local times, which is why localisation needs no
DST resolution policy. The weather API is queried with `timezone=Asia/Kolkata`
so both sides of the join are already in the same zone; `build_dataset`
raises if either index is naive or in a different zone.

---

## 3. Leakage safeguards

Leakage is the main way a forecasting project can produce impressive and
meaningless numbers. Four defences are in place, and the test suite exercises
each.

### 3.1 No observed weather, ever

Weather inputs come from Open-Meteo's Previous Runs API
(`temperature_2m_previous_day1`), the value the timestamp was given by the
model run issued a day earlier. Observed weather is retrieved too, but every
such column carries an `_obs` suffix, `build_feature_frame` drops them by
default, and `assert_no_leakage` raises on any feature set containing one.

### 3.2 No contemporaneous grid measurements

Solar, wind, thermal, hydro, nuclear, total generation and residual load at the
target hour are all measured at the same instant as the target and are unknown
a day ahead. `assert_no_leakage` rejects all of them by name. They are used for
descriptive analysis only, the evening-ramp section measures what the grid
did, it does not predict it.

### 3.3 No demand lag shorter than 24 hours

`assert_no_leakage` parses any `demand_lag_<n>h` feature and raises if
`n < 24`. Rolling same-hour statistics are shifted by 24 hours before the
window is applied, so a window can never include the target hour. Daily
aggregates are shifted in whole days, so day D only ever sees D-1 and earlier.

A dedicated test (`test_history_features_do_not_see_the_future`) recomputes
every history feature on a truncated copy of the data and asserts the values
are identical, if any feature looked forward, truncation would change it.

### 3.4 Preprocessing fitted on training rows only

The Ridge baseline's `StandardScaler` is fitted inside a pipeline on the
training slice and applied unchanged to validation and test. The hot-period
temperature threshold is computed on the training period and applied to all
three blocks. LightGBM early stopping uses the validation block; the test block
is touched only at final scoring.

---

## 4. Chronological split

No random splitting is used anywhere.

| Block | Range (IST) | Hours | Role |
| --- | --- | --- | --- |
| Train | 2021-09-01 to 2022-12-31 | 11,671 | Model fitting; hot-period threshold |
| Validation | 2023-01-01 to 2023-02-28 | 1,416 | Early stopping, model selection |
| Test | 2023-03-01 to 2023-12-31 | 7,344 | Final scoring only |

Why the test block is placed here. An earlier split that trained through
April 2023 was rejected: its test block (July-December) contained zero hours
above the hot-period threshold, because India's hot season runs March-June. A
test period with no hot weather cannot answer a research question about hot
weather. The current test block contains the pre-monsoon hot season, the
monsoon, the all-time peak demand hour of 1 September 2023, and the winter
trough.

The trade-off. The test block is longer than the training block. That is
accepted deliberately, 16 months of training still covers a complete annual
cycle, and a test period without a hot season would make the central analysis
impossible. The rolling-origin backtest is the check that this one split is
not a fluke: the model is refitted every month on all earlier data and scored
on the month it has never seen, across 12 folds.

---

## 5. Features

Three nested feature sets. The models are identical apart from which set they
may use, which is what makes the comparison a clean experiment.

### Calendar (11 features)
Hour, cyclical hour (sin/cos), weekday, weekend flag, month, cyclical month,
day of year, national holiday flag, and a slow linear trend term
(`days_from_start`) that lets tree models express multi-year load growth.

### Demand history (+9 features)
Lags at 24 h, 48 h and 168 h. Rolling mean and standard deviation of the same
hour-of-day over the previous 7 days. Previous-day mean and maximum demand,
previous-week mean, and the difference between them as a momentum term.

### Weather (+17 features)
All from archived day-ahead forecast temperature:

- Population-weighted national forecast temperature, hourly
- Hottest load centre and spread across the 18 cities
- Cooling degree-hours above 24 °C, heating degree-hours below 18 °C
- Daily forecast maximum, mean, diurnal range, and total cooling degree-hours
- 24-hour trailing mean (building thermal inertia)
- Day-on-day change in daily mean temperature
- Three-day accumulated cooling degree-days (consecutive hot days load the
  grid harder than one isolated hot day)
- Population-weighted forecast temperature for each of the five regional grids

Degree-day bases: 18 °C is the conventional HDD base in energy statistics;
24 °C is used for cooling because it is closer to the threshold at which Indian
air-conditioning load switches on.

---

## 6. Models

| Model | What it is |
| --- | --- |
| `naive_lag_24h` | Same hour, previous day. The baseline to beat. |
| `naive_lag_168h` | Same hour, previous week. |
| `climatology_hour_weekday` | Mean demand per (hour, weekday), fitted on training rows. |
| `ridge_calendar_history` | Ridge regression on calendar + history, scaled on training only. |
| `lgbm_calendar_history_level` | LightGBM, level target, no weather. |
| `lgbm_weather_level` | LightGBM, level target, with weather. |
| `lgbm_calendar_history` | LightGBM, delta target, no weather. |
| `lgbm_weather` | LightGBM, delta target, with weather. Headline model. |
| `lgbm_quantile` | Three LightGBM quantile models (P10/P50/P90), delta target. |

### Why there are level and delta variants

A gradient-boosted tree predicts a weighted average of values it saw in
training, so it can never output a number above its training maximum.
All-India demand grew substantially across the period: the training block tops
out at 209.8 GW, the test block reaches 239.4 GW, and 526 test hours exceed
anything the level model was trained on. The level model duly saturates, its
highest prediction over the entire test period is 204.7 GW.

The delta variants predict `demand(t) - demand(t-24h)` and add the persistence
anchor back at prediction time. The difference is much closer to stationary, so
the model tracks load growth instead of hitting a ceiling; its highest
prediction is 237.2 GW. The anchor, `demand_lag_24h`, is itself a legal
day-ahead feature.

Both variants are kept and reported. The level models lose to the naive
baseline, and that negative result is on the dashboard, because the reason is
instructive rather than embarrassing.

### Hyperparameters

Fixed and modest: learning rate 0.05, 63 leaves, minimum 40 rows per leaf,
feature and bagging fraction 0.85, L2 regularisation 1.0, up to 3,000 rounds
with early stopping after 100. Chosen for a ~20k-row dataset, selected against
the validation block only, and never tuned against the test block.
Seeded (`seed=42`, `deterministic=True`) so runs reproduce.

---

## 7. Evaluation

### Metrics

MAE, RMSE, MAPE, bias and R2, all in MW, plus:

- Daily peak error: error in the daily maximum, and in the hour it occurs.
  Only days with a complete 24 hours are scored. Peak-hour error is treated as
  circular, so 23:00 against 00:00 is one hour apart, not twenty-three.
- Sliced metrics: hot period, non-hot, evening window (17:00-22:00 IST),
  hot evenings, and the top decile of demand hours.
- By hour of day, to show when the model struggles.
- Pinball loss and empirical coverage for the quantile models.

Any slice with fewer than 200 matched hours is reported with its row count and
a `sufficient: false` flag instead of a metric, so a thin slice can never be
quoted as a headline result.

### Significance testing

Hourly errors are strongly autocorrelated, so the paired test between the
weather and no-weather models is run on daily mean absolute error, which is
much closer to independent. Both a paired t-test and a Wilcoxon signed-rank
test are reported, with a 95% confidence interval on the mean daily
improvement and the share of days improved.

### Rolling-origin backtest

Expanding window, monthly refit: for each month from 2023-01 to 2023-12, the
model is fitted on every hour before that month and scored on the month itself.
12 folds.

### Sensitivity analyses

- Weather weighting: the full experiment refitted under `pop`, `equal` and
  `region` city weights.
- Hot-period threshold: the hot-period comparison recomputed at the p80,
  p85, p90 and p95 thresholds.

---

## 8. Definitions that could otherwise be misread

### Research hot period, not an IMD heatwave

A day is labelled hot when its population-weighted national daily maximum
day-ahead forecast temperature is at or above the 90th percentile of daily
maxima over the training period, which works out at 37.67 °C.

This is deliberately not an India Meteorological Department heatwave
declaration. IMD declares heatwaves per station against that station's own
normal, with different criteria for plains, coastal and hilly regions. Those
criteria cannot be reconstructed faithfully from a national weighted index.
The label is called "research-defined" everywhere it appears, and the threshold
is swept in the sensitivity analysis.

### Research residual load, not an operator net load

```
residual_load_mw = demand_mw - max(solar_raw_mw, 0) - wind_mw
```

This is an indicator of the evening ramp, not an operator's net-load
figure. It excludes behind-the-meter rooftop solar (which appears in neither
series), interchange and storage, and `NLDC_DEMAND|P` is itself a met-demand
signal rather than a gross load. The definition travels with every statistic
computed from it.

### Association, not causation

Every reported relationship between temperature and demand is a predictive
association. No causal identification strategy is used. Temperature is
correlated with season, working patterns and economic activity, and nothing
here separates those. That weather features reduce forecast error is evidence
that temperature forecasts carry information about demand, it is not an
estimate of how much demand a degree of warming causes.

---

## 9. Reproducibility

```bash
python -m solaris.cli fetch-grid      # ~713 MB from Mendeley
python -m solaris.cli fetch-weather   # archived forecasts, 18 cities
python -m solaris.cli prepare         # parse, join, quality-check
python -m solaris.cli train --rolling # fit and evaluate everything
python -m solaris.cli report          # regenerate figures and tables
```

`reports/evaluation.json` is the single source of every number the project
publishes. `reports/EVALUATION.md`, the figures and the tables are rendered
from it, and `solaris.webexport` copies the subset the dashboard needs into
`app/data/*.json`. None contains a hand-entered metric.

The web app reads only that exported bundle, with the standard library. It
imports neither the research package nor pandas, which is why the deployed
function needs just four packages (`requirements.txt`) rather than the full
modelling stack (`requirements-research.txt`), and why it cannot display a
figure the pipeline did not produce. A test parses the `app/` source tree and
fails if that separation is ever broken.

Remaining nondeterminism. LightGBM is seeded and run with
`deterministic=True` and `force_row_wise=True`, so results reproduce on the
same platform and library versions. Small differences across LightGBM versions
or CPU architectures are possible. Downloads depend on the upstream services
remaining available; the Mendeley manifest including SHA-256 hashes is saved
to `data/raw/grid_india_mendeley/_manifest.json` on fetch.
