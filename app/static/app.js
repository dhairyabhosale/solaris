/* Solaris Grid Intelligence, frontend.
 *
 * Every figure is drawn from the API, which reads the research pipeline's own
 * output files. There are no hard-coded metrics: if an artefact is missing the
 * view says which command produces it rather than showing a placeholder.
 */

'use strict';

const PALETTE = {
  actual:   '#e9eefb',
  weather:  '#ffb020',
  baseline: '#22d3ee',
  naive:    '#6d7f9f',
  band:     'rgba(255, 176, 32, 0.14)',
  solar:    '#ffb020',
  wind:     '#34e4a8',
  residual: '#a78bfa',
  heat:     '#ff5a5f',
  good:     '#34e4a8',
  grid:     'rgba(140, 170, 225, 0.12)',
  text:     '#9aabcc',
};

const REDUCED_MOTION =
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const PLOT_LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { family: 'Inter, system-ui, sans-serif', size: 12, color: PALETTE.text },
  margin: { l: 66, r: 20, t: 16, b: 50 },
  hovermode: 'x unified',
  hoverlabel: {
    bgcolor: '#111a2e',
    bordercolor: 'rgba(140,170,225,.26)',
    font: { color: '#e9eefb', family: 'Inter, system-ui, sans-serif' },
  },
  xaxis: { gridcolor: PALETTE.grid, zeroline: false, linecolor: PALETTE.grid },
  yaxis: { gridcolor: PALETTE.grid, zeroline: false, linecolor: PALETTE.grid },
  legend: { orientation: 'h', y: -0.2, x: 0, font: { size: 11 } },
  transition: { duration: REDUCED_MOTION ? 0 : 450, easing: 'cubic-in-out' },
};

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

/* A categorical chart has nothing meaningful to pan or zoom to. Leaving the
 * axes draggable let a stray cursor drag scroll a bar chart off into empty
 * space, which is what produced a blank chart showing the year 2000. Locking
 * both axes removes the failure mode entirely. */
const LOCKED_AXES = {
  xaxis: { ...PLOT_LAYOUT.xaxis, fixedrange: true },
  yaxis: { ...PLOT_LAYOUT.yaxis, fixedrange: true },
};
const STATIC_CONFIG = {
  ...PLOT_CONFIG,
  scrollZoom: false,
  doubleClick: false,
  dragmode: false,
};

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

const dateOnly = (iso) => (iso ? String(iso).slice(0, 10) : '');

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = String(text ?? '');
  return div.innerHTML;
}

function showError(container, error) {
  container.innerHTML = `
    <div class="empty-state">
      <div>${escapeHtml(error.message)}</div>
      ${error.command ? `<code>${escapeHtml(error.command)}</code>` : ''}
    </div>`;
}

function toast(message) {
  const element = $('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { element.hidden = true; }, 5200);
}

function plot(id, traces, layoutOverrides = {}, config = PLOT_CONFIG) {
  const node = $(id);
  if (!node) return null;
  node.innerHTML = '';
  return Plotly.newPlot(node, traces, { ...PLOT_LAYOUT, ...layoutOverrides }, config);
}

// --------------------------------------------------------------------------
// Animation helpers
// --------------------------------------------------------------------------

/** Count a number up from zero, so KPIs land rather than just appear. */
function countUp(element, target, decimals, suffix = '') {
  if (REDUCED_MOTION || target === null || Number.isNaN(target)) {
    element.textContent = fmt(target, decimals) + suffix;
    return;
  }
  const duration = 1100;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / duration, 1);
    // easeOutExpo, so the number decelerates into place.
    const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
    element.textContent = fmt(target * eased, decimals) + suffix;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Draw a line chart in from left to right. */
function animateLines(nodeId, traces, layout, config = PLOT_CONFIG) {
  const node = $(nodeId);
  if (!node) return;
  if (REDUCED_MOTION) {
    plot(nodeId, traces, layout, config);
    return;
  }
  // Start with one point per trace, then extend to the full series.
  const seed = traces.map((t) => ({
    ...t,
    x: t.x.slice(0, 1),
    y: t.y.slice(0, 1),
  }));
  node.innerHTML = '';
  Plotly.newPlot(node, seed, { ...PLOT_LAYOUT, ...layout }, config).then(() => {
    const total = traces[0]?.x.length || 0;
    if (!total) return;
    const duration = 850;
    const start = performance.now();
    const step = (now) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const upto = Math.max(1, Math.round(total * eased));
      Plotly.restyle(node, {
        x: traces.map((tr) => tr.x.slice(0, upto)),
        y: traces.map((tr) => tr.y.slice(0, upto)),
      });
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

/** Grow bars up from zero. */
function animateBars(nodeId, traces, layout, config = STATIC_CONFIG) {
  const node = $(nodeId);
  if (!node) return;
  if (REDUCED_MOTION) {
    plot(nodeId, traces, layout, config);
    return;
  }
  const zeroed = traces.map((t) => ({ ...t, y: t.y.map(() => 0) }));
  node.innerHTML = '';
  Plotly.newPlot(node, zeroed, { ...PLOT_LAYOUT, ...layout }, config).then(() => {
    Plotly.animate(node, {
      data: traces.map((t) => ({ y: t.y })),
      traces: traces.map((_, i) => i),
    }, {
      transition: { duration: 750, easing: 'cubic-out' },
      frame: { duration: 750, redraw: false },
    });
  });
}

/** Grow horizontal bars out from zero. */
function animateBarsH(nodeId, traces, layout, config = STATIC_CONFIG) {
  const node = $(nodeId);
  if (!node) return;
  if (REDUCED_MOTION) {
    plot(nodeId, traces, layout, config);
    return;
  }
  const zeroed = traces.map((t) => ({ ...t, x: t.x.map(() => 0) }));
  node.innerHTML = '';
  Plotly.newPlot(node, zeroed, { ...PLOT_LAYOUT, ...layout }, config).then(() => {
    Plotly.animate(node, {
      data: traces.map((t) => ({ x: t.x })),
      traces: traces.map((_, i) => i),
    }, {
      transition: { duration: 750, easing: 'cubic-out' },
      frame: { duration: 750, redraw: false },
    });
  });
}

/** Reveal elements as they scroll into view. */
const revealObserver = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => {
      entries.forEach((entry, i) => {
        if (!entry.isIntersecting) return;
        setTimeout(() => entry.target.classList.add('is-visible'), i * 70);
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' })
  : null;

function observeReveals(root = document) {
  root.querySelectorAll('.reveal:not(.is-visible)').forEach((el) => {
    if (!revealObserver) {
      el.classList.add('is-visible');
      return;
    }
    // Anything already on screen is shown at once rather than waiting for an
    // intersection event that may never fire.
    const box = el.getBoundingClientRect();
    if (box.top < window.innerHeight && box.bottom > 0) {
      el.classList.add('is-visible');
      return;
    }
    revealObserver.observe(el);
  });
}

/* Safety net. Content must never be stuck invisible because an observer did
 * not fire: in a short page, a restored scroll position, or a headless
 * capture, an element can sit outside every intersection event. After a short
 * grace period anything still hidden is simply shown. */
function revealFallback() {
  setTimeout(() => {
    document.querySelectorAll('.reveal:not(.is-visible)').forEach((el) => {
      const box = el.getBoundingClientRect();
      if (box.top < window.innerHeight * 1.5) el.classList.add('is-visible');
    });
  }, 1200);
}

// --------------------------------------------------------------------------
// Chrome: scroll progress, sticky bar
// --------------------------------------------------------------------------

function onScroll() {
  const doc = document.documentElement;
  const max = doc.scrollHeight - doc.clientHeight;
  const pct = max > 0 ? (doc.scrollTop / max) * 100 : 0;
  $('scrollProgress').style.width = `${pct}%`;
  $('topbar').classList.toggle('is-scrolled', doc.scrollTop > 8);
}
window.addEventListener('scroll', onScroll, { passive: true });

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
  window.scrollTo({ top: 0, behavior: REDUCED_MOTION ? 'auto' : 'smooth' });

  if (!loaded.has(name)) {
    loaded.add(name);
    const loader = VIEW_LOADERS[name];
    if (loader) {
      loader()
        .then(() => { observeReveals(); revealFallback(); })
        .catch((error) => { console.error(error); toast(error.message); });
    }
  } else {
    // Plotly needs a nudge after a hidden container becomes visible again.
    document.querySelectorAll(`#view-${name} .plot`).forEach((node) => {
      if (node.data) Plotly.Plots.resize(node);
    });
  }
  observeReveals();
  revealFallback();
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
document.querySelector('.brand').addEventListener('click', (event) => {
  event.preventDefault();
  showView('overview');
});

// --------------------------------------------------------------------------
// Overview
// --------------------------------------------------------------------------

let OVERVIEW = null;

async function loadCoverageBanner() {
  // The coverage disclosure lives in the footer only. The header used to carry
  // a duplicate of the same sentence.
  try {
    OVERVIEW = OVERVIEW || await api('/api/overview');
    $('footerCoverage').textContent = OVERVIEW.data_coverage.refresh_note;
  } catch (error) {
    $('footerCoverage').textContent =
      'Pipeline output not found. Run: python -m solaris.cli all';
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
    `<span class="pill">Dataset <strong>${dateOnly(coverage.first_hour_ist)} to ${dateOnly(coverage.last_hour_ist)}</strong></span>`,
    `<span class="pill"><strong>${fmt(coverage.hours)}</strong> hours, ${coverage.pct_complete}% complete</span>`,
    `<span class="pill">Tested on <strong>${dateOnly(OVERVIEW.test_period.start)} to ${dateOnly(OVERVIEW.test_period.end)}</strong></span>`,
    `<span class="pill">Model <strong>${escapeHtml(version.name || 'n/a')}</strong></span>`,
  ].join('');

  grid.innerHTML = OVERVIEW.kpis.map((kpi) => `
      <div class="kpi">
        <div class="kpi-label">${escapeHtml(kpi.label)}</div>
        <div class="kpi-value" data-target="${kpi.value}" data-unit="${escapeHtml(kpi.unit)}">0</div>
        <div class="kpi-detail">${escapeHtml(kpi.detail || '')}</div>
      </div>`).join('');

  grid.querySelectorAll('.kpi-value').forEach((element, i) => {
    const target = parseFloat(element.dataset.target);
    const unit = element.dataset.unit;
    const decimals = unit === '%' ? 1 : 0;
    element.innerHTML = `<span class="num">0</span><span class="kpi-unit">${unit}</span>`;
    setTimeout(() => countUp(element.querySelector('.num'), target, decimals), 180 + i * 110);
  });

  const models = await api('/api/models');
  const contribution = models.weather_contribution;
  const significance = contribution.significance || {};

  $('findingGrid').innerHTML = `
    <div class="finding">
      <h3>Weather information helps</h3>
      <div class="big">${fmt(contribution.mae_reduction_pct, 1)}%</div>
      <p>Lower average error than the identical model without weather inputs
      (${fmt(contribution.mae_mw_without_weather)} to ${fmt(contribution.mae_mw_with_weather)} MW).</p>
    </div>
    <div class="finding">
      <h3>And it is not chance</h3>
      <div class="big">p = ${significance.paired_t_pvalue ? significance.paired_t_pvalue.toExponential(1) : 'n/a'}</div>
      <p>Paired test on daily error across ${fmt(significance.n_days)} held-out days.
      ${fmt(significance.days_improved_pct, 0)}% of days improved.</p>
    </div>
    <div class="finding">
      <h3>It helps most when it is hot</h3>
      <div class="big">${fmt(contribution.mae_reduction_pct__hot_period, 1)}%</div>
      <p>Error cut on research-defined hot hours, against
      ${fmt(contribution.mae_reduction_pct__non_hot_period, 1)}% on every other hour.</p>
    </div>
    <div class="finding">
      <h3>Against the obvious baseline</h3>
      <div class="big">${fmt(OVERVIEW.kpis[1].value, 1)}%</div>
      <p>Lower error than simply reusing yesterday's demand at the same hour, the
      baseline any model has to beat to be worth running.</p>
    </div>`;

  const series = await api('/api/series?hours=336');
  drawSeries('plotOverviewSeries', series, 'lgbm_calendar_history', true, true);
}

// --------------------------------------------------------------------------
// Forecast
// --------------------------------------------------------------------------

function drawSeries(nodeId, series, comparisonModel, showBand, animate) {
  const times = series.timestamps;
  const columns = series.columns;
  const traces = [];

  if (showBand && columns.q10 && columns.q90) {
    traces.push({
      x: times, y: columns.q90, name: 'P90',
      line: { width: 0 }, showlegend: false, hoverinfo: 'skip',
    });
    traces.push({
      x: times, y: columns.q10, name: 'P10 to P90 range',
      line: { width: 0 }, fill: 'tonexty', fillcolor: PALETTE.band,
      hoverinfo: 'skip',
    });
  }

  traces.push({
    x: times, y: columns.demand_mw, name: 'Actual demand',
    line: { color: PALETTE.actual, width: 2.4, shape: 'spline', smoothing: 0.5 },
    hovertemplate: '%{y:,.0f} MW<extra>Actual</extra>',
  });
  traces.push({
    x: times, y: columns.lgbm_weather, name: 'Weather-enhanced forecast',
    line: { color: PALETTE.weather, width: 2.2, shape: 'spline', smoothing: 0.5 },
    hovertemplate: '%{y:,.0f} MW<extra>Forecast</extra>',
  });

  if (comparisonModel && comparisonModel !== 'none' && columns[comparisonModel]) {
    const label = comparisonModel === 'naive_lag_24h'
      ? 'Naive: same hour yesterday' : 'Model without weather';
    traces.push({
      x: times, y: columns[comparisonModel], name: label,
      line: { color: PALETTE.baseline, width: 1.5, dash: 'dot' },
      hovertemplate: `%{y:,.0f} MW<extra>${label}</extra>`,
    });
  }

  const layout = { yaxis: { ...PLOT_LAYOUT.yaxis, title: 'All-India demand (MW)' } };
  if (animate) animateLines(nodeId, traces, layout);
  else plot(nodeId, traces, layout);

  const gaps = (columns.lgbm_weather || []).filter((v) => v === null).length;
  const note = $('coverageGapNote');
  if (note) {
    if (gaps > 0) {
      note.textContent =
        `${gaps} hour${gaps === 1 ? '' : 's'} in this window have no archived weather `
        + 'forecast, so the weather model produces no value for them. The gap is left '
        + 'open rather than filled in.';
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }
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
      <div class="stat-label">Hours with a forecast</div>
      <div class="stat-value">${fmt(count)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Average error here</div>
      <div class="stat-value">${fmt(mae)}<span class="kpi-unit">MW</span></div>
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

  const defaultStart = new Date(RANGE.last);
  defaultStart.setDate(defaultStart.getDate() - 21);
  startInput.value = dateOnly(defaultStart.toISOString());
  endInput.value = last;

  $('bandCaveat').textContent =
    'The shaded range is under-dispersed on this test period: it covers the real value '
    + 'less often than its nominal 80%. See Performance, uncertainty band calibration. '
    + 'Treat it as indicative, not as a calibrated risk band.';

  await refreshForecast(true);

  $('applyRange').addEventListener('click', () => refreshForecast(false));
  $('modelSelect').addEventListener('change', () => refreshForecast(false));
  $('showBand').addEventListener('change', () => refreshForecast(false));
  $('jumpPeak').addEventListener('click', jumpToPeak);

  try {
    renderSavedFigure('plotPeakScatter', await api('/api/figures/peak_scatter'));
  } catch (error) {
    showError($('plotPeakScatter'), error);
  }
}

async function refreshForecast(animate) {
  const start = $('startDate').value;
  const end = $('endDate').value;
  if (start && end && start > end) {
    toast('The start date must be on or before the end date.');
    return;
  }
  const node = $('plotForecast');
  node.innerHTML = '<div class="skeleton" style="height:440px"></div>';
  try {
    const series = await api(`/api/series?start=${start}&end=${end}`);
    drawSeries('plotForecast', series, $('modelSelect').value, $('showBand').checked, animate);
    windowStats(series);
  } catch (error) {
    showError(node, error);
  }
}

async function jumpToPeak() {
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
  $('endDate').value = dateOnly(
    (end.getTime() <= lastAvailable.getTime() ? end : lastAvailable).toISOString()
  );
  await refreshForecast(true);
  toast(`Record hour: ${fmtGW(peak, 1)} on ${peakDate.toLocaleDateString('en-IN', { dateStyle: 'medium' })}`);
}

// --------------------------------------------------------------------------
// Saved Plotly figures
// --------------------------------------------------------------------------

function renderSavedFigure(nodeId, figure, lockAxes = true) {
  const node = $(nodeId);
  if (!node) return;
  node.innerHTML = '';

  // Re-theme without flattening structure: some saved figures are subplots
  // with xaxis2/yaxis2, so each axis present is restyled individually.
  const saved = figure.layout || {};
  const layout = {
    ...saved,
    title: undefined,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: PLOT_LAYOUT.font,
    hoverlabel: PLOT_LAYOUT.hoverlabel,
    legend: { ...(saved.legend || {}), ...PLOT_LAYOUT.legend },
    margin: saved.margin && saved.margin.l > 90 ? saved.margin : PLOT_LAYOUT.margin,
  };
  Object.keys(saved).forEach((key) => {
    if (/^[xy]axis\d*$/.test(key)) {
      layout[key] = {
        ...saved[key],
        gridcolor: PALETTE.grid,
        linecolor: PALETTE.grid,
        zeroline: false,
        fixedrange: lockAxes,
      };
    }
  });

  Plotly.newPlot(node, figure.data, layout, lockAxes ? STATIC_CONFIG : PLOT_CONFIG);
}

async function showSavedFigure(nodeId, name, lockAxes = true) {
  try {
    renderSavedFigure(nodeId, await api(`/api/figures/${name}`), lockAxes);
  } catch (error) {
    showError($(nodeId), error);
  }
}

// --------------------------------------------------------------------------
// Heat and demand
// --------------------------------------------------------------------------

async function loadHeat() {
  // The scatter keeps zoom: exploring 800 points benefits from it.
  await showSavedFigure('plotTempDemand', 'temperature_vs_demand', false);

  const [methods, models] = await Promise.all([
    api('/api/methodology'), api('/api/models'),
  ]);

  $('hotDefinition').textContent = (methods.hot_period || {}).definition || '';

  const byModel = {};
  models.models.forEach((row) => { byModel[row.model] = row; });

  const traces = [
    {
      type: 'bar', name: 'Without weather',
      x: ['Hot hours', 'All other hours'],
      y: [byModel.lgbm_calendar_history?.mae_hot_mw, byModel.lgbm_calendar_history?.mae_mw],
      marker: { color: PALETTE.baseline, line: { width: 0 } },
      text: [fmt(byModel.lgbm_calendar_history?.mae_hot_mw), fmt(byModel.lgbm_calendar_history?.mae_mw)],
      textposition: 'outside',
      textfont: { color: PALETTE.text },
      hovertemplate: '%{x}<br>%{y:,.0f} MW<extra>Without weather</extra>',
    },
    {
      type: 'bar', name: 'With weather',
      x: ['Hot hours', 'All other hours'],
      y: [byModel.lgbm_weather?.mae_hot_mw, byModel.lgbm_weather?.mae_mw],
      marker: { color: PALETTE.weather, line: { width: 0 } },
      text: [fmt(byModel.lgbm_weather?.mae_hot_mw), fmt(byModel.lgbm_weather?.mae_mw)],
      textposition: 'outside',
      textfont: { color: PALETTE.text },
      hovertemplate: '%{x}<br>%{y:,.0f} MW<extra>With weather</extra>',
    },
  ];

  const maxY = Math.max(...traces.flatMap((t) => t.y.filter((v) => v != null)));
  animateBars('plotHotSplit', traces, {
    barmode: 'group',
    bargap: 0.38,
    // An explicit category axis with a fixed range: these are two labels, not
    // a continuum, and nothing here should be pannable.
    xaxis: { ...LOCKED_AXES.xaxis, type: 'category' },
    yaxis: {
      ...LOCKED_AXES.yaxis,
      title: 'Average error (MW)',
      range: [0, maxY * 1.18],
      autorange: false,
    },
    hovermode: 'closest',
  });

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
    <caption>The same comparison at four different definitions of "hot". The weather
    model wins at every threshold, so the finding does not depend on where the line
    is drawn.</caption>
    <thead><tr>
      <th>Percentile</th><th>Threshold (C)</th><th>Hot hours</th>
      <th>Error without weather (MW)</th><th>Error with weather (MW)</th><th>Reduction</th>
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
  animateLines('plotDailyProfile', [
    {
      x: hours, y: columns.solar_mw, name: 'Solar', fill: 'tozeroy',
      line: { color: PALETTE.solar, width: 2, shape: 'spline' },
      fillcolor: 'rgba(255,176,32,0.16)',
      hovertemplate: '%{y:,.0f} MW<extra>Solar</extra>',
    },
    {
      x: hours, y: columns.wind_mw, name: 'Wind',
      line: { color: PALETTE.wind, width: 2, shape: 'spline' },
      hovertemplate: '%{y:,.0f} MW<extra>Wind</extra>',
    },
    {
      x: hours, y: columns.demand_mw, name: 'Demand',
      line: { color: PALETTE.actual, width: 2.6, shape: 'spline' },
      hovertemplate: '%{y:,.0f} MW<extra>Demand</extra>',
    },
    {
      x: hours, y: columns.residual_load_mw, name: 'Residual load',
      line: { color: PALETTE.residual, width: 2.6, shape: 'spline' },
      hovertemplate: '%{y:,.0f} MW<extra>Residual load</extra>',
    },
  ], {
    xaxis: { ...LOCKED_AXES.xaxis, title: 'Hour of day (IST)', dtick: 2 },
    yaxis: { ...LOCKED_AXES.yaxis, title: 'MW' },
  }, STATIC_CONFIG);

  $('rampStats').innerHTML = `
    <div class="stat">
      <div class="stat-label">Mean steepest hourly ramp</div>
      <div class="stat-value">${fmtGW(full.mean_max_hourly_ramp_mw, 1)}<span class="kpi-unit">/h</span></div>
      <div class="stat-sub">Averaged over ${fmt(full.n_days)} complete days</div>
    </div>
    <div class="stat">
      <div class="stat-label">95th percentile ramp</div>
      <div class="stat-value">${fmtGW(full.p95_max_hourly_ramp_mw, 1)}<span class="kpi-unit">/h</span></div>
      <div class="stat-sub">One day in twenty is at least this steep</div>
    </div>
    <div class="stat">
      <div class="stat-label">Steepest 3-hour ramp</div>
      <div class="stat-value">${fmtGW(full.mean_max_3h_ramp_mw, 1)}</div>
      <div class="stat-sub">Sustained evening pick-up, on average</div>
    </div>
    <div class="stat">
      <div class="stat-label">Most common ramp hour</div>
      <div class="stat-value">${String(full.modal_ramp_hour_ist ?? '--').padStart(2, '0')}:00</div>
      <div class="stat-sub">IST. Evening residual peak at ${String(full.modal_evening_peak_hour_ist ?? '--').padStart(2, '0')}:00</div>
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
    + 'fitting, early stopping or threshold selection.';

  const best = models.models[0];
  $('modelTable').innerHTML = `
    <thead><tr>
      <th>Model</th><th>Error (MW)</th><th>RMSE (MW)</th><th>Error %</th>
      <th>Bias (MW)</th><th>vs naive</th><th>Hot hours</th><th>Peak error</th><th>Peak hour</th>
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
        <td>${fmt(row.peak_hour_mae_h, 2)} h</td>
      </tr>`).join('')}</tbody>`;

  await Promise.all([
    showSavedFigure('plotErrorByHour', 'error_by_hour'),
    showSavedFigure('plotResiduals', 'residuals', false),
    showSavedFigure('plotCalibration', 'quantile_calibration'),
  ]);

  const extrapolation = models.extrapolation || {};
  $('extrapolationBody').innerHTML = `
    <p class="prose">Two models in the table, the ones ending <code>_level</code>, are
    worse than simply reusing yesterday's demand. That is reported rather than dropped,
    because the reason is worth understanding.</p>
    <p class="prose">A gradient-boosted tree predicts an average of values it saw in
    training, so it can never output a number above its training maximum. India's demand
    grew through the period: the training block topped out at
    ${fmtGW(extrapolation.train_max_demand_mw)}, but the test block reaches
    ${fmtGW(extrapolation.test_max_demand_mw)}, with
    ${fmt(extrapolation.test_hours_above_train_max)} test hours above anything the model
    had ever seen.</p>
    <p class="prose">The level model duly saturates. Its highest prediction across the
    whole test period is ${fmtGW(extrapolation.level_model_max_prediction_mw)}. The models
    used for the headline results predict the change from the same hour yesterday and add
    that anchor back, which lifts the ceiling: their highest prediction is
    ${fmtGW(extrapolation.delta_model_max_prediction_mw)}.</p>`;

  const quantile = methods.quantile || {};
  const interval = quantile.interval_p10_p90 || {};
  const validation = quantile.validation_calibration?.interval_p10_p90 || {};
  const volatility = quantile.target_volatility || {};
  $('calibrationNote').innerHTML = `
    <div class="callout callout-warn" style="margin-top:18px">
      <strong>The uncertainty band is not well calibrated, and should not be used as one.</strong>
      <span>The P10 to P90 range covers the real value
      ${interval.empirical_coverage ? `${(interval.empirical_coverage * 100).toFixed(1)}%` : 'n/a'}
      of the time on the test period, against a nominal 80%. On the validation block it
      covers ${validation.empirical_coverage ? `${(validation.empirical_coverage * 100).toFixed(1)}%` : 'n/a'},
      so the band is roughly right where it was tuned and too narrow out of sample. The
      validation block (January and February) has a mean daily demand range of
      ${fmtGW(volatility.valid?.daily_range_mean_mw)} against
      ${fmtGW(volatility.test?.daily_range_mean_mw)} on test, which is the kind of
      distribution shift that produces exactly this failure. Reported as measured. Fixing
      it would need recalibration on a period that spans a hot season.</span>
    </div>`;
}

// --------------------------------------------------------------------------
// Explainability
// --------------------------------------------------------------------------

async function loadExplain() {
  const explain = await api('/api/explainability');
  $('explainCaveat').textContent = explain.caveat;

  const entries = Object.entries(explain.gain_importance).slice(0, 18).reverse();
  animateBarsH('plotImportance', [{
    type: 'bar', orientation: 'h',
    x: entries.map(([, value]) => value),
    y: entries.map(([name]) => name),
    marker: {
      color: entries.map(([name]) =>
        (name.includes('temp') || name.includes('cdd') || name.includes('hdd') || name.includes('heat'))
          ? PALETTE.weather : PALETTE.baseline),
      line: { width: 0 },
    },
    hovertemplate: '%{y}: %{x:.3f}<extra></extra>',
  }], {
    height: 540,
    margin: { l: 220, r: 26, t: 12, b: 46 },
    xaxis: { ...LOCKED_AXES.xaxis, title: 'Share of total split gain' },
    yaxis: { ...LOCKED_AXES.yaxis, automargin: true },
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
      <span class="range">${dateOnly(block.start)} to ${dateOnly(block.end)}</span>
      <span class="hours">${fmt(block.hours)} h</span>
    </div>`;
  }).join('');

  $('sourceList').innerHTML = methods.sources.map((source) => `
    <div class="source">
      <h3>${escapeHtml(source.name)}</h3>
      <div class="meta">${escapeHtml(source.via)} | ${escapeHtml(source.licence)}</div>
      <div class="note">${escapeHtml(source.note)}</div>
      <div class="note"><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.url)}</a></div>
    </div>`).join('');

  const weighting = methods.weighting_sensitivity || {};
  $('weightingTable').innerHTML = `
    <caption>Weather aggregation weighting. The weather model wins under all three
    schemes, so the conclusion does not rest on the choice of city weights.</caption>
    <thead><tr><th>Scheme</th><th>Error (MW)</th><th>Reduction vs no weather</th></tr></thead>
    <tbody>${Object.entries(weighting).map(([name, entry]) => `
      <tr><td>${escapeHtml(name)}</td><td>${fmt(entry.mae_mw)}</td>
      <td>${fmt(entry.mae_reduction_vs_no_weather_pct, 2)}%</td></tr>`).join('')}</tbody>`;

  const rolling = methods.rolling_backtest || {};
  $('rollingTable').innerHTML = `
    <caption>Rolling-origin backtest. The model is refitted every month on all earlier
    data and scored on the month it has never seen. Mean error across folds.</caption>
    <thead><tr><th>Model</th><th>Mean fold error (MW)</th></tr></thead>
    <tbody>${Object.entries(rolling).map(([name, value]) => `
      <tr><td>${escapeHtml(name)}</td><td>${fmt(value)}</td></tr>`).join('')}</tbody>`;

  $('limitationList').innerHTML = methods.limitations
    .map((item) => `<li>${escapeHtml(item)}</li>`).join('');
}

// --------------------------------------------------------------------------
// Hero visualisation
// --------------------------------------------------------------------------

/* A looping illustration of the idea behind the project: a load curve is
 * observed up to "now", then continues as a forecast whose uncertainty band
 * widens with the horizon.
 *
 * The shape is generated, not measured. It carries no axis values, no units
 * and a caption saying so, because this project does not put invented numbers
 * in front of a reader. It is decoration that explains the concept. Every real
 * figure on the site comes from the evaluation run.
 */
function startHeroViz() {
  const canvas = $('heroCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let width = 0;
  let height = 0;
  let running = false;
  let frame = null;
  let onScreen = true;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /* A plausible daily load shape in normalised units: overnight trough,
   * midday shoulder, and the evening peak this project is about. */
  function load(t) {
    return (
      0.5
      + 0.20 * Math.sin(t - 1.7)
      + 0.13 * Math.sin(2 * t + 0.6)
      + 0.05 * Math.sin(3.4 * t + 1.1)
      + 0.025 * Math.sin(7.3 * t)
    );
  }

  const PAD_L = 18;
  const PAD_R = 18;
  const PAD_T = 46;
  const PAD_B = 26;

  function draw(timestamp) {
    const phase = timestamp / 5200;
    const plotW = width - PAD_L - PAD_R;
    const plotH = height - PAD_T - PAD_B;
    const toY = (v) => PAD_T + plotH - v * plotH * 0.84 - plotH * 0.08;

    ctx.clearRect(0, 0, width, height);

    // Grid, drifting left so the panel reads as a monitor.
    ctx.strokeStyle = 'rgba(140,170,225,0.085)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = PAD_T + (plotH / 4) * i;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(width - PAD_R, y); ctx.stroke();
    }
    const spacing = 58;
    const drift = (phase * 90) % spacing;
    for (let x = PAD_L - drift; x < width - PAD_R; x += spacing) {
      if (x < PAD_L) continue;
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + plotH); ctx.stroke();
    }

    // "Now" sits two thirds across: observed behind it, forecast ahead.
    const nowX = PAD_L + plotW * 0.62;
    const span = 7.2;
    const steps = 150;
    const tAt = (x) => ((x - PAD_L) / plotW) * span + phase * 2.0;
    const aheadW = Math.max(width - PAD_R - nowX, 1);

    // Uncertainty band, widening with the forecast horizon.
    ctx.beginPath();
    for (let i = 0; i <= steps; i += 1) {
      const x = nowX + (aheadW * i) / steps;
      const h = (x - nowX) / aheadW;
      ctx.lineTo(x, toY(load(tAt(x)) + 0.035 + 0.1 * h * h));
    }
    for (let i = steps; i >= 0; i -= 1) {
      const x = nowX + (aheadW * i) / steps;
      const h = (x - nowX) / aheadW;
      ctx.lineTo(x, toY(load(tAt(x)) - 0.035 - 0.1 * h * h));
    }
    ctx.closePath();
    ctx.fillStyle = 'rgba(255,176,32,0.13)';
    ctx.fill();

    // Observed segment.
    const stroke = ctx.createLinearGradient(PAD_L, 0, width - PAD_R, 0);
    stroke.addColorStop(0, 'rgba(34,211,238,0.35)');
    stroke.addColorStop(0.55, 'rgba(233,238,251,0.95)');
    stroke.addColorStop(1, 'rgba(233,238,251,0.95)');
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2.4;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (let i = 0; i <= steps; i += 1) {
      const x = PAD_L + ((nowX - PAD_L) * i) / steps;
      ctx.lineTo(x, toY(load(tAt(x))));
    }
    ctx.stroke();

    // Forecast segment, dashed to separate it from what was measured.
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = 'rgba(255,176,32,0.95)';
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i <= steps; i += 1) {
      const x = nowX + (aheadW * i) / steps;
      ctx.lineTo(x, toY(load(tAt(x))));
    }
    ctx.stroke();
    ctx.restore();

    // The forecast-issue moment.
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = 'rgba(140,170,225,0.40)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(nowX, PAD_T - 6);
    ctx.lineTo(nowX, PAD_T + plotH);
    ctx.stroke();
    ctx.restore();

    const headY = toY(load(tAt(nowX)));
    const halo = 9.5 + Math.sin(timestamp / 300) * 1.4;
    ctx.beginPath();
    ctx.arc(nowX, headY, halo, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,176,32,0.16)';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(nowX, headY, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#ffb020';
    ctx.fill();

    // Sampled observations along the measured part.
    for (let i = 0; i < 7; i += 1) {
      const x = PAD_L + ((nowX - PAD_L) / 7) * (i + 0.5);
      const y = toY(load(tAt(x)));
      const twinkle = 0.3 + 0.3 * Math.sin(timestamp / 520 + i * 1.3);
      ctx.beginPath();
      ctx.arc(x, y, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(34,211,238,' + (twinkle + 0.35).toFixed(3) + ')';
      ctx.fill();
    }

    if (running) frame = requestAnimationFrame(draw);
  }

  // Only animate while the panel is on screen, the tab is visible, and the
  // overview is the active view. An off-screen canvas loop is wasted battery.
  function shouldRun() {
    return onScreen
      && !document.hidden
      && !REDUCED_MOTION
      && $('view-overview').classList.contains('is-active');
  }

  function start() {
    if (running) return;
    running = true;
    frame = requestAnimationFrame(draw);
  }
  function stop() {
    running = false;
    if (frame) cancelAnimationFrame(frame);
    frame = null;
  }

  resize();
  window.addEventListener('resize', () => {
    resize();
    if (!running) draw(performance.now());
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else if (shouldRun()) start();
  });
  document.querySelectorAll('.nav-link').forEach((link) => {
    link.addEventListener('click', () => {
      setTimeout(() => { if (shouldRun()) start(); else stop(); }, 60);
    });
  });

  if ('IntersectionObserver' in window) {
    new IntersectionObserver((entries) => {
      onScreen = entries[0].isIntersecting;
      if (shouldRun()) start(); else stop();
    }, { threshold: 0.05 }).observe(canvas);
  }

  if (REDUCED_MOTION) draw(0);   // a single static frame, no loop
  else start();
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

window.addEventListener('hashchange', () => {
  const name = viewFromHash();
  const current = document.querySelector('.view.is-active');
  if (current && current.id === `view-${name}`) return;
  showView(name);
});

window.addEventListener('resize', () => {
  document.querySelectorAll('.view.is-active .plot').forEach((node) => {
    if (node.data) Plotly.Plots.resize(node);
  });
});

loadCoverageBanner();
showView(viewFromHash());
startHeroViz();
onScroll();
