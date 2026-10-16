# Model card — `lgbm_weather`

The headline day-ahead demand forecasting model of Solaris Grid Intelligence.

Generated from the evaluation run recorded in `reports/evaluation.json`. Every
metric below was computed by `solaris.evaluate` on held-out data.

---

## Model details

| Field | Value |
| --- | --- |
| Name | `lgbm_weather` |
| Type | LightGBM gradient-boosted regression trees |
| Target parameterisation | **Delta**: predicts `demand(t) − demand(t−24h)`, then adds the anchor back |
| Target | `demand_mw` — all-India metered demand (`NLDC_DEMAND|P`), hourly mean, **MW** |
| Features | 37 (11 calendar, 9 demand history, 17 weather) |
| Training rows | 11,503 hours |
| Training range | 2021-09-01 → 2022-12-31 IST |
| Boosting rounds used | 61 (early stopping on the validation block) |
| Random seed | 42, `deterministic=True` |
| Version | 1.0.0 |
| Licence | Same as the repository |
| Artefacts | `models/lgbm_weather.txt`, `models/lgbm_weather.meta.json` |

A companion `lgbm_quantile` ensemble fits P10/P50/P90 with the same features
and target parameterisation. **Its calibration is poor — see Limitations.**

---

## Intended use

**What it is for**

- Research into whether archived weather forecasts improve day-ahead
  electricity demand forecasting for India.
- A reproducible, honestly-evaluated worked example of time-series forecasting
  with explicit leakage control.
- Retrospective analysis of demand, heat and evening ramp behaviour over
  2021–2023.

**What it is not for**

- **Operational grid despatch, scheduling, reserve sizing or trading.** The
  model is evaluated on 2023 data, has never been validated against live
  operations, and carries no service guarantees.
- **Any safety-critical decision.** Nothing here should inform decisions where
  a wrong forecast causes harm.
- **Causal inference.** The model predicts; it does not estimate the causal
  effect of temperature on demand.
- **Forecasting after 2023.** The dataset ends 31 December 2023. The model has
  not been evaluated on any later period, and no claim is made about 2024–2026
  demand records.
- **Regional, state or utility-level forecasting.** The target is all-India
  only.

---

## Training and evaluation data

See `docs/DATA_SOURCES.md` for full provenance.

| Block | Range (IST) | Hours |
| --- | --- | --- |
| Train | 2021-09-01 → 2022-12-31 | 11,671 |
| Validation | 2023-01-01 → 2023-02-28 | 1,416 |
| Test | 2023-03-01 → 2023-12-31 | 7,344 |

Chronological throughout; no random splitting. The test block was used only
for final scoring — never for fitting, early stopping or threshold selection.

Demand and generation: Grid-India / NERLDC SCADA via Mendeley Data
(doi:10.17632/y58jknpgs8.1, CC BY 4.0), aggregated from 5-minute and
10-second exports to hourly means. Weather: Open-Meteo Previous Runs API,
archived day-ahead forecast temperature, population-weighted across 18
load-centre cities.

---

## Metrics — held-out test period, 2023-03-01 to 2023-12-31

Scored on 7,301 of 7,344 hours; 43 hours have no archived weather forecast
and receive no prediction.

### Against every baseline

| Model | MAE (MW) | RMSE (MW) | MAPE (%) | Bias (MW) | R² | vs naive-24h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`lgbm_weather`** | **3,398** | **4,565** | **1.87** | −615 | **0.945** | **+31.2%** |
| `lgbm_calendar_history` | 3,822 | 5,067 | 2.09 | −717 | 0.932 | +22.6% |
| `ridge_calendar_history` | 4,662 | 6,114 | 2.58 | 349 | 0.901 | +5.6% |
| `naive_lag_24h` | 4,938 | 6,691 | 2.72 | 31 | 0.882 | 0.0% |
| `lgbm_calendar_history_level` | 5,443 | 7,524 | 2.89 | −2,345 | 0.850 | −10.2% |
| `lgbm_weather_level` | 5,528 | 7,799 | 2.90 | −3,099 | 0.839 | −11.9% |
| `naive_lag_168h` | 9,807 | 12,346 | 5.43 | 117 | 0.597 | −98.6% |
| `climatology_hour_weekday` | 22,386 | 26,167 | 11.71 | −20,132 | −0.811 | −353.3% |

### Daily peak

| Metric | Value |
| --- | --- |
| Days scored | 304 |
| Peak MAE | 3,281 MW (1.65% of peak) |
| Peak bias | −850 MW (slightly under-forecasts peaks) |
| Peak-hour MAE | 1.63 hours |
| Peak hour exactly right | 50.7% of days |
| Peak hour within ±1 hour | 77.0% of days |

### By slice

| Slice | Hours | MAE (MW) |
| --- | ---: | ---: |
| All test hours | 7,301 | 3,398 |
| Research-defined hot hours | 936 | 3,410 |
| All other hours | 6,365 | 3,397 |
| Evening window (17:00–22:00 IST) | 1,824 | 3,156 |
| Hot evenings | 234 | 2,875 |
| Top decile of demand hours | 734 | 3,852 |

---

## The contribution of weather information

`lgbm_weather` and `lgbm_calendar_history` are the same algorithm with the same
hyperparameters and the same target parameterisation. Only the feature set
differs, so the gap between them is the contribution of weather information.

| Measure | Value |
| --- | --- |
| MAE without weather | 3,822 MW |
| MAE with weather | 3,398 MW |
| **MAE reduction** | **11.07%** (424 MW) |
| RMSE reduction | 9.91% |
| Paired t-test on daily MAE | t = 6.50, p = 3.2 × 10⁻¹⁰, n = 305 days |
| Wilcoxon signed-rank | p = 1.5 × 10⁻¹⁰ |
| Mean daily MAE improvement | 432 MW (95% CI 301 to 563 MW) |
| Days improved | 65.6% |

### It helps most when it is hot

| Slice | MAE reduction from weather |
| --- | ---: |
| Research-defined hot hours | **21.45%** |
| All other hours | 9.32% |
| Evening window | 12.43% |
| Top decile of demand | 16.80% |

This holds at every hot-period threshold tested (p80: 15.7%, p85: 18.8%,
p90: 21.5%, p95: 22.6%), so the finding does not depend on where the line is
drawn.

### It holds under a rolling backtest

Expanding window, refitted monthly, 12 folds over 2023:

| Model | Mean fold MAE (MW) |
| --- | ---: |
| `lgbm_weather` | 3,227 |
| `lgbm_calendar_history` | 3,731 |
| `naive_lag_24h` | 4,728 |

Weather reduced MAE in **9 of 12 folds**; mean 11.5%, range −2.9% to +27.7%.

### It holds under different weather weightings

| City weighting | MAE (MW) | Reduction vs no weather |
| --- | ---: | ---: |
| Population (default) | 3,398 | 11.07% |
| Equal | 3,420 | 10.52% |
| Region-balanced | 3,543 | 7.29% |

---

## What the model relies on

Top features by share of total split gain:

| Feature | Gain share |
| --- | ---: |
| `demand_lag_24h` | 0.362 |
| `dayofweek` | 0.123 |
| `demand_same_hour_std_7d` | 0.076 |
| `hour` | 0.076 |
| `demand_trend_1d_7d` | 0.067 |
| `temp_fc_anomaly_vs_prev_day` | 0.040 |
| `demand_daily_max_lag_1d` | 0.021 |
| `demand_daily_mean_lag_1d` | 0.020 |

Recent demand dominates, which is expected for a 24-hour-ahead load forecast.
The highest-ranked weather feature is the **day-on-day change in forecast
temperature** — consistent with a delta-target model, which is asking "how
will tomorrow differ from today?" rather than "how hot will it be?".

**Caveat.** Split gain shows what the model leans on, not what drives the
grid. Correlated features share credit arbitrarily, and nothing here identifies
a causal effect.

---

## Failure modes

**Saturation on a growing series (solved, but instructive).** A level-target
tree cannot predict above its training maximum. The training block tops out at
209.8 GW while the test block reaches 239.4 GW, with 526 test hours above
anything a level model saw. The level variants duly saturate at 204.7 GW and
lose to the naive baseline. The delta parameterisation fixes this (highest
prediction 237.2 GW), but the same trap will reappear if the model is left
unretrained as load continues to grow.

**Under-forecasting the highest hours.** Bias is −615 MW overall and −850 MW on
daily peaks: the model is slightly conservative exactly where a system operator
cares most. MAE on the top decile of demand hours (3,852 MW) is worse than
overall (3,398 MW).

**Dependence on the persistence anchor.** Because predictions are built on
`demand_lag_24h`, an anomalous previous day propagates into the forecast. The
model has no mechanism to recognise that yesterday was itself unusual.

**Holiday behaviour.** Only nationwide holidays are flagged. Regional festivals
with large local load effects are invisible to the model.

**Missing weather.** The archived forecast is absent for 43 test hours; the
model produces no prediction rather than guessing.

**Shifted weather distribution.** The forecast temperature series runs about
2.2 °C warmer than the observed series because they come from different
underlying models. The model is internally consistent, but a different weather
provider would need refitting, not substitution.

---

## Limitations

1. **Only temperature is available at a genuine day-ahead lead.** Open-Meteo
   does not archive humidity, radiation, cloud cover or wind forecasts for
   2021–2023. They are excluded rather than replaced with observations.
   Humidity in particular is likely to matter for Indian cooling load, so the
   measured 11.07% gain is probably a **lower bound** on what weather
   information could contribute.

2. **Population-weighted weather is a proxy.** City population stands in for
   each region's share of electricity demand because actual demand weights were
   not reproducibly obtainable. It understates industrial and agricultural
   load.

3. **The uncertainty band is not calibrated.** The P10–P90 interval covers the
   actual value **57.5%** of the time on the test period against a nominal 80%
   (P90 alone: 73.0% against 90%). On the validation block it covers 72.0%, so
   the band is roughly right where it was tuned and too narrow out of sample.
   The validation block (January–February) has a mean daily demand range of
   58.9 GW against 34.7 GW on test — distribution shift of exactly the kind
   that produces this failure. **The band should not be used as a risk measure
   without recalibration on a period spanning a hot season.**

4. **The hot-period label is research-defined**, a temperature percentile, not
   an IMD heatwave declaration.

5. **Residual load is an indicator, not an operator net load.** It excludes
   behind-the-meter solar, interchange and storage.

6. **Associations are not causal.** Temperature is confounded with season,
   working patterns and economic activity.

7. **The training block is shorter than the test block** (16 months against
   10). This was chosen deliberately so the test period would contain a hot
   season; the rolling backtest is the mitigation.

8. **Single-source demand data.** All demand figures trace to one Mendeley
   mirror of a Grid-India SCADA export. The peak was cross-checked against
   press and Ministry reporting, and a definitional difference against one
   official figure (243.27 GW for September 2023) remains unresolved.

---

## Ethical and practical considerations

The model forecasts an aggregate national quantity and uses no personal data.
The main risk is **misuse through over-trust**: presenting a retrospective
research result as an operational forecast, or reading the uncertainty band as
a calibrated risk measure when it is not. The dashboard states the data is
historical, labels the test period on every performance view, and carries the
calibration failure as a warning rather than a footnote.

---

## Maintenance

The model is **not** maintained on a schedule and does not auto-refresh. It is
a fixed research artefact over 2021–2023. Retraining on newer data requires
re-running the pipeline; given the load-growth saturation failure mode above,
the delta parameterisation should be retained in any retrain.
