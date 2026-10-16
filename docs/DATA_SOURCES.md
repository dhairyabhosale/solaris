# Data sources

Every dataset used by Solaris Grid Intelligence, with its provenance, licence,
coverage, retrieval route and the discrepancies that were found when the files
were actually inspected.

Retrieval date for everything below: 16 October 2026.

---

## 1. All-India electricity demand and generation (Grid-India / NERLDC)

| Field | Value |
| --- | --- |
| Publisher of the mirror | Mendeley Data (Elsevier) |
| Originating source | North-Eastern Regional Load Despatch Centre (NERLDC), Grid-India, Ministry of Power, Government of India |
| Citation | Mukherjee, D.; Kalita, K.; Kumar, S. (2024), Electricity Demand, Solar and Wind Generation Data (2021-2023) of India at 1-hour interval, Mendeley Data, V1 |
| DOI | [10.17632/y58jknpgs8.1](https://doi.org/10.17632/y58jknpgs8.1) |
| Landing page | https://data.mendeley.com/datasets/y58jknpgs8 |
| Licence | CC BY 4.0, share and adapt with attribution |
| Files | 28 monthly `.xlsx` workbooks, ~713 MB total |
| Coverage retrieved | 2021-09-01 00:00 to 2023-12-31 23:00 IST, 20,448 hours, 100% complete |
| Retrieval | `python -m solaris.cli fetch-grid` |

### Variables used

All power values are megawatts (MW), read from OSIsoft SCADA analog points
exported by the despatch centre.

| Source point | Column in this project | Meaning |
| --- | --- | --- |
| `NLDC_DEMAND\|P` | `demand_mw` | All-India metered demand, the forecast target |
| `TOTAL_THM_ONLY\|P` | `thermal_mw` | Thermal generation |
| `TOTAL_NLDC_HYD\|P` | `hydro_mw` | Hydro generation |
| `TOTAL_NLDC_GAS\|P` | `gas_mw` | Gas generation |
| `TOTAL_NLDC_NPC\|P` | `nuclear_mw` | Nuclear generation |
| `ALL_INDIA_WIND\|P` | `wind_mw` | All-India wind generation |
| `ALL_IND_SOLAR\|P` | `solar_raw_mw` | All-India solar generation (raw) |

Points present in the files but not used: `INTRA_SYSTEM_INTERTIA|P`,
`ER_Total_Inertia|P`, `SYS_IN|P`, `SPARE 05`. The two inertia points and
`SYS_IN` are zero for the whole of the 2021-2022 workbooks and are not needed
for a demand forecast.

### Verified discrepancies

These were found by downloading and opening the files, not by reading the
description. They are the reason the brief's instruction to inspect before
trusting matters.

1. The dataset is not hourly, despite its title.

The title says "at 1-hour interval". It is not. Every workbook carries a hidden
`_osi_config` sheet whose JSON records the export sample period in seconds:

| Period | Workbooks | Sample interval | Rows per month |
| --- | --- | --- | --- |
| Sep 2021, Sep 2022 | 13 | `"r":300.0` to 5 minutes | ~8,640 |
| Oct 2022, Dec 2023 | 15 | `"r":10.0` to 10 seconds | ~267,840 |

This project reads that field, records it per workbook in
`data/interim/grid_workbooks.json`, and aggregates both regimes to true
hourly means. The number of raw samples behind each hourly value is kept as
`n_samples` so the difference stays auditable, a 2023 hour is an average of
360 samples, a 2021 hour an average of 12.

2. The sampling regime changes mid-dataset. The switch happens exactly at
October 2022. Hourly aggregation makes the two regimes comparable, but the
change is recorded rather than smoothed over.

3. Three columns are Excel formulas, not data. Columns H, I and J
("Solar", "Wind+Solar"/"Solar+Wind", "Total") contain formula strings such as
`=MAX(0,G3)`. A naive read picks up the formula text or a stale cached value.
This project ignores those columns entirely and recomputes the derived
series from the raw SCADA points, so the derivation is explicit and tested.

4. Raw solar reads negative at night. `ALL_IND_SOLAR|P` sits at roughly
-10 to -25 MW overnight. This is a SCADA measurement offset, not generation.
Both series are kept: `solar_raw_mw` unchanged, and `solar_mw` clipped at zero
for every downstream use.

5. One filename is misspelled. The dataset ships `Novemeber 2023.xlsx`.
The parser accepts it explicitly rather than skipping the month.

6. Column header text differs between regimes. The 2021-2022 workbooks
label column I `Solar+Wind`; the 2023 workbooks label it `Wind+Solar`. Since
the pipeline recomputes that column, the inconsistency does not propagate.

### Data quality as measured

From `reports/data_quality.json`, produced by `python -m solaris.cli prepare`:

- 20,448 hourly rows, 0 missing hours, 0 duplicate timestamps
- 17 hours flagged implausible (0.08%): 12 are exactly 0 MW across every
  point, whole-system SCADA dropouts, and 5 more fall below 70 GW, which
  the all-India grid never reaches. All are excluded from modelling by the
  `is_valid` flag rather than silently deleted.
- Dropout clusters: 2021-10-08 20:00 to 2021-10-09 02:00, 2021-11-21,
  2022-05-23, 2022-07-03, 2022-10-01.
- 20,431 valid modelling hours remain.

### Cross-check against official records

The highest hourly mean demand in the dataset is 239,406 MW at 11:00 IST on
1 September 2023.

Press Trust of India, citing government data, reported a record of
239.97 GW on 1 September 2023, the same day, within 0.24% of this
dataset's value. The small gap is expected and definitional: this project
reports an hourly mean, whereas the official peak is an instantaneous or
short-block maximum, which is necessarily higher.

A separate figure of 243.27 GW for September 2023 appears in a Ministry of
Power Lok Sabha answer. That value does not appear anywhere in this
dataset, whose September 2023 maximum is 239.4 GW. The likely explanation is
again definitional, "peak demand met" as reported by the Ministry is not the
same series as the `NLDC_DEMAND|P` SCADA point averaged to the hour. This is
recorded as an unresolved difference rather than reconciled by assumption, and
no claim about the 243.27 GW figure is made anywhere in this project.

Sources for the cross-check:
- [Peak power demand hits season's new high of nearly 240 GW](https://psuwatch.com/newsupdates/peak-power-demand-hits-seasons-new-high-of-nearly-240-gw-on-friday) (PSU Watch, Sept 2023)
- [Lok Sabha answer AU2058](https://eparlib.nic.in/bitstream/123456789/2970305/1/AU2058.pdf) (Ministry of Power, Dec 2023)
- [CRISIL, Power play](https://integraliq.crisil.com/content/crisil/en/Not-used-hme/our-analysis/views-and-commentaries/2023/09/power-play.html) (Sept 2023)

---

## 2. Archived day-ahead weather forecasts (Open-Meteo Previous Runs API)

| Field | Value |
| --- | --- |
| Publisher | Open-Meteo |
| Endpoint | `https://previous-runs-api.open-meteo.com/v1/forecast` |
| Documentation | https://open-meteo.com/en/docs/previous-runs-api |
| Licence | CC BY 4.0; attribution "Weather data by Open-Meteo.com" |
| Coverage retrieved | 2021-08-31 to 2024-01-01, hourly, 18 cities |
| Retrieval | `python -m solaris.cli fetch-weather` |

This is the leakage-safe weather input. The API exposes
`<variable>_previous_day1`: the value a timestamp was assigned by the model run
issued one day earlier. That is genuinely what a day-ahead forecaster would
have had, as opposed to what the weather turned out to be.

That the two differ was verified directly. For 15 May 2022 in Delhi, the
day-ahead forecast for 12:00-15:00 was 45.4 / 46.3 / 46.7 / 46.9 °C while the
observed values were 44.6 / 44.9 / 45.2 / 45.4 °C, a real forecast with a real
warm bias, not a relabelled observation.

### Verified source limitation: only temperature is archived

Each of the following was requested with the `_previous_day1` suffix for a 2022
date. All returned HTTP 200 with an entirely null series:

`relative_humidity_2m`, `dew_point_2m`, `apparent_temperature`,
`shortwave_radiation`, `cloud_cover`, `wind_speed_10m`, `precipitation`,
`surface_pressure`, `wind_speed_100m`, `direct_radiation`,
`et0_fao_evapotranspiration`, `weather_code`.

Only `temperature_2m` returns data over 2021-2023.

Consequence, and the choice made. Those variables are therefore not
available at a genuine day-ahead lead from this source. They are excluded
from the operational model rather than substituted with observed values,
because substituting observations is precisely the leakage this project exists
to avoid. The weather feature set gets its breadth from transforming
temperature, degree-hours, daily aggregates, diurnal range, multi-day heat
accumulation, and the spatial spread across load centres, rather than from
more variables. This is a real limitation and is stated on the dashboard, in
the evaluation report and in the model card.

### Coverage gap

The archived forecast is missing for 2023-12-30 05:00 to 2024-01-01 23:00
(67 hours, of which 43 fall inside the dataset range). Gaps of up to 3 hours
are filled by time interpolation; this one is far longer, so it is left as
`NaN`. The weather model produces no prediction for those hours and the
dashboard shows the gap rather than filling it. This is why the headline model
is scored on 7,301 of the 7,344 test hours.

---

## 3. Observed weather (Open-Meteo Historical Weather API)

| Field | Value |
| --- | --- |
| Endpoint | `https://archive-api.open-meteo.com/v1/archive` |
| Documentation | https://open-meteo.com/en/docs/historical-weather-api |
| Licence | CC BY 4.0 |
| Variables | temperature, relative humidity, dew point, apparent temperature, shortwave radiation, cloud cover, wind speed |

Retrieved for retrospective analysis only. Every column carries an `_obs`
suffix, and `solaris.features.assert_no_leakage` raises on any feature set
containing one. The test suite asserts this.

Note that the observed and forecast series come from different underlying
models, the archive is reanalysis-based at a coarser grid, the previous-runs
archive comes from operational forecast models. Over the retrieved period the
population-weighted forecast index averages 27.7 °C against 25.5 °C observed.
The two are therefore not interchangeable, which is a further reason the
model uses the forecast series consistently and never mixes them.

---

## 4. Spatial weighting of weather

All-India demand is one national series; weather is local. Using one city's
temperature as a national driver would be wrong, so a weighted national index
is built over 18 load-centre cities spanning all five regional grids:

| Region | Cities |
| --- | --- |
| Northern (NR) | Delhi, Jaipur, Lucknow, Ludhiana |
| Western (WR) | Mumbai, Ahmedabad, Pune, Surat, Nagpur, Indore |
| Southern (SR) | Bengaluru, Chennai, Hyderabad, Coimbatore |
| Eastern (ER) | Kolkata, Patna, Bhubaneswar |
| North-Eastern (NER) | Guwahati |

Three weightings are computed so the choice can be audited:

- `pop` (default), Census of India 2011 urban agglomeration population
- `equal`, every city counts the same
- `region`, each regional grid gets one fifth, split within by population

This is a documented weakness. Population is a proxy for each region's
share of electricity demand, not a measurement of it. A reproducible,
machine-readable series of actual city-level demand weights could not be
obtained from Grid-India or CEA. Population understates industrial and
agricultural load, which is not proportional to urban population.

The sensitivity analysis in `reports/evaluation.json` refits the whole model
under all three schemes. The weather model beats the no-weather model under
every one (11.07%, 10.52% and 7.29% MAE reduction respectively), so the
central finding does not rest on this choice, though the magnitude does
depend on it.

Coordinates are city-centre points; Open-Meteo snaps them to its model grid,
so the value returned represents a grid cell of roughly 10 km, not a station.

---

## 5. Calendar

Indian national public holidays come from the `holidays` Python package
(India calendar), restricted to nationwide observances. State-specific
festivals vary too much between states to treat as national demand drivers and
are not used.

---

## Sources consulted but not used

| Source | Why not used |
| --- | --- |
| [Grid-India](https://grid-india.in/) | The authoritative publisher. Its public reports are PSP/monthly PDFs, not a machine-readable hourly series for 2021-2023; the Mendeley mirror of its NERLDC SCADA export is the reproducible route to the same underlying data. |
| [National Power Portal](https://www.npp.gov.in/) | Aggregated dashboards and reports; no reproducible hourly all-India demand download for the required period. |
| [CEA](https://cea.nic.in/) | Used for definitional context only. Its published series are monthly/annual, not hourly. |
| [IMD](https://mausam.imd.gov.in/) | Official heatwave criteria are station-based departures from a station normal. They cannot be reconstructed faithfully from a population-weighted national index, so this project defines an explicitly separate research hot period and never calls it an IMD heatwave. |
| [Copernicus ERA5](https://cds.climate.copernicus.eu/) | Reanalysis, not an operational forecast. Using it as a day-ahead input would be the leakage this project avoids. Open-Meteo's archive already provides an equivalent observed series. |
| [NASA POWER](https://power.larc.nasa.gov/) | ~0.5° grid is coarse for city-level load centres, and it provides observations rather than archived forecasts. |

---

## Attribution

- Electricity data: Mukherjee, Kalita & Kumar (2024), Mendeley Data,
  doi:10.17632/y58jknpgs8.1, CC BY 4.0. Originating source: NERLDC, Grid-India,
  Ministry of Power, Government of India.
- Weather data by [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0.
