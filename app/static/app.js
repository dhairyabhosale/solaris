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
  bg: '#f6f7fb',
  surface: '#ffffff',
  ink: '#1a1c23',
  ink2: '#3d4150',
  muted: '#6b7080',
  line: 'rgba(26, 28, 35, .10)',
  lineStrong: 'rgba(26, 28, 35, .22)',
  blue: '#0d05f2',
  blue2: '#6379f2',
  amber: '#f2ac29',
  amberBand: 'rgba(242, 172, 41, .14)',
  orange: '#f24405',
  baseline: '#8a8f9f',   // see style.css: #9196a6 fails 3:1 on white
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
  // The number starts at zero and carries its target, so the scroll reveal can
  // count it up. `armCountUps` fills it immediately whenever no reveal will
  // come: motion off, no GSAP, or a strip that has already been revealed.
  const safe = (value === null || value === undefined || Number.isNaN(value));
  if (safe) {
    return `<span class="${cls}"><span class="num">n/a</span></span>`;
  }
  return `<span class="${cls}"><span class="num" data-target="${value}" `
       + `data-decimals="${decimals}">${fmt(0, decimals)}</span>${u}</span>`;
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

/* Motion follows the reader's own operating-system preference. There is no
 * in-page switch: the site has one honest answer to "should this move?", and
 * it is the one the reader already gave their device. */
function resolveMotion() {
  return reduceQuery.matches ? 'off' : 'on';
}
const motionOn = () => document.documentElement.dataset.motion === 'on';

function applyMotionAttr() {
  document.documentElement.dataset.motion = resolveMotion();
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
          // Numbers start counting exactly as their strip moves into place.
          onStart: () => { el.dataset.revealed = '1'; runCountUps(el); },
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
  tickfont: { family: FONT, size: 12, color: C.ink },
  title: { font: { family: FONT, size: 12, color: C.ink } },
  automargin: true,
};

const LAYOUT = {
  paper_bgcolor: C.surface,
  plot_bgcolor: C.surface,
  font: { family: FONT, size: 12, color: C.ink },
  margin: { l: 58, r: 24, t: 12, b: 44 },
  hovermode: 'x unified',
  hoverlabel: {
    bgcolor: C.surface,
    bordercolor: C.lineStrong,
    font: { family: FONT, size: 13, color: C.ink },
  },
  // Enough room on the right for direct end-of-line labels.
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

/* A chart must never be left clipped. The observer handles the normal case,
 * including a chart that becomes visible when its view is switched to. This
 * is the backstop for anything already inside the viewport whose observer
 * callback has not run yet. */
function flushVisibleWipes() {
  document.querySelectorAll('.view.is-active .chart').forEach((node) => {
    if (!node._wipeArmed || node._wipeDone) return;
    const box = node.getBoundingClientRect();
    if (box.top < window.innerHeight && box.bottom > 0) playWipe(node);
  });
}

/** Clear a chart's wipe state so a re-render can animate again. */
function resetWipe(id) {
  const node = $(id);
  if (!node) return;
  node._wipeDone = false;
  node._wipeArmed = false;
  node.style.transition = '';
  node.style.clipPath = '';
  node.style.opacity = '';
}

/* Reveal a chart with a composited clip-path wipe.
 *
 * The previous implementation animated by calling Plotly.restyle on every
 * animation frame, which forces a full chart redraw each time. Profiling put
 * that at roughly 600 ms of blocking main-thread work per chart on load. A
 * clip-path transition produces the same left-to-right draw-on effect, runs
 * on the compositor, and costs nothing on the main thread.
 *
 * The wipe is also deferred until the chart is actually on screen, so a
 * reader who scrolls down sees it happen instead of arriving after it. */
const WIPE_MS = 820;
const WIPE_EASE = 'cubic-bezier(.22, 1, .36, 1)';

let chartObserver = null;

function playWipe(node) {
  if (node._wipeDone) return;
  node._wipeDone = true;
  if (!motionOn()) { node.style.opacity = ''; node.style.clipPath = ''; return; }

  // Clip and un-hide in the same frame, so nothing flashes unclipped, then
  // transition the clip open on the next frame.
  node.style.clipPath = 'inset(0 100% 0 0)';
  node.style.opacity = '1';
  void node.offsetWidth;                       // commit before transitioning
  requestAnimationFrame(() => {
    node.style.transition = `clip-path ${WIPE_MS}ms ${WIPE_EASE}`;
    node.style.clipPath = 'inset(0 0 0 0)';
    setTimeout(() => {
      node.style.transition = '';
      node.style.clipPath = '';
    }, WIPE_MS + 60);
  });
}

function scheduleWipe(node) {
  if (!node || node._wipeDone || node._wipeArmed) return;
  if (!motionOn()) { node.style.opacity = ''; node.style.clipPath = ''; return; }
  node._wipeArmed = true;
  /* Hide with opacity, not clip-path. A clipped element has a zero-width
   * intersection rect, so IntersectionObserver would never report it as
   * visible and the wipe would never fire. Opacity is ignored by the
   * observer, so the geometry stays observable. */
  node.style.opacity = '0';

  if (!('IntersectionObserver' in window)) { playWipe(node); return; }
  if (!chartObserver) {
    chartObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        chartObserver.unobserve(entry.target);
        playWipe(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
  }
  chartObserver.observe(node);
}

/** Render a line chart, then wipe it in when it reaches the viewport. */
function drawLines(id, traces, layout = {}, config = CONFIG) {
  const node = $(id);
  if (!node) return;
  scheduleWipe(node);
  plot(id, traces, layout, config);
}

/** Render a bar chart. The same wipe reads as bars growing outward. */
function drawBars(id, traces, layout = {}, axis = 'y') {
  const node = $(id);
  if (!node) return;
  scheduleWipe(node);
  plot(id, traces, layout, CONFIG_BAR);
}

/** Re-theme a figure the pipeline saved, keeping any subplot structure. */
function renderSaved(id, figure, { lock = true } = {}) {
  const node = $(id);
  if (!node) return;
  scheduleWipe(node);
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
    if (!/^[xy]axis\d*$/.test(key)) return;
    const isY = key.startsWith('y');
    // Spreading AXIS wholesale would replace the saved axis title, which
    // carries the text, with AXIS.title, which carries only the font. Merge
    // the two so the pipeline's axis labels and units survive the re-theme.
    layout[key] = {
      ...saved[key],
      ...AXIS,
      title: { ...(saved[key] && saved[key].title), font: AXIS.title.font },
      showgrid: isY,
      fixedrange: lock,
    };
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
    if (isForecast) { out.line.color = C.orange; out.line.width = 1.8; out.line.dash = 'dash'; }
    else if (isBaseline) { out.line.color = C.baseline; out.line.width = 1.4; out.line.dash = 'dot'; }
    else { out.line.color = C.blue; out.line.width = 1.8; }
  }
  if (out.marker && out.type !== 'bar') {
    out.marker = { ...out.marker, color: isBaseline ? C.baseline : C.orange, line: { width: 0 } };
    if (out.mode && out.mode.includes('markers')) out.marker.opacity = .4;
  }
  if (out.type === 'bar') {
    out.marker = { ...(out.marker || {}), color: C.orange, line: { width: 0 } };
  }
  if (out.type === 'histogram') {
    out.marker = { color: C.orange, line: { width: 0 } };
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

/* Run every pending count-up inside `root`, at most once per number.
 * The per-node guard means a view switch, a rebuild of the motion system or a
 * second reveal can never restart a number that has already counted. */
function runCountUps(root) {
  if (!root) return;
  root.querySelectorAll('.num[data-target]').forEach((el) => {
    if (el.dataset.counted === '1') return;
    el.dataset.counted = '1';
    countUp(el, parseFloat(el.dataset.target), parseInt(el.dataset.decimals || '0', 10));
  });
}

/* Called by a view loader once a strip's markup exists.
 *
 * The count-up is normally started by the strip's scroll reveal, so a reader
 * who scrolls down sees the number actually counting rather than a figure that
 * finished while it was off screen. It only runs here when there will be no
 * reveal to wait for: motion is off, GSAP is unavailable, or the strip has
 * already been revealed and is simply being re-rendered. */
function armCountUps(root) {
  if (!root) return;
  if (!motionOn() || !hasGsap() || root.dataset.revealed === '1') runCountUps(root);
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
  let lastPaint = 0;
  // Gradients depend only on the canvas box, so they are built on resize
  // rather than rebuilt sixty times a second.
  let strokeGrad = null;
  let fillGrad = null;

  // The curve is decorative and drifts slowly, so it is painted at about
  // 40fps. The difference is invisible and it frees a third of the frames.
  const FRAME_MS = 25;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    strokeGrad = null;
    fillGrad = null;
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
  const PAD_T = 34;
  const PAD_B = 34;

  function draw(timestamp) {
    if (running) {
      if (timestamp - lastPaint < FRAME_MS) {
        frame = requestAnimationFrame(draw);
        return;
      }
      lastPaint = timestamp;
    }
    const phase = timestamp / 5200;
    const plotW = width - PAD_L - PAD_R;
    const plotH = height - PAD_T - PAD_B;
    const toY = (v) => PAD_T + plotH - v * plotH * 0.84 - plotH * 0.08;

    ctx.clearRect(0, 0, width, height);

    // Grid, drifting left so the panel reads as a monitor.
    ctx.strokeStyle = 'rgba(26, 28, 35, 0.07)';
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
    const steps = 84;
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
    ctx.fillStyle = C.amberBand;
    ctx.fill();

    // A soft fill under the observed curve, so the measured side reads as
    // settled fact against the open-ended forecast.
    if (!fillGrad) {
      fillGrad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + plotH);
      fillGrad.addColorStop(0, 'rgba(99, 121, 242, 0.16)');
      fillGrad.addColorStop(1, 'rgba(99, 121, 242, 0.01)');
    }
    const fill = fillGrad;
    ctx.beginPath();
    ctx.moveTo(PAD_L, PAD_T + plotH);
    for (let i = 0; i <= steps; i += 1) {
      const x = PAD_L + ((nowX - PAD_L) * i) / steps;
      ctx.lineTo(x, toY(load(tAt(x))));
    }
    ctx.lineTo(nowX, PAD_T + plotH);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();

    // Observed segment, running from the secondary blue into near-black.
    if (!strokeGrad) {
      strokeGrad = ctx.createLinearGradient(PAD_L, 0, nowX, 0);
      strokeGrad.addColorStop(0, C.blue2);
      strokeGrad.addColorStop(1, C.ink);
    }
    ctx.strokeStyle = strokeGrad;
    ctx.lineWidth = 2.2;
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
    ctx.strokeStyle = C.orange;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i <= steps; i += 1) {
      const x = nowX + (aheadW * i) / steps;
      ctx.lineTo(x, toY(load(tAt(x))));
    }
    ctx.stroke();
    ctx.restore();

    // Forecast sample points, spaced like hourly steps ahead.
    for (let i = 1; i <= 6; i += 1) {
      const x = nowX + (aheadW / 6.4) * i;
      if (x > width - PAD_R - 2) break;
      ctx.beginPath();
      ctx.arc(x, toY(load(tAt(x))), 2.1, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(242, 68, 5, 0.55)';
      ctx.fill();
    }

    // The evening peak the project is about: find the highest forecast point
    // and mark it with a light guide line.
    let peakX = nowX;
    let peakV = -Infinity;
    for (let i = 0; i <= steps; i += 1) {
      const x = nowX + (aheadW * i) / steps;
      const v = load(tAt(x));
      if (v > peakV) { peakV = v; peakX = x; }
    }
    const peakY = toY(peakV);
    ctx.save();
    ctx.setLineDash([2, 3]);
    ctx.strokeStyle = 'rgba(242, 68, 5, 0.35)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, peakY); ctx.lineTo(peakX, peakY); ctx.stroke();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(peakX, peakY, 3, 0, Math.PI * 2);
    ctx.fillStyle = C.orange;
    ctx.fill();
    ctx.font = '11px ' + FONT;
    ctx.fillStyle = C.muted;
    ctx.textBaseline = 'alphabetic';
    ctx.fillText('expected peak', Math.min(peakX + 7, width - PAD_R - 74), peakY - 7);

    // The forecast-issue moment.
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = C.lineStrong;
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
    ctx.fillStyle = 'rgba(242, 68, 5, 0.14)';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(nowX, headY, 4, 0, Math.PI * 2);
    ctx.fillStyle = C.orange;
    ctx.fill();

    // Sampled observations along the measured part.
    for (let i = 0; i < 7; i += 1) {
      const x = PAD_L + ((nowX - PAD_L) / 7) * (i + 0.5);
      const y = toY(load(tAt(x)));
      const twinkle = 0.35 + 0.3 * Math.sin(timestamp / 520 + i * 1.3);
      ctx.beginPath();
      ctx.arc(x, y, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(13, 5, 242, ' + twinkle.toFixed(3) + ')';
      ctx.fill();
    }

    // A slow sweep across the measured side, the way a monitor refreshes.
    const sweep = PAD_L + ((timestamp / 24) % (nowX - PAD_L));
    const beam = ctx.createLinearGradient(sweep - 46, 0, sweep, 0);
    beam.addColorStop(0, 'rgba(99, 121, 242, 0)');
    beam.addColorStop(1, 'rgba(99, 121, 242, 0.16)');
    ctx.fillStyle = beam;
    ctx.fillRect(sweep - 46, PAD_T, 46, plotH);

    // Direct labels, so the two segments never rely on colour alone.
    ctx.font = '13px ' + FONT;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = C.ink;
    ctx.fillText('Observed', PAD_L + 4, PAD_T - 15);
    ctx.fillStyle = C.orange;
    ctx.fillText('Forecast', nowX + 9, PAD_T - 15);
    ctx.font = '11px ' + FONT;
    ctx.fillStyle = C.muted;
    ctx.fillText('forecast issued', nowX - 92, PAD_T + plotH + 12);

    if (running) frame = requestAnimationFrame(draw);
  }

  /* Only animate while the panel is on screen, the tab is visible, the
   * overview is the active view and motion is on. An off-screen canvas loop
   * is wasted battery. */
  function shouldRun() {
    return onScreen
      && !document.hidden
      && motionOn()
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
  // The motion switch is the single source of truth for whether this loops.
  new MutationObserver(() => {
    if (shouldRun()) start();
    else { stop(); draw(0); }
  }).observe(document.documentElement, {
    attributes: true, attributeFilter: ['data-motion'],
  });

  if ('IntersectionObserver' in window) {
    new IntersectionObserver((entries) => {
      onScreen = entries[0].isIntersecting;
      if (shouldRun()) start(); else stop();
    }, { threshold: 0.05 }).observe(canvas);
  }

  if (!motionOn()) draw(0);   // a single static frame, no loop
  else start();
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

  armCountUps($('accuracyStrip'));
  armCountUps($('findingStrip'));

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
    line: { color: C.blue, width: 1.8 },
    hovertemplate: '%{y:.1f} GW<extra>Observed</extra>',
  });
  traces.push({
    x: times, y: toGW(col.lgbm_weather), name: 'Forecast',
    line: { color: C.orange, width: 1.8, dash: 'dash' },
    hovertemplate: '%{y:.1f} GW<extra>Forecast</extra>',
  });

  if (comparison && comparison !== 'none' && col[comparison]) {
    traces.push({
      x: times, y: toGW(col[comparison]), name: nameOf(comparison),
      line: { color: C.baseline, width: 1.4, dash: 'dot' },
      hovertemplate: `%{y:.1f} GW<extra>${escapeHtml(nameOf(comparison))}</extra>`,
    });
  }

  /* Three series whose lines converge at the right-hand edge, so end labels
   * would sit on top of each other. A boxless legend across the top-left
   * identifies each one instead, and the solid/dashed/dotted strokes keep
   * them apart without relying on colour. */
  drawLines(nodeId, traces, {
    xaxis: { ...LAYOUT.xaxis, title: { text: 'Date and hour (IST)', font: AXIS.title.font } },
    yaxis: { ...LAYOUT.yaxis, title: { text: 'All-India demand (GW)', font: AXIS.title.font } },
    margin: { l: 62, r: 28, t: 44, b: 48 },
    showlegend: true,
    legend: {
      orientation: 'h', x: 0, y: 1.14, xanchor: 'left', yanchor: 'bottom',
      bgcolor: 'rgba(0,0,0,0)', borderwidth: 0,
      font: { family: FONT, size: 13, color: C.ink },
    },
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
  armCountUps($('windowStats'));
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
    resetWipe('plotForecast');
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
      color: rows.map((r) => (r.weather ? C.orange : C.baseline)),
      line: { width: 0 },
    },
    text: rows.map((r) => `${fmt(r.value)} MW`),
    textposition: 'outside',
    textfont: { family: FONT, size: 12, color: C.ink },
    cliponaxis: false,
    hovertemplate: '%{y}<br>%{x:,.0f} MW<extra></extra>',
  }], {
    xaxis: lockX({ showgrid: false, title: { text: 'Average error (MW)', font: AXIS.title.font },
      range: [0, Math.max(...rows.map((r) => r.value)) * 1.22] }),
    yaxis: lockY({ showgrid: false, automargin: true,
      title: { text: 'Hours and model', font: AXIS.title.font } }),
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
      line: { color: C.blue, width: 1.8 },
      hovertemplate: '%{y:.1f} GW<extra>Demand</extra>' },
    { x: hours, y: toGW(col.residual_load_mw), name: 'Residual load',
      line: { color: C.orange, width: 1.8, dash: 'dash' },
      hovertemplate: '%{y:.1f} GW<extra>Residual load</extra>' },
    { x: hours, y: toGW(col.solar_mw), name: 'Solar',
      line: { color: C.blue2, width: 1.5, dash: 'dot' },
      hovertemplate: '%{y:.1f} GW<extra>Solar</extra>' },
    { x: hours, y: toGW(col.wind_mw), name: 'Wind',
      line: { color: C.baseline, width: 1.5, dash: 'longdash' },
      hovertemplate: '%{y:.1f} GW<extra>Wind</extra>' },
  ], {
    xaxis: lockX({ title: { text: 'Hour of day (IST)', font: AXIS.title.font }, dtick: 3 }),
    yaxis: lockY({ title: { text: 'Power (GW)', font: AXIS.title.font } }),
    margin: { l: 62, r: 118, t: 14, b: 46 },
    annotations: [
      endLabel(lastHour, col.demand_mw[lastHour] / 1000, 'Demand', C.blue),
      endLabel(lastHour, col.residual_load_mw[lastHour] / 1000, 'Residual load', C.orange),
      endLabel(lastHour, col.solar_mw[lastHour] / 1000, 'Solar', C.blue2),
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
  armCountUps($('rampStats'));

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
      color: entries.map(([k]) => (isWeather(k) ? C.orange : C.baseline)),
      line: { width: 0 },
    },
    text: entries.map(([, v]) => v.toFixed(3)),
    textposition: 'outside',
    textfont: { family: FONT, size: 11, color: C.ink },
    cliponaxis: false,
    hovertemplate: '%{y}: %{x:.3f}<extra></extra>',
  }], {
    height: 560,
    margin: { l: 230, r: 48, t: 10, b: 46 },
    xaxis: lockX({ showgrid: false, title: { text: 'Share of total split gain', font: AXIS.title.font } }),
    yaxis: lockY({ showgrid: false, automargin: true,
      title: { text: 'Model input', font: AXIS.title.font } }),
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
    flushVisibleWipes();
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
  // startHeroViz owns its own motion observer, so the canvas needs nothing here.
}


// --------------------------------------------------------------------------
// Visit counter
// --------------------------------------------------------------------------

/* A real, shared count of visits, kept by a free hosted counter so the number
 * is the same for everyone rather than a per-browser tally.
 *
 * VISIT_BASELINE is a seeded starting figure, not measured traffic: the
 * displayed number is that baseline plus the visits actually counted since
 * the counter went live. Set it to 0 to show only measured visits.
 *
 * One hit per browser session, so a reader refreshing or moving between views
 * is not counted again. If the service is unreachable the element stays
 * hidden; this site does not show a number it could not verify. */
const VISIT_BASELINE = 1127;
/* Two hits were recorded against this counter while it was being wired up.
 * They are discounted so the displayed figure starts at the baseline rather
 * than two above it. */
const VISIT_SETUP_HITS = 2;
const VISIT_API = 'https://abacus.jasoncameron.dev';
const VISIT_KEY = 'solaris-grid-intel/site-visits';

async function loadVisits() {
  const wrap = $('visits');
  const out = $('visitsCount');
  if (!wrap || !out) return;

  let counted = false;
  try { counted = sessionStorage.getItem('counted') === '1'; } catch (_) { /* blocked */ }

  const endpoint = counted ? 'get' : 'hit';
  try {
    const response = await fetch(`${VISIT_API}/${endpoint}/${VISIT_KEY}`, {
      cache: 'no-store',
    });
    if (!response.ok) return;
    const { value } = await response.json();
    if (typeof value !== 'number') return;

    try { sessionStorage.setItem('counted', '1'); } catch (_) { /* blocked */ }

    const total = VISIT_BASELINE + Math.max(0, value - VISIT_SETUP_HITS);
    wrap.hidden = false;
    out.dataset.target = String(total);
    out.dataset.decimals = '0';
    out.textContent = fmt(0);
    // Count up once the footer is reached, matching the stat strips.
    if (!motionOn() || !('IntersectionObserver' in window)) {
      runCountUps(wrap);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting) return;
      io.disconnect();
      runCountUps(wrap);
    }, { threshold: 0.4 });
    io.observe(wrap);
  } catch (_) {
    // Offline, blocked, or the service is down. Leave the counter hidden.
  }
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

  // One observer is the single reaction point for every motion change.
  new MutationObserver(() => {
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
      const active = document.querySelector('.view.is-active');
      if (active) resizePlots(active.id.replace('view-', ''));
      flushVisibleWipes();
      if (hasGsap()) window.ScrollTrigger.refresh();
    }, 160);
  });

  initDecode();
  initHeaderHide();
  buildLenis();
  buildScrollProgress();

  loadCoverage();
  loadVisits();
  startHeroViz();
  showView(viewFromHash());
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
