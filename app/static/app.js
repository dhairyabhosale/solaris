/* Solaris Grid Intelligence - frontend.
 *
 * Every figure here is drawn from the API, which reads the research
 * pipeline's own output files. There are no hard-coded metrics: if an
 * artefact is missing, the view says what command produces it rather than
 * showing a plausible-looking placeholder.
 */

'use strict';

const isLight = window.matchMedia('(prefers-color-scheme: light)').matches;

const PALETTE = {
  // The actual-demand line is the reference every chart is read against, so it
  // has to be the highest-contrast colour in whichever theme is active.
  actual:   isLight ? '#0d1522' : '#e8edf5',
  weather:  '#f59e0b',
  baseline: isLight ? '#0891b2' : '#22d3ee',
  naive:    isLight ? '#7b8aa3' : '#6b7a92',
  band:     'rgba(245, 158, 11, 0.16)',
  solar:    '#d97706',
  wind:     isLight ? '#059669' : '#34d399',
  residual: isLight ? '#7c3aed' : '#a78bfa',
  heat:     '#ef4444',
  good:     isLight ? '#059669' : '#34d399',
};

const PLOT_LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: {
    family: 'Inter, system-ui, sans-serif',
    size: 12,
    color: isLight ? '#51607a' : '#9aa8bd',
  },
  margin: { l: 62, r: 18, t: 14, b: 48 },
  hovermode: 'x unified',
  xaxis: { gridcolor: isLight ? '#e6ebf3' : '#1e2838', zeroline: false },
  yaxis: { gridcolor: isLight ? '#e6ebf3' : '#1e2838', zeroline: false },
  legend: { orientation: 'h', y: -0.18, x: 0 },
};

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

// --------------------------------------------------------------------------
// Utilities
// --------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = {};
    try { detail = (await response.json()).detail || {}; } catch (_) { /* non-JSON */ }
    const error = new Error(detail.message || `Request failed (${response.status})`);
    error.command = detail.command;
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function fmt(value, decimals = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  return Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtGW(mw, decimals = 1) {
  if (mw === null || mw === undefined || Number.isNaN(mw)) return 'n/a';
  return `${(mw / 1000).toFixed(decimals)} GW`;
}

function dateOnly(iso) {
  return iso ? String(iso).slice(0, 10) : '';
}

function showError(container, error) {
  container.innerHTML = `
    <div class="empty-state">
      <div>${escapeHtml(error.message)}</div>
      ${error.command ? `<code>${escapeHtml(error.command)}</code>` : ''}
    </div>`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = String(text ?? '');
  return div.innerHTML;
}

function toast(message) {
  const element = $('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { element.hidden = true; }, 5000);
}

function plot(id, traces, layoutOverrides = {}) {
  const node = $(id);
  if (!node) return;
  node.innerHTML = '';
  Plotly.newPlot(node, traces, { ...PLOT_LAYOUT, ...layoutOverrides }, PLOT_CONFIG);
}

// --------------------------------------------------------------------------
// Navigation
// --------------------------------------------------------------------------

const loaded = new Set();

function showView(name) {
  document.querySelectorAll('.view').forEach((view) => {
    view.classList.toggle('is-active', view.id === `view-${name}`);
  });
  document.querySelectorAll('.nav-link').forEach((link) => {
    link.classList.toggle('is-active', link.dataset.view === name);
  });
  $('nav').classList.remove('is-open');
  $('navToggle').setAttribute('aria-expanded', 'false');
  window.scrollTo({ top: 0, behavior: 'smooth' });

  if (!loaded.has(name)) {
    loaded.add(name);
    const loader = VIEW_LOADERS[name];
    if (loader) loader().catch((error) => { console.error(error); toast(error.message); });
  }
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
}

document.querySelectorAll('.nav-link').forEach((link) => {
  link.addEventListener('click', () => showView(link.dataset.view));
});
$('navToggle').addEventListener('click', () => {
  const nav = $('nav');
  const open = nav.classList.toggle('is-open');
  $('navToggle').setAttribute('aria-expanded', String(open));
});

// --------------------------------------------------------------------------
// Overview
// --------------------------------------------------------------------------

let OVERVIEW = null;

/** Coverage disclosure belongs on every view, not just the one that loads it. */
async function loadCoverageBanner() {
  try {
    OVERVIEW = OVERVIEW || await api('/api/overview');
    const note = OVERVIEW.data_coverage.refresh_note;
    $('dataBannerText').textContent = note;
    $('footerCoverage').textContent = note;
  } catch (error) {
    const message = 'Pipeline output not found — run `python -m solaris.cli all`.';
    $('dataBannerText').textContent = message;
    $('footerCoverage').textContent = message;
  }
}

async function loadOverview() {
  const grid = $('kpiGrid');
  try {
    OVERVIEW = OVERVIEW || await api('/api/overview');
  } catch (error) {
    showError(grid, error);
    return;
  }

  const coverage = OVERVIEW.data_coverage;

  const version = OVERVIEW.model_version || {};
  $('heroMeta').innerHTML = [
    `<span class="pill">Dataset <strong>${dateOnly(coverage.first_hour_ist)} → ${dateOnly(coverage.last_hour_ist)}</strong></span>`,
    `<span class="pill"><strong>${fmt(coverage.hours)}</strong> hours · ${coverage.pct_complete}% complete</span>`,
    `<span class="pill">Test period <strong>${dateOnly(OVERVIEW.test_period.start)} → ${dateOnly(OVERVIEW.test_period.end)}</strong></span>`,
    `<span class="pill">Model <strong>${escapeHtml(version.name || 'n/a')}</strong></span>`,
  ].join('');

  grid.innerHTML = OVERVIEW.kpis.map((kpi) => {
    const decimals = kpi.unit === '%' ? 1 : 0;
    return `
      <div class="kpi">
        <div class="kpi-label">${escapeHtml(kpi.label)}</div>
        <div class="kpi-value">${fmt(kpi.value, decimals)}<span class="kpi-unit">${escapeHtml(kpi.unit)}</span></div>
        <div class="kpi-detail">${escapeHtml(kpi.detail || '')}</div>
      </div>`;
  }).join('');

  const models = await api('/api/models');
  const contribution = models.weather_contribution;
  const significance = contribution.significance || {};

  $('findingGrid').innerHTML = `
    <div class="finding">
      <h3>Weather information helps</h3>
      <div class="big">${fmt(contribution.mae_reduction_pct, 1)}%</div>
      <p>Lower mean absolute error than the identical model without weather inputs
      (${fmt(contribution.mae_mw_without_weather)} → ${fmt(contribution.mae_mw_with_weather)} MW).</p>
    </div>
    <div class="finding">
      <h3>And it is not noise</h3>
      <div class="big">p = ${significance.paired_t_pvalue ? significance.paired_t_pvalue.toExponential(1) : 'n/a'}</div>
      <p>Paired t-test on daily error across ${fmt(significance.n_days)} held-out days;
      ${fmt(significance.days_improved_pct, 0)}% of days improved.</p>
    </div>
    <div class="finding">
      <h3>It helps most when it is hot</h3>
      <div class="big">${fmt(contribution.mae_reduction_pct__hot_period, 1)}%</div>
      <p>Error reduction on research-defined hot hours, against
      ${fmt(contribution.mae_reduction_pct__non_hot_period, 1)}% on all other hours.</p>
    </div>
    <div class="finding">
      <h3>Against the obvious baseline</h3>
      <div class="big">${fmt(OVERVIEW.kpis[1].value, 1)}%</div>
      <p>Lower error than simply reusing yesterday's demand at the same hour — the
      baseline any model has to beat to be worth running.</p>
    </div>`;

  const series = await api('/api/series?hours=336');
  drawSeries('plotOverviewSeries', series, 'lgbm_calendar_history', true);
}

// --------------------------------------------------------------------------
// Forecast
// --------------------------------------------------------------------------

function drawSeries(nodeId, series, comparisonModel, showBand) {
  const times = series.timestamps;
  const columns = series.columns;
  const traces = [];

  if (showBand && columns.q10 && columns.q90) {
    traces.push({
      x: times, y: columns.q90, name: 'P90',
      line: { width: 0 }, showlegend: false, hoverinfo: 'skip',
    });
    traces.push({
      x: times, y: columns.q10, name: 'P10–P90 band',
      line: { width: 0 }, fill: 'tonexty', fillcolor: PALETTE.band,
      hoverinfo: 'skip',
    });
  }

  traces.push({
    x: times, y: columns.demand_mw, name: 'Actual demand',
    line: { color: PALETTE.actual, width: 2.6 },
    hovertemplate: '%{y:,.0f} MW<extra>Actual</extra>',
  });
  // Hours where the archived forecast is missing are left as gaps rather than
  // interpolated, so the weather model's line genuinely stops there.
  const gaps = columns.lgbm_weather.filter((v) => v === null).length;
  if (gaps > 0) {
    const note = $('coverageGapNote');
    if (note) {
      note.textContent =
        `${gaps} hour${gaps === 1 ? '' : 's'} in this window have no archived weather `
        + 'forecast, so the weather model produces no value for them. The gap is left '
        + 'open rather than filled in.';
      note.hidden = false;
    }
  }
  traces.push({
    x: times, y: columns.lgbm_weather, name: 'Weather-enhanced forecast',
    line: { color: PALETTE.weather, width: 2 },
    hovertemplate: '%{y:,.0f} MW<extra>Forecast</extra>',
  });

  if (comparisonModel && comparisonModel !== 'none' && columns[comparisonModel]) {
    const label = comparisonModel === 'naive_lag_24h'
      ? 'Naive: same hour yesterday' : 'Calendar + history model';
    traces.push({
      x: times, y: columns[comparisonModel], name: label,
      line: { color: PALETTE.baseline, width: 1.5, dash: 'dot' },
      hovertemplate: `%{y:,.0f} MW<extra>${label}</extra>`,
    });
  }

  plot(nodeId, traces, { yaxis: { ...PLOT_LAYOUT.yaxis, title: 'All-India demand (MW)' } });
}

function windowStats(series) {
  const actual = series.columns.demand_mw;
  const predicted = series.columns.lgbm_weather;
  let sum = 0;
  let count = 0;
  let peak = -Infinity;
  let peakAt = null;

  for (let i = 0; i < actual.length; i += 1) {
    if (actual[i] !== null && predicted[i] !== null) {
      sum += Math.abs(predicted[i] - actual[i]);
      count += 1;
    }
    if (actual[i] !== null && actual[i] > peak) {
      peak = actual[i];
      peakAt = series.timestamps[i];
    }
  }

  const mae = count ? sum / count : null;
  $('windowStats').innerHTML = `
    <div class="stat">
      <div class="stat-label">Hours shown</div>
      <div class="stat-value">${fmt(count)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">MAE in this window</div>
      <div class="stat-value">${fmt(mae)} <span class="kpi-unit">MW</span></div>
      <div class="stat-sub">${mae ? fmtGW(mae, 2) : ''}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Peak demand in window</div>
      <div class="stat-value">${fmtGW(peak === -Infinity ? null : peak)}</div>
      <div class="stat-sub">${peakAt ? new Date(peakAt).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : ''}</div>
    </div>`;
}

let RANGE = null;

async function loadForecast() {
  RANGE = await api('/api/series/range');
  const first = dateOnly(RANGE.first);
  const last = dateOnly(RANGE.last);

  const startInput = $('startDate');
  const endInput = $('endDate');
  [startInput, endInput].forEach((input) => { input.min = first; input.max = last; });

  // Default to the final 21 days of the test period.
  const defaultStart = new Date(RANGE.last);
  defaultStart.setDate(defaultStart.getDate() - 21);
  startInput.value = dateOnly(defaultStart.toISOString());
  endInput.value = last;

  $('bandCaveat').textContent =
    'The P10–P90 band is under-dispersed on this test period — it covers the actual '
    + 'value less often than its nominal 80%. See Performance → Uncertainty band '
    + 'calibration. Treat it as indicative, not as a calibrated risk band.';

  await refreshForecast();

  $('applyRange').addEventListener('click', refreshForecast);
  $('modelSelect').addEventListener('change', refreshForecast);
  $('showBand').addEventListener('change', refreshForecast);
  $('jumpPeak').addEventListener('click', jumpToPeak);

  try {
    const figure = await api('/api/figures/peak_scatter');
    renderSavedFigure('plotPeakScatter', figure);
  } catch (error) {
    showError($('plotPeakScatter'), error);
  }
}

async function refreshForecast() {
  const start = $('startDate').value;
  const end = $('endDate').value;
  if (start && end && start > end) {
    toast('Start date must be on or before the end date.');
    return;
  }
  const node = $('plotForecast');
  node.innerHTML = '<div class="skeleton" style="height:420px"></div>';
  try {
    const series = await api(`/api/series?start=${start}&end=${end}`);
    drawSeries('plotForecast', series, $('modelSelect').value, $('showBand').checked);
    windowStats(series);
  } catch (error) {
    showError(node, error);
  }
}

async function jumpToPeak() {
  // Find the highest-demand day across the whole test period, then centre on it.
  const all = await api('/api/series');
  const actual = all.columns.demand_mw;
  let peak = -Infinity;
  let index = 0;
  actual.forEach((value, i) => { if (value !== null && value > peak) { peak = value; index = i; } });

  const peakDate = new Date(all.timestamps[index]);
  const start = new Date(peakDate); start.setDate(start.getDate() - 5);
  const end = new Date(peakDate); end.setDate(end.getDate() + 5);
  const lastAvailable = new Date(RANGE.last);

  $('startDate').value = dateOnly(start.toISOString());
  // Clamp the window to the end of the test period; comparing Date objects
  // needs getTime(), not a bare equality check against a coerced number.
  $('endDate').value = dateOnly(
    (end.getTime() <= lastAvailable.getTime() ? end : lastAvailable).toISOString()
  );
  await refreshForecast();
  toast(`Peak demand hour: ${fmtGW(peak, 1)} on ${peakDate.toLocaleDateString('en-IN', { dateStyle: 'medium' })}`);
}

// --------------------------------------------------------------------------
// Saved Plotly figures
// --------------------------------------------------------------------------

function renderSavedFigure(nodeId, figure) {
  const node = $(nodeId);
  if (!node) return;
  node.innerHTML = '';

  // Re-theme the saved figure without flattening its structure: some saved
  // figures are subplots with xaxis2/yaxis2, so every axis present is restyled
  // individually rather than replaced by a single xaxis/yaxis pair.
  const saved = figure.layout || {};
  const layout = {
    ...saved,
    title: undefined,
    paper_bgcolor: PLOT_LAYOUT.paper_bgcolor,
    plot_bgcolor: PLOT_LAYOUT.plot_bgcolor,
    font: PLOT_LAYOUT.font,
    legend: { ...(saved.legend || {}), ...PLOT_LAYOUT.legend },
    margin: saved.margin && saved.margin.l > 90 ? saved.margin : PLOT_LAYOUT.margin,
  };
  Object.keys(saved).forEach((key) => {
    if (/^[xy]axis\d*$/.test(key)) {
      layout[key] = {
        ...saved[key],
        gridcolor: PLOT_LAYOUT.xaxis.gridcolor,
        zeroline: false,
      };
    }
  });

  Plotly.newPlot(node, figure.data, layout, PLOT_CONFIG);
}

async function showSavedFigure(nodeId, name) {
  try {
    renderSavedFigure(nodeId, await api(`/api/figures/${name}`));
  } catch (error) {
    showError($(nodeId), error);
  }
}

// --------------------------------------------------------------------------
// Heat and demand
// --------------------------------------------------------------------------

async function loadHeat() {
  await showSavedFigure('plotTempDemand', 'temperature_vs_demand');

  const [methods, models] = await Promise.all([
    api('/api/methodology'), api('/api/models'),
  ]);

  const hot = methods.hot_period || {};
  $('hotDefinition').textContent = hot.definition || '';

  const contribution = models.weather_contribution;
  const byModel = {};
  models.models.forEach((row) => { byModel[row.model] = row; });

  plot('plotHotSplit', [
    {
      type: 'bar', name: 'Without weather',
      x: ['Hot hours', 'All other hours'],
      y: [byModel.lgbm_calendar_history?.mae_hot_mw, byModel.lgbm_calendar_history?.mae_mw],
      marker: { color: PALETTE.baseline },
      text: [fmt(byModel.lgbm_calendar_history?.mae_hot_mw), fmt(byModel.lgbm_calendar_history?.mae_mw)],
      textposition: 'outside',
    },
    {
      type: 'bar', name: 'With weather',
      x: ['Hot hours', 'All other hours'],
      y: [byModel.lgbm_weather?.mae_hot_mw, byModel.lgbm_weather?.mae_mw],
      marker: { color: PALETTE.weather },
      text: [fmt(byModel.lgbm_weather?.mae_hot_mw), fmt(byModel.lgbm_weather?.mae_mw)],
      textposition: 'outside',
    },
  ], {
    barmode: 'group',
    yaxis: { ...PLOT_LAYOUT.yaxis, title: 'MAE (MW)' },
    hovermode: 'closest',
  });

  // Sensitivity table across hot thresholds.
  const sensitivity = methods.hot_threshold_sensitivity || {};
  const rows = Object.entries(sensitivity).map(([name, entry]) => {
    if (!entry.sufficient) {
      return `<tr><td>${escapeHtml(name)}</td><td>${fmt(entry.threshold_c, 1)}</td>
        <td>${fmt(entry.test_hours_hot)}</td>
        <td colspan="3" class="muted">sample too small to report</td></tr>`;
    }
    const reduction = (1 - entry.lgbm_weather / entry.lgbm_calendar_history) * 100;
    return `<tr>
      <td>${escapeHtml(name)}</td>
      <td>${fmt(entry.threshold_c, 1)}</td>
      <td>${fmt(entry.test_hours_hot)}</td>
      <td>${fmt(entry.lgbm_calendar_history)}</td>
      <td>${fmt(entry.lgbm_weather)}</td>
      <td><strong>${fmt(reduction, 1)}%</strong></td>
    </tr>`;
  }).join('');

  $('hotSensitivityTable').innerHTML = `
    <caption>Sensitivity: the same comparison at four hot-period thresholds. The weather
    model wins at every threshold, so the finding does not depend on where the line is drawn.</caption>
    <thead><tr>
      <th>Percentile</th><th>Threshold (°C)</th><th>Hot hours</th>
      <th>MAE without weather (MW)</th><th>MAE with weather (MW)</th><th>Reduction</th>
    </tr></thead>
    <tbody>${rows}</tbody>`;
}

// --------------------------------------------------------------------------
// Evening stress
// --------------------------------------------------------------------------

async function loadEvening() {
  const [profile, stress] = await Promise.all([
    api('/api/daily-profile'), api('/api/evening-stress'),
  ]);

  const full = stress.full_period || {};
  $('residualDefinition').textContent = full.definition || 'Definition unavailable.';

  const hours = profile.hours;
  const columns = profile.columns;
  plot('plotDailyProfile', [
    {
      x: hours, y: columns.solar_mw, name: 'Solar', fill: 'tozeroy',
      line: { color: PALETTE.solar, width: 2 },
      fillcolor: 'rgba(251,191,36,0.2)',
    },
    { x: hours, y: columns.wind_mw, name: 'Wind', line: { color: PALETTE.wind, width: 2 } },
    { x: hours, y: columns.demand_mw, name: 'Demand', line: { color: PALETTE.actual, width: 2.6 } },
    {
      x: hours, y: columns.residual_load_mw, name: 'Residual load',
      line: { color: PALETTE.residual, width: 2.6 },
    },
  ], {
    xaxis: { ...PLOT_LAYOUT.xaxis, title: 'Hour of day (IST)', dtick: 2 },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: 'MW' },
  });

  $('rampStats').innerHTML = `
    <div class="stat">
      <div class="stat-label">Mean steepest hourly ramp</div>
      <div class="stat-value">${fmtGW(full.mean_max_hourly_ramp_mw, 1)}<span class="kpi-unit">/h</span></div>
      <div class="stat-sub">Averaged over ${fmt(full.n_days)} full days</div>
    </div>
    <div class="stat">
      <div class="stat-label">95th percentile ramp</div>
      <div class="stat-value">${fmtGW(full.p95_max_hourly_ramp_mw, 1)}<span class="kpi-unit">/h</span></div>
      <div class="stat-sub">1 day in 20 is at least this steep</div>
    </div>
    <div class="stat">
      <div class="stat-label">Steepest 3-hour ramp (mean)</div>
      <div class="stat-value">${fmtGW(full.mean_max_3h_ramp_mw, 1)}</div>
      <div class="stat-sub">Sustained evening pick-up</div>
    </div>
    <div class="stat">
      <div class="stat-label">Most common ramp hour</div>
      <div class="stat-value">${String(full.modal_ramp_hour_ist ?? '--').padStart(2, '0')}:00</div>
      <div class="stat-sub">IST; evening residual peak at ${String(full.modal_evening_peak_hour_ist ?? '--').padStart(2, '0')}:00</div>
    </div>`;

  await showSavedFigure('plotEveningRamp', 'evening_ramp');
}

// --------------------------------------------------------------------------
// Performance
// --------------------------------------------------------------------------

async function loadPerformance() {
  const [models, methods] = await Promise.all([
    api('/api/models'), api('/api/methodology'),
  ]);
  const split = methods.split.test;

  $('perfLede').textContent =
    `Every model below was scored on the same held-out block: ${dateOnly(split.start)} `
    + `to ${dateOnly(split.end)}, ${fmt(split.hours)} hours that no model saw during `
    + `fitting, early stopping or threshold selection.`;

  const best = models.models[0];
  $('modelTable').innerHTML = `
    <thead><tr>
      <th>Model</th><th>MAE (MW)</th><th>RMSE (MW)</th><th>MAPE %</th>
      <th>Bias (MW)</th><th>vs naive</th><th>Hot MAE</th><th>Peak MAE</th><th>Peak hour ±h</th>
    </tr></thead>
    <tbody>${models.models.map((row) => `
      <tr class="${row.model === best.model ? 'is-best' : ''}">
        <td>${escapeHtml(row.model)}<span class="family-tag">${escapeHtml(row.family)}</span></td>
        <td>${fmt(row.mae_mw)}</td>
        <td>${fmt(row.rmse_mw)}</td>
        <td>${fmt(row.mape_pct, 2)}</td>
        <td>${fmt(row.bias_mw)}</td>
        <td>${row.skill_vs_naive_pct === null ? 'n/a' : `${fmt(row.skill_vs_naive_pct, 1)}%`}</td>
        <td>${row.mae_hot_mw === null ? '<span class="muted">n/a</span>' : fmt(row.mae_hot_mw)}</td>
        <td>${fmt(row.peak_mae_mw)}</td>
        <td>${fmt(row.peak_hour_mae_h, 2)}</td>
      </tr>`).join('')}</tbody>`;

  await Promise.all([
    showSavedFigure('plotErrorByHour', 'error_by_hour'),
    showSavedFigure('plotResiduals', 'residuals'),
    showSavedFigure('plotCalibration', 'quantile_calibration'),
  ]);

  const extrapolation = models.extrapolation || {};
  $('extrapolationBody').innerHTML = `
    <p class="prose">Two of the models in the table — the ones ending <code>_level</code> —
    are <em>worse</em> than simply reusing yesterday's demand. That is reported rather than
    dropped, because the reason is instructive.</p>
    <p class="prose">A gradient-boosted tree predicts an average of values it saw in training,
    so it can never output a number above its training maximum. All-India demand grew
    through the period: the training block topped out at
    <strong>${fmtGW(extrapolation.train_max_demand_mw)}</strong>, but the test block reaches
    <strong>${fmtGW(extrapolation.test_max_demand_mw)}</strong>, with
    <strong>${fmt(extrapolation.test_hours_above_train_max)}</strong> test hours above
    anything the model had ever seen.</p>
    <p class="prose">The level model duly saturates — its highest prediction across the whole
    test period is ${fmtGW(extrapolation.level_model_max_prediction_mw)}. The models used for
    the headline results predict the <em>change</em> from the same hour yesterday and add that
    anchor back, which lifts the ceiling: their highest prediction is
    ${fmtGW(extrapolation.delta_model_max_prediction_mw)}.</p>`;

  const quantile = methods.quantile || {};
  const interval = quantile.interval_p10_p90 || {};
  const validation = quantile.validation_calibration?.interval_p10_p90 || {};
  const volatility = quantile.target_volatility || {};
  $('calibrationNote').innerHTML = `
    <div class="callout callout-warn" style="margin-top:16px">
      <strong>The uncertainty band is not well calibrated, and should not be used as one.</strong>
      <span>The P10–P90 interval covers the actual value
      ${interval.empirical_coverage ? `${(interval.empirical_coverage * 100).toFixed(1)}%` : 'n/a'}
      of the time on the test period, against a nominal 80%. On the validation block it
      covers ${validation.empirical_coverage ? `${(validation.empirical_coverage * 100).toFixed(1)}%` : 'n/a'},
      so the band is roughly right where it was tuned and too narrow out of sample. The
      validation block (January–February) has a mean daily demand range of
      ${fmtGW(volatility.valid?.daily_range_mean_mw)} against
      ${fmtGW(volatility.test?.daily_range_mean_mw)} on test, which is the kind of
      distribution shift that produces exactly this failure. Reported as measured; fixing it
      would need recalibration on a period that spans a hot season.</span>
    </div>`;
}

// --------------------------------------------------------------------------
// Explainability
// --------------------------------------------------------------------------

async function loadExplain() {
  const explain = await api('/api/explainability');
  $('explainCaveat').textContent = explain.caveat;

  const entries = Object.entries(explain.gain_importance).slice(0, 18).reverse();
  plot('plotImportance', [{
    type: 'bar', orientation: 'h',
    x: entries.map(([, value]) => value),
    y: entries.map(([name]) => name),
    marker: {
      color: entries.map(([name]) =>
        (name.includes('temp') || name.includes('cdd') || name.includes('hdd') || name.includes('heat'))
          ? PALETTE.weather : PALETTE.baseline),
    },
    hovertemplate: '%{y}: %{x:.3f}<extra></extra>',
  }], {
    height: 520,
    margin: { l: 215, r: 24, t: 10, b: 44 },
    xaxis: { ...PLOT_LAYOUT.xaxis, title: 'Share of total split gain' },
    yaxis: { ...PLOT_LAYOUT.yaxis, automargin: true },
    hovermode: 'closest',
  });
}

// --------------------------------------------------------------------------
// Methodology
// --------------------------------------------------------------------------

async function loadMethods() {
  const methods = await api('/api/methodology');

  $('methodConvention').textContent = methods.forecast_convention;

  $('splitTable').innerHTML = ['train', 'valid', 'test'].map((part) => {
    const block = methods.split[part];
    return `<div class="split-row ${part}">
      <b>${part}</b>
      <span class="range">${dateOnly(block.start)} → ${dateOnly(block.end)}</span>
      <span class="hours">${fmt(block.hours)} h</span>
    </div>`;
  }).join('');

  $('sourceList').innerHTML = methods.sources.map((source) => `
    <div class="source">
      <h3>${escapeHtml(source.name)}</h3>
      <div class="meta">${escapeHtml(source.via)} · ${escapeHtml(source.licence)}</div>
      <div class="note">${escapeHtml(source.note)}</div>
      <div class="note"><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.url)}</a></div>
    </div>`).join('');

  const weighting = methods.weighting_sensitivity || {};
  $('weightingTable').innerHTML = `
    <caption>Weather aggregation weighting — the weather model wins under all three schemes,
    so the conclusion does not rest on the choice of city weights.</caption>
    <thead><tr><th>Scheme</th><th>MAE (MW)</th><th>Reduction vs no weather</th></tr></thead>
    <tbody>${Object.entries(weighting).map(([name, entry]) => `
      <tr><td>${escapeHtml(name)}</td><td>${fmt(entry.mae_mw)}</td>
      <td>${fmt(entry.mae_reduction_vs_no_weather_pct, 2)}%</td></tr>`).join('')}</tbody>`;

  const rolling = methods.rolling_backtest || {};
  $('rollingTable').innerHTML = `
    <caption>Rolling-origin backtest: the model is refitted every month on all earlier data
    and scored on the month it has never seen. Mean MAE across folds.</caption>
    <thead><tr><th>Model</th><th>Mean fold MAE (MW)</th></tr></thead>
    <tbody>${Object.entries(rolling).map(([name, value]) => `
      <tr><td>${escapeHtml(name)}</td><td>${fmt(value)}</td></tr>`).join('')}</tbody>`;

  $('limitationList').innerHTML = methods.limitations
    .map((item) => `<li>${escapeHtml(item)}</li>`).join('');
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

const VIEW_LOADERS = {
  overview: loadOverview,
  forecast: loadForecast,
  heat: loadHeat,
  evening: loadEvening,
  performance: loadPerformance,
  explain: loadExplain,
  methods: loadMethods,
};

function viewFromHash() {
  const name = (location.hash || '#overview').slice(1);
  return VIEW_LOADERS[name] ? name : 'overview';
}

// Deep links, bookmarks and the browser's back/forward buttons all change the
// hash without reloading, so the view has to follow it.
window.addEventListener('hashchange', () => {
  const name = viewFromHash();
  const current = document.querySelector('.view.is-active');
  if (current && current.id === `view-${name}`) return;
  showView(name);
});

loadCoverageBanner();
showView(viewFromHash());
