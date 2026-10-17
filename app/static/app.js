/* Solaris Grid Intelligence, frontend.
 *
 * Every figure comes from the API, which reads the research pipeline's own
 * output files. No metric is written by hand here. If an artefact is missing,
 * the view says which command regenerates it.
 *
 * Motion is optional throughout: Lenis and GSAP are progressive enhancements
 * and the page is fully usable and fully visible without them.
 */

'use strict';

// --------------------------------------------------------------------------
// Design tokens, mirrored from style.css for canvas and Plotly
// --------------------------------------------------------------------------

const C = {
  bg: '#0b0e13',
  surface: '#11151c',
  ink: '#e6e8eb',
  ink2: '#c9cdd3',
  muted: '#98a0ab',
  faint: '#636b76',
  line: 'rgba(230, 232, 235, .12)',
  lineStrong: 'rgba(230, 232, 235, .24)',
  amber: '#f2a43a',
  amberBand: 'rgba(242, 164, 58, .14)',
  baseline: '#7f8a99',
  heatHot: '#e8673a',
};

const FONT = 'Archivo, system-ui, sans-serif';

// People-facing names. Internals never reach the page.
const MODEL_NAMES = {
  lgbm_weather: 'LightGBM with weather features',
  lgbm_calendar_history: 'LightGBM, calendar and demand history',
  lgbm_weather_level: 'LightGBM with weather, level target',
  lgbm_calendar_history_level: 'LightGBM calendar and history, level target',
  ridge_calendar_history: 'Ridge regression, calendar and history',
  naive_lag_24h: 'Same hour yesterday',
  naive_lag_168h: 'Same hour last week',
  climatology_hour_weekday: 'Hour and weekday average',
};
const nameOf = (key) => MODEL_NAMES[key] || key;

const HEADLINE = 'lgbm_weather';

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const NBSP = ' ';

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = {};
    try { detail = (await response.json()).detail || {}; } catch (_) { /* not JSON */ }
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

const gw = (mw, d = 1) =>
  (mw === null || mw === undefined || Number.isNaN(mw)) ? 'n/a' : (mw / 1000).toFixed(d);

const dateOnly = (iso) => (iso ? String(iso).slice(0, 10) : '');

function prettyDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  });
}

/** Scientific notation with a real multiplication sign and superscripts. */
function sci(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  const exp = Math.floor(Math.log10(Math.abs(value)));
  const mantissa = (value / Math.pow(10, exp)).toFixed(1);
  const sup = { '-': '⁻', 0: '⁰', 1: '¹', 2: '²', 3: '³',
    4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹' };
  const digits = String(exp).split('').map((ch) => sup[ch] || ch).join('');
  return `${mantissa} × 10${digits}`;
}

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

/** A number plus its unit, joined so they never wrap apart. */
function valueMarkup(value, unit, decimals = 0, lead = false) {
  const cls = lead ? 'lead-value' : 'stat-value';
  const u = unit ? `<span class="unit">${NBSP}${escapeHtml(unit)}</span>` : '';
  return `<span class="${cls}"><span class="num">${fmt(value, decimals)}</span>${u}</span>`;
}

function stripCell(value, unit, caption, decimals, lead) {
  return `<div class="strip-cell">
    ${valueMarkup(value, unit, decimals, lead)}
    <p class="strip-caption">${escapeHtml(caption)}</p>
  </div>`;
}

// --------------------------------------------------------------------------
// Motion switch: one source of truth, html[data-motion]
// --------------------------------------------------------------------------

const reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
const pointerQuery = window.matchMedia('(pointer: fine)');

function resolveMotion() {
  if (sessionStorage.getItem('motion') === 'off') return 'off';
  if (reduceQuery.matches) return 'off';
  return 'on';
}
const motionOn = () => document.documentElement.dataset.motion === 'on';

function applyMotionAttr() {
  document.documentElement.dataset.motion = resolveMotion();
  syncMotionToggle();
}

function syncMotionToggle() {
  const button = $('motionToggle');
  if (!button) return;
  const on = motionOn();
  button.setAttribute('aria-pressed', String(on));
  button.textContent = on ? 'Motion on' : 'Motion off';
  button.setAttribute('aria-label', on ? 'Turn page motion off' : 'Turn page motion on');
}

const hasGsap = () => typeof window.gsap !== 'undefined' && typeof window.ScrollTrigger !== 'undefined';
const hasLenis = () => typeof window.Lenis !== 'undefined';

// --------------------------------------------------------------------------
// Smooth scrolling
// --------------------------------------------------------------------------

let lenis = null;

function buildLenis() {
  if (lenis || !hasLenis()) return;
  if (!motionOn() || !pointerQuery.matches) return;
  lenis = new window.Lenis({
    autoRaf: true,
    lerp: 0.13,
    smoothWheel: true,
    syncTouch: false,
    anchors: false,
  });
  if (hasGsap()) {
    lenis.on('scroll', window.ScrollTrigger.update);
  }
}

function destroyLenis() {
  if (!lenis) return;
  lenis.destroy();
  lenis = null;
  document.documentElement.classList.remove('lenis', 'lenis-smooth');
}

function scrollToTop() {
  if (lenis) lenis.scrollTo(0, { immediate: true });
  else window.scrollTo(0, 0);
}

// --------------------------------------------------------------------------
// Scroll reveals
// --------------------------------------------------------------------------

let viewContext = null;

/** Build the reveal triggers for a view, after its content has rendered. */
function buildReveals(viewName) {
  if (viewContext) { viewContext.revert(); viewContext = null; }
  if (!motionOn() || !hasGsap()) return;

  const root = $(`view-${viewName}`);
  if (!root) return;
  const gsap = window.gsap;

  viewContext = gsap.context(() => {
    root.querySelectorAll('[data-reveal], .section, .chart-block, .table-wrap')
      .forEach((el) => {
        gsap.fromTo(el, { y: 32 }, {
          y: 0, duration: .85, ease: 'power3.out', clearProps: 'transform',
          scrollTrigger: { trigger: el, start: 'top 94%', once: true },
        });
      });

    root.querySelectorAll('h2').forEach((el) => {
      gsap.fromTo(el,
        { filter: 'blur(7px)', y: 14, opacity: .65 },
        {
          filter: 'blur(0px)', y: 0, opacity: 1, duration: .75, ease: 'power2.out',
          immediateRender: false, clearProps: 'filter,transform,opacity',
          scrollTrigger: { trigger: el, start: 'top 91%', once: true },
        });
    });

    root.querySelectorAll('[data-sequence] > *').forEach((el, i) => {
      gsap.fromTo(el, { x: 20 }, {
        x: 0, duration: .75, delay: .08 * i, ease: 'power3.out',
        clearProps: 'transform',
        scrollTrigger: { trigger: el, start: 'top 90%', once: true },
      });
    });

    // The one scrubbed clip reveal, on chart sections that opt in.
    root.querySelectorAll('[data-clip]').forEach((el) => {
      gsap.fromTo(el,
        { clipPath: 'inset(6% 3% 6% 3%)' },
        {
          clipPath: 'inset(0% 0% 0% 0%)', ease: 'none',
          scrollTrigger: { trigger: el, start: 'top 95%', end: 'top 40%', scrub: .65 },
        });
    });

    // The findings lead number tightens as it arrives.
    const lead = root.querySelector('#findingStrip .lead-value');
    if (lead) {
      gsap.fromTo(lead, { letterSpacing: '-.06em' }, {
        letterSpacing: '-.02em', ease: 'none',
        scrollTrigger: {
          trigger: lead.closest('.section'),
          start: 'top bottom', end: 'top 35%', scrub: .8,
        },
      });
    }

    if (viewName === 'overview') {
      gsap.to('.hero', {
        y: -65, opacity: .35, ease: 'none',
        scrollTrigger: { trigger: '.hero', start: '25% top', end: 'bottom top', scrub: 1 },
      });
    }
  }, root);

  if (lenis) lenis.resize();
  window.ScrollTrigger.refresh();
}

/** The one orchestrated entrance, once per page load. */
let heroPlayed = false;
function playHero() {
  if (heroPlayed) return;
  heroPlayed = true;
  if (!motionOn() || !hasGsap()) return;
  const gsap = window.gsap;
  gsap.fromTo('.hero .reveal-line',
    { yPercent: 105, filter: 'blur(5px)' },
    {
      yPercent: 0, filter: 'blur(0px)', duration: 1.25, stagger: .12,
      ease: 'power4.out', clearProps: 'transform,filter',
    });
  gsap.fromTo('.hero .lede',
    { filter: 'blur(3px)', y: 8 },
    {
      filter: 'blur(0px)', y: 0, duration: .75, delay: .15, ease: 'power2.out',
      clearProps: 'filter,transform',
    });
}

// --------------------------------------------------------------------------
// Scroll progress and header hide
// --------------------------------------------------------------------------

let progressTrigger = null;
let progressRaf = null;

function buildScrollProgress() {
  const bar = $('scrollProgress');
  if (!bar) return;
  if (progressTrigger) { progressTrigger.kill(); progressTrigger = null; }
  if (progressRaf) { window.removeEventListener('scroll', progressRaf); progressRaf = null; }

  if (hasGsap()) {
    progressTrigger = window.ScrollTrigger.create({
      trigger: document.body, start: 'top top', end: 'bottom bottom',
      onUpdate: (self) => { bar.style.transform = `scaleX(${self.progress})`; },
    });
    return;
  }
  // Without GSAP, a throttled passive listener does the same job.
  let ticking = false;
  progressRaf = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const doc = document.documentElement;
      const max = doc.scrollHeight - doc.clientHeight;
      bar.style.transform = `scaleX(${max > 0 ? doc.scrollTop / max : 0})`;
      ticking = false;
    });
  };
  window.addEventListener('scroll', progressRaf, { passive: true });
  progressRaf();
}

/** Header hide with hysteresis, so small jitters never flip it. */
function initHeaderHide() {
  const header = $('topbar');
  let last = window.scrollY;
  let accum = 0;
  let direction = 0;
  let ticking = false;

  function update() {
    const doc = document.documentElement;
    const max = Math.max(doc.scrollHeight - doc.clientHeight, 0);
    const y = Math.min(Math.max(window.scrollY, 0), max);
    const delta = y - last;
    last = y;

    if (Math.abs(delta) < .5) { ticking = false; return; }

    const dir = delta > 0 ? 1 : -1;
    if (dir !== direction) { direction = dir; accum = 0; }
    accum += delta;

    const forced = y < 100
      || $('nav').classList.contains('is-open')
      || header.contains(document.activeElement);

    if (forced) header.dataset.hidden = 'false';
    else if (accum > 28) header.dataset.hidden = 'true';
    else if (accum < -8) header.dataset.hidden = 'false';

    ticking = false;
  }

  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(update);
  }, { passive: true });

  document.addEventListener('focusin', (event) => {
    if (header.contains(event.target)) header.dataset.hidden = 'false';
  });
}

// --------------------------------------------------------------------------
// Decode effect, navigation links only
// --------------------------------------------------------------------------

const DECODE_CHARS = '0123456789/';

function initDecode() {
  document.querySelectorAll('.nav-link').forEach((link) => {
    const label = link.textContent.trim();
    link.innerHTML =
      `<span class="decode">
         <span class="sr-only">${escapeHtml(label)}</span>
         <span class="decode-size" aria-hidden="true">${escapeHtml(label)}</span>
         <span class="decode-live" aria-hidden="true">${escapeHtml(label)}</span>
       </span>`;
    const live = link.querySelector('.decode-live');
    let raf = null;
    let cooldown = 0;

    function restore() {
      if (raf) cancelAnimationFrame(raf);
      raf = null;
      live.textContent = label;
    }

    function run() {
      if (!motionOn()) return;
      const now = performance.now();
      if (now < cooldown || raf) return;
      cooldown = now + 1550;
      const start = now;
      let painted = 0;

      const step = (t) => {
        const progress = Math.min((t - start) / 550, 1);
        if (t - painted >= 32 || progress === 1) {
          painted = t;
          const cut = label.length * progress;
          live.textContent = label
            .split('')
            .map((ch, i) => {
              if (ch === ' ' || i < cut) return ch;
              const n = Math.floor(t / 40 + i) % DECODE_CHARS.length;
              return DECODE_CHARS[n];
            })
            .join('');
        }
        if (progress < 1) raf = requestAnimationFrame(step);
        else restore();
      };
      raf = requestAnimationFrame(step);
    }

    link.addEventListener('pointerenter', run);
    link.addEventListener('focusin', run);
    document.addEventListener('visibilitychange', restore);
    link._decodeRestore = restore;

    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) { run(); io.disconnect(); }
      }, { threshold: .5 });
      io.observe(link);
    }
  });
}

function stopAllDecode() {
  document.querySelectorAll('.nav-link').forEach((link) => {
    if (link._decodeRestore) link._decodeRestore();
  });
}

// --------------------------------------------------------------------------
// Plotly: one shared template
// --------------------------------------------------------------------------

const AXIS = {
  gridcolor: C.line,
  zeroline: false,
  showline: false,
  ticks: 'outside',
  ticklen: 4,
  tickcolor: C.lineStrong,
  tickfont: { family: FONT, size: 12, color: C.muted },
  title: { font: { family: FONT, size: 12, color: C.muted } },
  automargin: true,
};

const LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { family: FONT, size: 12, color: C.muted },
  margin: { l: 58, r: 24, t: 12, b: 44 },
  hovermode: 'x unified',
  hoverlabel: {
    bgcolor: C.surface,
    bordercolor: C.lineStrong,
    font: { family: FONT, size: 13, color: C.ink },
  },
  // Horizontal gridlines only.
  xaxis: { ...AXIS, showgrid: false },
  yaxis: { ...AXIS, showgrid: true },
  showlegend: false,
};

const CONFIG = { displayModeBar: false, responsive: true };
const CONFIG_BAR = { ...CONFIG, scrollZoom: false, doubleClick: false };

const lockX = (extra = {}) => ({ ...LAYOUT.xaxis, fixedrange: true, ...extra });
const lockY = (extra = {}) => ({ ...LAYOUT.yaxis, fixedrange: true, ...extra });

function plot(id, traces, layout = {}, config = CONFIG) {
  const node = $(id);
  if (!node) return Promise.resolve();
  node.innerHTML = '';
  return Plotly.newPlot(node, traces, { ...LAYOUT, ...layout }, config);
}

/** Draw a line chart in from left to right, once. */
function drawLines(id, traces, layout = {}, config = CONFIG) {
  const node = $(id);
  if (!node) return;
  if (!motionOn()) { plot(id, traces, layout, config); return; }

  const seed = traces.map((t) => ({ ...t, x: t.x.slice(0, 1), y: t.y.slice(0, 1) }));
  node.innerHTML = '';
  Plotly.newPlot(node, seed, { ...LAYOUT, ...layout }, config).then(() => {
    const total = traces[0]?.x.length || 0;
    if (!total) return;
    const start = performance.now();
    const step = (now) => {
      const t = Math.min((now - start) / 900, 1);
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

/** Grow bars from zero along `axis` ("y" for vertical, "x" for horizontal). */
function drawBars(id, traces, layout = {}, axis = 'y') {
  const node = $(id);
  if (!node) return;
  if (!motionOn()) { plot(id, traces, layout, CONFIG_BAR); return; }

  const zeroed = traces.map((t) => ({ ...t, [axis]: t[axis].map(() => 0) }));
  node.innerHTML = '';
  Plotly.newPlot(node, zeroed, { ...LAYOUT, ...layout }, CONFIG_BAR).then(() => {
    Plotly.animate(node, {
      data: traces.map((t) => ({ [axis]: t[axis] })),
      traces: traces.map((_, i) => i),
    }, {
      transition: { duration: 780, easing: 'cubic-out' },
      frame: { duration: 780, redraw: false },
    });
  });
}

/** Re-theme a figure the pipeline saved, keeping any subplot structure. */
function renderSaved(id, figure, { lock = true } = {}) {
  const node = $(id);
  if (!node) return;
  node.innerHTML = '';
  const saved = figure.layout || {};
  const layout = {
    ...saved,
    title: undefined,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: LAYOUT.font,
    hoverlabel: LAYOUT.hoverlabel,
    showlegend: false,
    // Keep the saved margin when the figure needs the room: a wide left
    // gutter for long category labels, or headroom for subplot titles.
    margin: (saved.margin && (saved.margin.l > 90 || saved.margin.t > 40))
      ? saved.margin : LAYOUT.margin,
  };
  Object.keys(saved).forEach((key) => {
    if (/^xaxis\d*$/.test(key)) layout[key] = { ...saved[key], ...AXIS, showgrid: false, fixedrange: lock };
    if (/^yaxis\d*$/.test(key)) layout[key] = { ...saved[key], ...AXIS, showgrid: true, fixedrange: lock };
  });
  // Recolour saved traces onto the semantic palette.
  const data = (figure.data || []).map((trace) => recolour(trace));
  Plotly.newPlot(node, data, layout, lock ? CONFIG_BAR : CONFIG);
}

/** Map a saved trace's colours onto the three semantic roles. */
function recolour(trace) {
  const out = { ...trace };
  const label = String(trace.name || '').toLowerCase();
  const isForecast = label.includes('weather') || label.includes('forecast')
    || label.includes('fitted') || label.includes('model');
  const isBaseline = label.includes('naive') || label.includes('perfect')
    || label.includes('calendar') || label.includes('history');

  if (out.line) {
    out.line = { ...out.line };
    if (isForecast) { out.line.color = C.amber; out.line.width = 1.5; }
    else if (isBaseline) { out.line.color = C.baseline; out.line.width = 1.3; out.line.dash = 'dash'; }
    else { out.line.color = C.ink; out.line.width = 1.75; }
  }
  if (out.marker && out.type !== 'bar') {
    out.marker = { ...out.marker, color: isBaseline ? C.baseline : C.amber, line: { width: 0 } };
    if (out.mode && out.mode.includes('markers')) out.marker.opacity = .35;
  }
  if (out.type === 'bar') {
    out.marker = { ...(out.marker || {}), color: C.amber, line: { width: 0 } };
  }
  if (out.type === 'histogram') {
    out.marker = { color: C.amber, line: { width: 0 } };
  }
  return out;
}

async function showSaved(id, name, options) {
  try {
    renderSaved(id, await api(`/api/figures/${name}`), options);
  } catch (error) {
    showError($(id), error);
  }
}

/** Direct end-of-line labels, preferred over a legend. */
function endLabel(x, y, text, colour) {
  return {
    x, y, text: ` ${text}`, showarrow: false,
    xanchor: 'left', yanchor: 'middle',
    font: { family: FONT, size: 13, color: colour },
  };
}

// --------------------------------------------------------------------------
// Count-ups
// --------------------------------------------------------------------------

function countUp(element, target, decimals) {
  if (!motionOn() || target === null || Number.isNaN(target)) {
    element.textContent = fmt(target, decimals);
    return;
  }
  const duration = 1000;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / duration, 1);
    const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
    element.textContent = fmt(target * eased, decimals);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function runCountUps(root) {
  root.querySelectorAll('.num[data-target]').forEach((el) => {
    countUp(el, parseFloat(el.dataset.target), parseInt(el.dataset.decimals || '0', 10));
  });
}

// --------------------------------------------------------------------------
// Hero figure, drawn from real test-period data
// --------------------------------------------------------------------------

let heroData = null;
let heroDrawn = false;

async function loadHeroFigure() {
  const canvas = $('heroCanvas');
  if (!canvas) return;

  try {
    // Take a wide tail of the test period and keep the last stretch that has a
    // forecast, since the final test hours have no archived weather forecast.
    const series = await api('/api/series?hours=720');
    const { timestamps, columns } = series;
    let end = columns.lgbm_weather.length - 1;
    while (end >= 0 && columns.lgbm_weather[end] === null) end -= 1;
    const span = 96;
    const from = Math.max(0, end - span + 1);
    const cut = Math.max(from + 1, end - 23);   // last 24 hours shown as forecast

    heroData = {
      times: timestamps.slice(from, end + 1),
      demand: columns.demand_mw.slice(from, end + 1),
      forecast: columns.lgbm_weather.slice(from, end + 1),
      lo: (columns.q10 || []).slice(from, end + 1),
      hi: (columns.q90 || []).slice(from, end + 1),
      splitIndex: cut - from,
    };
    $('heroSource').textContent =
      `Observed demand and the model's day-ahead forecast for `
      + `${prettyDate(heroData.times[0])} to ${prettyDate(heroData.times[heroData.times.length - 1])}, `
      + 'the end of the held-out test period. Source: Grid-India hourly demand via '
      + 'Mendeley, CC BY 4.0; Solaris model output.';
  } catch (error) {
    heroData = null;
    $('heroSource').textContent =
      'Illustrative curve: the measured series could not be loaded. '
      + 'Run python -m solaris.cli report to regenerate it.';
  }

  sizeHeroCanvas();
  drawHero();
}

function sizeHeroCanvas() {
  const canvas = $('heroCanvas');
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  canvas._cssSize = [rect.width, rect.height];
}

/** Draw once. Animated on first paint when motion is on, static otherwise. */
function drawHero() {
  const canvas = $('heroCanvas');
  if (!canvas || !canvas._cssSize) return;
  const ctx = canvas.getContext('2d');
  const [W, H] = canvas._cssSize;

  // Fall back to a generated shape only if the API could not be read.
  const data = heroData || fallbackHero();
  const n = data.times.length;
  if (!n) return;

  const PAD = { l: 8, r: 92, t: 20, b: 24 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;

  const values = [];
  data.demand.forEach((v) => { if (v !== null) values.push(v); });
  data.forecast.forEach((v) => { if (v !== null) values.push(v); });
  data.lo.forEach((v) => { if (v !== null) values.push(v); });
  data.hi.forEach((v) => { if (v !== null) values.push(v); });
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.14 || 1;
  const toX = (i) => PAD.l + (plotW * i) / Math.max(n - 1, 1);
  const toY = (v) => PAD.t + plotH - ((v - min + pad) / (max - min + pad * 2)) * plotH;

  const render = (progress) => {
    ctx.clearRect(0, 0, W, H);

    // Horizontal gridlines only.
    ctx.strokeStyle = C.line;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i += 1) {
      const y = PAD.t + (plotH / 3) * i;
      ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(W - PAD.r, y); ctx.stroke();
    }

    const split = data.splitIndex;
    const shown = Math.max(1, Math.round(n * progress));

    // Uncertainty range behind the forecast.
    if (progress > .82) {
      const bandAlpha = Math.min((progress - .82) / .18, 1);
      ctx.save();
      ctx.globalAlpha = bandAlpha;
      ctx.beginPath();
      let started = false;
      for (let i = split; i < n; i += 1) {
        if (data.hi[i] === null) continue;
        const x = toX(i); const y = toY(data.hi[i]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      for (let i = n - 1; i >= split; i -= 1) {
        if (data.lo[i] === null) continue;
        ctx.lineTo(toX(i), toY(data.lo[i]));
      }
      ctx.closePath();
      ctx.fillStyle = C.amberBand;
      ctx.fill();
      ctx.restore();
    }

    // Observed demand.
    ctx.strokeStyle = C.ink;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < Math.min(shown, split + 1); i += 1) {
      if (data.demand[i] === null) continue;
      const x = toX(i); const y = toY(data.demand[i]);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (progress > .82) {
      const alpha = Math.min((progress - .82) / .18, 1);
      ctx.save();
      ctx.globalAlpha = alpha;

      // The forecast-issue moment.
      ctx.setLineDash([2, 4]);
      ctx.strokeStyle = C.lineStrong;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(toX(split), PAD.t);
      ctx.lineTo(toX(split), PAD.t + plotH);
      ctx.stroke();

      // Forecast.
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = C.amber;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      started = false;
      for (let i = split; i < n; i += 1) {
        if (data.forecast[i] === null) continue;
        const x = toX(i); const y = toY(data.forecast[i]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.restore();
      ctx.setLineDash([]);

      // Direct labels at the line ends.
      ctx.globalAlpha = alpha;
      ctx.font = `13px ${FONT}`;
      ctx.textBaseline = 'middle';
      let lastObs = split;
      while (lastObs > 0 && data.demand[lastObs] === null) lastObs -= 1;
      ctx.fillStyle = C.ink;
      ctx.fillText('Observed', toX(split) - 72, toY(data.demand[lastObs]) - 16);
      let lastFc = n - 1;
      while (lastFc > split && data.forecast[lastFc] === null) lastFc -= 1;
      ctx.fillStyle = C.amber;
      ctx.fillText('Forecast', toX(lastFc) + 8, toY(data.forecast[lastFc]));
      ctx.globalAlpha = 1;
    }
  };

  if (!motionOn() || heroDrawn) { render(1); heroDrawn = true; return; }
  heroDrawn = true;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / 1800, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    render(eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Only used when the API cannot be reached. The caption says so. */
function fallbackHero() {
  const n = 96;
  const times = []; const demand = []; const forecast = []; const lo = []; const hi = [];
  const split = 72;
  for (let i = 0; i < n; i += 1) {
    const t = (i / 24) * Math.PI * 2;
    const v = 180 + 22 * Math.sin(t - 1.7) + 12 * Math.sin(2 * t + .6);
    times.push('');
    demand.push(i <= split ? v : null);
    forecast.push(i >= split ? v : null);
    lo.push(i >= split ? v - 4 - (i - split) * .22 : null);
    hi.push(i >= split ? v + 4 + (i - split) * .22 : null);
  }
  return { times, demand, forecast, lo, hi, splitIndex: split };
}

// --------------------------------------------------------------------------
// Views
// --------------------------------------------------------------------------

let OVERVIEW = null;

async function loadCoverage() {
  try {
    OVERVIEW = OVERVIEW || await api('/api/overview');
    $('footerCoverage').textContent = OVERVIEW.data_coverage.refresh_note;
  } catch (error) {
    $('footerCoverage').textContent =
      'Pipeline output not found. Run python -m solaris.cli all';
  }
}

async function loadOverview() {
  let models;
  try {
    OVERVIEW = OVERVIEW || await api('/api/overview');
    models = await api('/api/models');
  } catch (error) {
    showError($('accuracyStrip'), error);
    return;
  }

  const coverage = OVERVIEW.data_coverage;
  const test = OVERVIEW.test_period;
  const version = OVERVIEW.model_version || {};

  $('heroMeta').innerHTML = [
    `<p class="meta-line">Trained and tested on <b>${fmt(coverage.hours)}</b> hours of all-India demand</p>`,
    `<p class="meta-line">Held out <b>${prettyDate(test.start)}</b> to <b>${prettyDate(test.end)}</b></p>`,
    `<p class="meta-line">Model <b>${escapeHtml(nameOf(version.name))}</b></p>`,
  ].join('');

  const headline = models.models.find((m) => m.model === HEADLINE) || {};
  const contribution = models.weather_contribution || {};
  const significance = contribution.significance || {};

  // Strip A: accuracy only. Nothing here repeats in strip B.
  $('accuracyStrip').innerHTML = [
    stripCell(headline.mae_mw, 'MW', 'Average error across every held-out hour', 0, true),
    stripCell(headline.mape_pct, '%', 'The same error as a share of demand', 2, false),
    stripCell(headline.peak_mae_mw, 'MW', 'Average error on the daily peak', 0, false),
    stripCell(headline.peak_hour_mae_h, 'h', 'Average error in the hour the peak lands', 2, false),
  ].join('');

  // Strip B: what weather is worth. Lead is the weather gain.
  $('findingStrip').innerHTML = [
    stripCell(contribution.mae_reduction_pct, '%',
      'Less error than the same model without weather inputs', 1, true),
    stripCell(contribution.mae_reduction_pct__hot_period, '%',
      'The gain on research-defined hot hours, where it matters most', 1, false),
    stripCell(headline.skill_vs_naive_pct, '%',
      'Less error than reusing yesterday at the same hour', 1, false),
    `<div class="strip-cell">
       <span class="stat-value">${sci(significance.paired_t_pvalue)}</span>
       <p class="strip-caption">Paired test over ${fmt(significance.n_days)} held-out days, so the gain is not chance</p>
     </div>`,
  ].join('');

  runCountUps($('accuracyStrip'));
  runCountUps($('findingStrip'));

  const series = await api('/api/series?hours=336');
  drawSeries('plotOverviewSeries', series, 'lgbm_calendar_history', true);
}

// --------------------------------------------------------------------------
// Forecast view
// --------------------------------------------------------------------------

function drawSeries(nodeId, series, comparison, showBand) {
  const times = series.timestamps;
  const col = series.columns;
  const traces = [];
  const toGW = (arr) => arr.map((v) => (v === null ? null : v / 1000));

  if (showBand && col.q10 && col.q90) {
    traces.push({
      x: times, y: toGW(col.q90), name: 'P90',
      line: { width: 0 }, hoverinfo: 'skip', showlegend: false,
    });
    traces.push({
      x: times, y: toGW(col.q10), name: 'P10 to P90 range',
      line: { width: 0 }, fill: 'tonexty', fillcolor: C.amberBand,
      hoverinfo: 'skip', showlegend: false,
    });
  }

  traces.push({
    x: times, y: toGW(col.demand_mw), name: 'Observed',
    line: { color: C.ink, width: 1.75 },
    hovertemplate: '%{y:.1f} GW<extra>Observed</extra>',
  });
  traces.push({
    x: times, y: toGW(col.lgbm_weather), name: 'Forecast',
    line: { color: C.amber, width: 1.5 },
    hovertemplate: '%{y:.1f} GW<extra>Forecast</extra>',
  });

  if (comparison && comparison !== 'none' && col[comparison]) {
    traces.push({
      x: times, y: toGW(col[comparison]), name: nameOf(comparison),
      line: { color: C.baseline, width: 1.3, dash: 'dash' },
      hovertemplate: `%{y:.1f} GW<extra>${escapeHtml(nameOf(comparison))}</extra>`,
    });
  }

  drawLines(nodeId, traces, {
    yaxis: { ...LAYOUT.yaxis, title: { text: 'All-India demand (GW)', font: AXIS.title.font } },
    margin: { l: 62, r: 24, t: 12, b: 44 },
  });

  const gaps = (col.lgbm_weather || []).filter((v) => v === null).length;
  const note = $('coverageGapNote');
  if (note) {
    if (gaps > 0) {
      note.textContent = `${gaps} hour${gaps === 1 ? '' : 's'} in this window have no `
        + 'archived weather forecast, so the model produces no value for them. The gap is '
        + 'left open rather than filled in.';
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }
}

function windowStats(series) {
  const actual = series.columns.demand_mw;
  const predicted = series.columns.lgbm_weather;
  let sum = 0; let count = 0; let peak = -Infinity; let peakAt = null;

  for (let i = 0; i < actual.length; i += 1) {
    if (actual[i] !== null && predicted[i] !== null) {
      sum += Math.abs(predicted[i] - actual[i]); count += 1;
    }
    if (actual[i] !== null && actual[i] > peak) { peak = actual[i]; peakAt = series.timestamps[i]; }
  }
  const mae = count ? sum / count : null;

  $('windowStats').innerHTML = [
    stripCell(mae, 'MW', 'Average error across the hours shown', 0, true),
    stripCell(count, '', 'Hours with both a reading and a forecast', 0, false),
    stripCell(peak === -Infinity ? null : peak / 1000, 'GW', 'Highest demand in this window', 1, false),
    `<div class="strip-cell">
       <span class="stat-value">${peakAt ? escapeHtml(prettyDate(peakAt)) : 'n/a'}</span>
       <p class="strip-caption">Day that peak fell on</p>
     </div>`,
  ].join('');
  runCountUps($('windowStats'));
}

let RANGE = null;

async function loadForecast() {
  RANGE = await api('/api/series/range');
  const first = dateOnly(RANGE.first);
  const last = dateOnly(RANGE.last);
  const startInput = $('startDate');
  const endInput = $('endDate');
  [startInput, endInput].forEach((i) => { i.min = first; i.max = last; });

  const defaultStart = new Date(RANGE.last);
  defaultStart.setDate(defaultStart.getDate() - 21);
  startInput.value = dateOnly(defaultStart.toISOString());
  endInput.value = last;

  $('bandCaveat').textContent =
    'The shaded range is under-dispersed on this test period: it covers the real value '
    + 'less often than its nominal 80 per cent. See Performance, uncertainty range '
    + 'calibration. Treat it as indicative, not as a calibrated risk band.';

  await refreshForecast();

  $('applyRange').addEventListener('click', refreshForecast);
  $('modelSelect').addEventListener('change', refreshForecast);
  $('showBand').addEventListener('change', refreshForecast);
  $('jumpPeak').addEventListener('click', jumpToPeak);

  await showSaved('plotPeakScatter', 'peak_scatter', { lock: false });
}

async function refreshForecast() {
  const start = $('startDate').value;
  const end = $('endDate').value;
  if (start && end && start > end) {
    toast('The start date must be on or before the end date.');
    return;
  }
  try {
    const series = await api(`/api/series?start=${start}&end=${end}`);
    drawSeries('plotForecast', series, $('modelSelect').value, $('showBand').checked);
    windowStats(series);
  } catch (error) {
    showError($('plotForecast'), error);
  }
}

async function jumpToPeak() {
  const all = await api('/api/series');
  const actual = all.columns.demand_mw;
  let peak = -Infinity; let index = 0;
  actual.forEach((v, i) => { if (v !== null && v > peak) { peak = v; index = i; } });

  const peakDate = new Date(all.timestamps[index]);
  const from = new Date(peakDate); from.setDate(from.getDate() - 5);
  const to = new Date(peakDate); to.setDate(to.getDate() + 5);
  const lastAvailable = new Date(RANGE.last);

  $('startDate').value = dateOnly(from.toISOString());
  $('endDate').value = dateOnly(
    (to.getTime() <= lastAvailable.getTime() ? to : lastAvailable).toISOString());
  await refreshForecast();
  toast(`Record hour: ${gw(peak)} GW on ${prettyDate(all.timestamps[index])}`);
}

// --------------------------------------------------------------------------
// Heat view
// --------------------------------------------------------------------------

async function loadHeat() {
  await showSaved('plotTempDemand', 'temperature_vs_demand', { lock: false });

  const [methods, models] = await Promise.all([api('/api/methodology'), api('/api/models')]);
  $('hotDefinition').textContent = (methods.hot_period || {}).definition || '';

  const byModel = {};
  models.models.forEach((row) => { byModel[row.model] = row; });
  const withW = byModel[HEADLINE] || {};
  const without = byModel.lgbm_calendar_history || {};

  // Sorted by value, labels at the bar ends, no gridlines.
  const rows = [
    { label: 'Hot hours, without weather', value: without.mae_hot_mw, hot: true, weather: false },
    { label: 'Hot hours, with weather', value: withW.mae_hot_mw, hot: true, weather: true },
    { label: 'All other hours, without weather', value: without.mae_mw, hot: false, weather: false },
    { label: 'All other hours, with weather', value: withW.mae_mw, hot: false, weather: true },
  ].filter((r) => r.value != null).sort((a, b) => a.value - b.value);

  drawBars('plotHotSplit', [{
    type: 'bar', orientation: 'h',
    x: rows.map((r) => r.value),
    y: rows.map((r) => r.label),
    marker: {
      color: rows.map((r) => (r.weather ? (r.hot ? C.heatHot : C.amber) : C.baseline)),
      line: { width: 0 },
    },
    text: rows.map((r) => `${fmt(r.value)} MW`),
    textposition: 'outside',
    textfont: { family: FONT, size: 12, color: C.muted },
    cliponaxis: false,
    hovertemplate: '%{y}<br>%{x:,.0f} MW<extra></extra>',
  }], {
    xaxis: lockX({ showgrid: false, title: { text: 'Average error (MW)', font: AXIS.title.font },
      range: [0, Math.max(...rows.map((r) => r.value)) * 1.22] }),
    yaxis: lockY({ showgrid: false, automargin: true }),
    margin: { l: 250, r: 32, t: 10, b: 46 },
    hovermode: 'closest',
  }, 'x');

  const sensitivity = methods.hot_threshold_sensitivity || {};
  const body = Object.entries(sensitivity).map(([name, entry]) => {
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
      <td class="is-chosen">${fmt(entry.lgbm_weather)}</td>
      <td>${fmt(reduction, 1)}%</td>
    </tr>`;
  }).join('');

  $('hotSensitivityTable').innerHTML = `
    <caption>The same comparison at four definitions of hot. The weather model wins at
    every threshold, so the finding does not depend on where the line is drawn.</caption>
    <thead><tr>
      <th>Percentile</th><th>Threshold (C)</th><th>Hot hours</th>
      <th>Error without weather (MW)</th><th>Error with weather (MW)</th><th>Reduction</th>
    </tr></thead><tbody>${body}</tbody>`;
}

// --------------------------------------------------------------------------
// Evening view
// --------------------------------------------------------------------------

async function loadEvening() {
  const [profile, stress] = await Promise.all([
    api('/api/daily-profile'), api('/api/evening-stress'),
  ]);
  const full = stress.full_period || {};
  $('residualDefinition').textContent = full.definition || 'Definition unavailable.';

  const hours = profile.hours;
  const col = profile.columns;
  const toGW = (arr) => arr.map((v) => (v === null ? null : v / 1000));
  const lastHour = hours[hours.length - 1];

  drawLines('plotDailyProfile', [
    { x: hours, y: toGW(col.demand_mw), name: 'Demand',
      line: { color: C.ink, width: 1.75 },
      hovertemplate: '%{y:.1f} GW<extra>Demand</extra>' },
    { x: hours, y: toGW(col.residual_load_mw), name: 'Residual load',
      line: { color: C.amber, width: 1.5 },
      hovertemplate: '%{y:.1f} GW<extra>Residual load</extra>' },
    { x: hours, y: toGW(col.solar_mw), name: 'Solar',
      line: { color: C.baseline, width: 1.3, dash: 'dash' },
      hovertemplate: '%{y:.1f} GW<extra>Solar</extra>' },
    { x: hours, y: toGW(col.wind_mw), name: 'Wind',
      line: { color: C.baseline, width: 1.3, dash: 'dot' },
      hovertemplate: '%{y:.1f} GW<extra>Wind</extra>' },
  ], {
    xaxis: lockX({ title: { text: 'Hour of day (IST)', font: AXIS.title.font }, dtick: 3 }),
    yaxis: lockY({ title: { text: 'Power (GW)', font: AXIS.title.font } }),
    margin: { l: 62, r: 118, t: 14, b: 46 },
    annotations: [
      endLabel(lastHour, col.demand_mw[lastHour] / 1000, 'Demand', C.ink),
      endLabel(lastHour, col.residual_load_mw[lastHour] / 1000, 'Residual load', C.amber),
      endLabel(lastHour, col.solar_mw[lastHour] / 1000, 'Solar', C.baseline),
      endLabel(lastHour, col.wind_mw[lastHour] / 1000, 'Wind', C.baseline),
    ],
  }, CONFIG_BAR);

  $('rampStats').innerHTML = [
    stripCell(full.mean_max_hourly_ramp_mw / 1000, 'GW/h',
      'Typical steepest climb in a single hour', 1, true),
    stripCell(full.p95_max_hourly_ramp_mw / 1000, 'GW/h',
      'One day in twenty is at least this steep', 1, false),
    stripCell(full.mean_max_3h_ramp_mw / 1000, 'GW',
      'Typical steepest climb across three hours', 1, false),
    `<div class="strip-cell">
       <span class="stat-value">${String(full.modal_ramp_hour_ist ?? '--').padStart(2, '0')}:00</span>
       <p class="strip-caption">Hour the steepest climb most often falls in, IST</p>
     </div>`,
  ].join('');
  runCountUps($('rampStats'));

  await showSaved('plotEveningRamp', 'evening_ramp');
}

// --------------------------------------------------------------------------
// Performance view
// --------------------------------------------------------------------------

async function loadPerformance() {
  const [models, methods] = await Promise.all([api('/api/models'), api('/api/methodology')]);
  const split = methods.split.test;

  $('perfLede').textContent =
    `Every model below was scored on the same held-out block: ${prettyDate(split.start)} `
    + `to ${prettyDate(split.end)}, ${fmt(split.hours)} hours that no model saw during `
    + 'fitting, early stopping or threshold selection.';

  $('modelTable').innerHTML = `
    <thead><tr>
      <th>Model</th><th>Error (MW)</th><th>RMSE (MW)</th><th>Error (%)</th>
      <th>Bias (MW)</th><th>Against same hour yesterday</th>
      <th>Hot hours (MW)</th><th>Peak error (MW)</th><th>Peak hour (h)</th>
    </tr></thead>
    <tbody>${models.models.map((row) => `
      <tr>
        <td${row.model === HEADLINE ? ' class="is-chosen"' : ''}>${escapeHtml(nameOf(row.model))}</td>
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
    showSaved('plotErrorByHour', 'error_by_hour'),
    showSaved('plotResiduals', 'residuals', { lock: false }),
    showSaved('plotCalibration', 'quantile_calibration'),
  ]);

  const ex = models.extrapolation || {};
  $('extrapolationBody').innerHTML = `
    <p class="prose">Two models in the table use a level target, and both are worse than
    simply reusing yesterday&rsquo;s demand. That is reported rather than dropped, because
    the reason is worth understanding.</p>
    <p class="prose">A gradient-boosted tree predicts an average of values it saw in
    training, so it can never output a number above its training maximum.
    India&rsquo;s demand grew through the period: the training block topped out at
    ${gw(ex.train_max_demand_mw)} GW, but the test block reaches
    ${gw(ex.test_max_demand_mw)} GW, with ${fmt(ex.test_hours_above_train_max)} test hours
    above anything the model had ever seen.</p>
    <p class="prose">The level model duly saturates. Its highest prediction across the
    whole test period is ${gw(ex.level_model_max_prediction_mw)} GW. The models used for
    the headline results predict the change from the same hour yesterday and add that
    anchor back, which lifts the ceiling: their highest prediction is
    ${gw(ex.delta_model_max_prediction_mw)} GW.</p>`;

  const q = methods.quantile || {};
  const interval = q.interval_p10_p90 || {};
  const valid = q.validation_calibration?.interval_p10_p90 || {};
  const vol = q.target_volatility || {};
  $('calibrationNote').innerHTML = `
    <p class="note note--warn">
      <b>The uncertainty range is not well calibrated, and should not be used as one.</b>
      The P10 to P90 range covers the real value
      ${interval.empirical_coverage ? `${(interval.empirical_coverage * 100).toFixed(1)} per cent` : 'n/a'}
      of the time on the test period, against a nominal 80. On the validation block it
      covers ${valid.empirical_coverage ? `${(valid.empirical_coverage * 100).toFixed(1)} per cent` : 'n/a'},
      so the range is roughly right where it was tuned and too narrow out of sample. The
      validation block, January and February, has a mean daily demand range of
      ${gw(vol.valid?.daily_range_mean_mw)} GW against ${gw(vol.test?.daily_range_mean_mw)} GW
      on test, which is the kind of distribution shift that produces exactly this failure.
      Reported as measured. Fixing it would need recalibration on a period that spans a
      hot season.
    </p>`;
}

// --------------------------------------------------------------------------
// Explainability view
// --------------------------------------------------------------------------

async function loadExplain() {
  const explain = await api('/api/explainability');
  $('explainCaveat').textContent = explain.caveat;

  const entries = Object.entries(explain.gain_importance).slice(0, 18).reverse();
  const isWeather = (name) => /temp|cdd|hdd|heat/.test(name);

  drawBars('plotImportance', [{
    type: 'bar', orientation: 'h',
    x: entries.map(([, v]) => v),
    y: entries.map(([k]) => k),
    marker: {
      color: entries.map(([k]) => (isWeather(k) ? C.amber : C.baseline)),
      line: { width: 0 },
    },
    text: entries.map(([, v]) => v.toFixed(3)),
    textposition: 'outside',
    textfont: { family: FONT, size: 11, color: C.muted },
    cliponaxis: false,
    hovertemplate: '%{y}: %{x:.3f}<extra></extra>',
  }], {
    height: 560,
    margin: { l: 230, r: 48, t: 10, b: 46 },
    xaxis: lockX({ showgrid: false, title: { text: 'Share of total split gain', font: AXIS.title.font } }),
    yaxis: lockY({ showgrid: false, automargin: true }),
    hovermode: 'closest',
  }, 'x');
}

// --------------------------------------------------------------------------
// Methods view
// --------------------------------------------------------------------------

async function loadMethods() {
  const methods = await api('/api/methodology');
  $('methodConvention').textContent = methods.forecast_convention;

  $('splitTable').innerHTML = ['train', 'valid', 'test'].map((part) => {
    const block = methods.split[part];
    const label = { train: 'Training', valid: 'Validation', test: 'Held-out test' }[part];
    return `<div class="split-row">
      <b>${label}</b>
      <span class="range">${prettyDate(block.start)} to ${prettyDate(block.end)}</span>
      <span class="hours num">${fmt(block.hours)} h</span>
    </div>`;
  }).join('');

  $('sourceList').innerHTML = methods.sources.map((s) => `
    <div class="source">
      <h3>${escapeHtml(s.name)}</h3>
      <div class="meta">${escapeHtml(s.via)}, ${escapeHtml(s.licence)}</div>
      <p>${escapeHtml(s.note)}</p>
      <p><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.url)}</a></p>
    </div>`).join('');

  const weighting = methods.weighting_sensitivity || {};
  $('weightingTable').innerHTML = `
    <caption>Weather aggregation weighting. The weather model wins under all three
    schemes, so the conclusion does not rest on the choice of city weights.</caption>
    <thead><tr><th>Scheme</th><th>Error (MW)</th><th>Reduction against no weather</th></tr></thead>
    <tbody>${Object.entries(weighting).map(([name, e]) => `
      <tr><td>${escapeHtml(name)}</td><td>${fmt(e.mae_mw)}</td>
      <td>${fmt(e.mae_reduction_vs_no_weather_pct, 2)}%</td></tr>`).join('')}</tbody>`;

  const rolling = methods.rolling_backtest || {};
  $('rollingTable').innerHTML = `
    <caption>Rolling-origin backtest. The model is refitted every month on all earlier
    data and scored on the month it has never seen.</caption>
    <thead><tr><th>Model</th><th>Mean error across folds (MW)</th></tr></thead>
    <tbody>${Object.entries(rolling).map(([name, v]) => `
      <tr><td${name === HEADLINE ? ' class="is-chosen"' : ''}>${escapeHtml(nameOf(name))}</td>
      <td>${fmt(v)}</td></tr>`).join('')}</tbody>`;

  $('limitationList').innerHTML = methods.limitations
    .map((item) => `<li>${escapeHtml(item)}</li>`).join('');
}

// --------------------------------------------------------------------------
// Routing
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

const loaded = new Set();

function showView(name) {
  document.querySelectorAll('.view').forEach((view) => {
    view.classList.toggle('is-active', view.id === `view-${name}`);
  });
  document.querySelectorAll('.nav-link').forEach((link) => {
    if (link.dataset.view === name) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  $('nav').classList.remove('is-open');
  $('navToggle').setAttribute('aria-expanded', 'false');
  scrollToTop();

  const finish = () => {
    resizePlots(name);
    buildReveals(name);
    if (name === 'overview') playHero();
  };

  if (!loaded.has(name)) {
    loaded.add(name);
    const loader = VIEW_LOADERS[name];
    if (loader) {
      loader()
        .then(finish)
        .catch((error) => { console.error(error); toast(error.message); finish(); });
    } else finish();
  } else {
    finish();
  }

  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
}

function resizePlots(name) {
  document.querySelectorAll(`#view-${name} .chart`).forEach((node) => {
    if (node.data) Plotly.Plots.resize(node);
  });
}

function viewFromHash() {
  const name = (location.hash || '#overview').slice(1);
  return VIEW_LOADERS[name] ? name : 'overview';
}

// --------------------------------------------------------------------------
// Motion teardown and rebuild
// --------------------------------------------------------------------------

function teardownMotion() {
  if (viewContext) { viewContext.revert(); viewContext = null; }
  if (hasGsap()) {
    window.ScrollTrigger.getAll().forEach((t) => t.kill());
    window.gsap.globalTimeline.clear();
  }
  progressTrigger = null;
  destroyLenis();
  stopAllDecode();
}

function rebuildMotion() {
  teardownMotion();
  buildLenis();
  buildScrollProgress();
  const active = document.querySelector('.view.is-active');
  if (active) buildReveals(active.id.replace('view-', ''));
  // A hero that already played is left in its final state.
  drawHero();
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

function boot() {
  applyMotionAttr();

  document.querySelectorAll('.nav-link').forEach((link) => {
    link.addEventListener('click', () => showView(link.dataset.view));
  });
  $('navToggle').addEventListener('click', () => {
    const nav = $('nav');
    const open = nav.classList.toggle('is-open');
    $('navToggle').setAttribute('aria-expanded', String(open));
  });
  document.querySelectorAll('.brand').forEach((el) => {
    el.addEventListener('click', (event) => { event.preventDefault(); showView('overview'); });
  });

  $('motionToggle').addEventListener('click', () => {
    const next = motionOn() ? 'off' : 'on';
    sessionStorage.setItem('motion', next);
    applyMotionAttr();
  });

  // One observer is the single reaction point for every motion change.
  new MutationObserver(() => {
    syncMotionToggle();
    if (motionOn()) rebuildMotion();
    else teardownMotion();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-motion'] });

  const onQueryChange = () => { applyMotionAttr(); if (motionOn()) rebuildMotion(); else teardownMotion(); };
  reduceQuery.addEventListener('change', onQueryChange);
  pointerQuery.addEventListener('change', onQueryChange);

  window.addEventListener('hashchange', () => {
    const name = viewFromHash();
    const current = document.querySelector('.view.is-active');
    if (current && current.id === `view-${name}`) return;
    showView(name);
  });

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      sizeHeroCanvas();
      drawHero();
      const active = document.querySelector('.view.is-active');
      if (active) resizePlots(active.id.replace('view-', ''));
      if (hasGsap()) window.ScrollTrigger.refresh();
    }, 160);
  });

  initDecode();
  initHeaderHide();
  buildLenis();
  buildScrollProgress();

  loadCoverage();
  loadHeroFigure();
  showView(viewFromHash());
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
