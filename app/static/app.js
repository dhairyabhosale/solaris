// Solaris — Heat Safety Companion. Vanilla JS, no build step, no framework.
// Backed by /onboarding, /chat, /worker/{id}/status, /worker/{id}/acknowledge,
// /worker/{id}/hydration, /worker/{id}/traces.

const KNOWN_WORKERS_KEY = "solaris_known_workers"; // [{worker_id, work_type, location}]
const ACTIVE_WORKER_KEY = "solaris_active_worker"; // worker_id string

const RISK_LABELS = {
  low: "Low heat risk",
  moderate: "Moderate heat risk",
  high: "High heat risk",
  extreme: "Extreme heat risk",
  unknown: "Awaiting check-in",
};

const RISK_COLORS = {
  low: "#0d9488",
  moderate: "#d97706",
  high: "#ea580c",
  extreme: "#dc2626",
  unknown: "#94a3b8",
};

const GAUGE_CIRCUMFERENCE = 440;
const GAUGE_MIN_C = 20;
const GAUGE_MAX_C = 50;

const el = {
  onboardingScreen: document.getElementById("onboardingScreen"),
  onboardingForm: document.getElementById("onboardingForm"),
  onboardingSubmit: document.getElementById("onboardingSubmit"),
  onboardingError: document.getElementById("onboardingError"),
  appRoot: document.getElementById("appRoot"),

  liveClockTime: document.getElementById("liveClockTime"),
  liveClockDate: document.getElementById("liveClockDate"),

  workerPillName: document.getElementById("workerPillName"),
  workerPillRole: document.getElementById("workerPillRole"),
  btnOpenSwitcher: document.getElementById("btnOpenSwitcher"),

  heatRiskBadge: document.getElementById("heatRiskBadge"),
  heatRiskBadgeText: document.getElementById("heatRiskBadgeText"),
  heatIndexVal: document.getElementById("heatIndexVal"),
  gaugeProgress: document.getElementById("gaugeProgress"),
  locationVal: document.getElementById("locationVal"),
  tempVal: document.getElementById("tempVal"),
  humidityVal: document.getElementById("humidityVal"),
  peakHourVal: document.getElementById("peakHourVal"),
  escalationBanner: document.getElementById("escalationBanner"),
  escalationText: document.getElementById("escalationText"),

  nextStepVal: document.getElementById("nextStepVal"),
  nextStepSubtext: document.getElementById("nextStepSubtext"),
  waterTargetVal: document.getElementById("waterTargetVal"),
  scheduleList: document.getElementById("scheduleList"),
  ackBtn: document.getElementById("ackBtn"),
  ackBtnLabel: document.getElementById("ackBtnLabel"),

  hydrationFill: document.getElementById("hydrationFill"),
  hydrationBarFill: document.getElementById("hydrationBarFill"),
  hydrationCurrentText: document.getElementById("hydrationCurrentText"),
  btnLogSip: document.getElementById("btnLogSip"),

  chatHistory: document.getElementById("chatHistory"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  btnSendMessage: document.getElementById("btnSendMessage"),
  quickChips: document.querySelectorAll(".chip-btn"),

  prismDrawer: document.getElementById("prismDrawer"),
  btnTogglePrism: document.getElementById("btnTogglePrism"),
  btnClosePrism: document.getElementById("btnClosePrism"),
  prismSessionIdDisplay: document.getElementById("prismSessionIdDisplay"),
  prismTracesContainer: document.getElementById("prismTracesContainer"),

  switcherModal: document.getElementById("switcherModal"),
  btnCloseSwitcher: document.getElementById("btnCloseSwitcher"),
  workerList: document.getElementById("workerList"),
  btnShowAddWorker: document.getElementById("btnShowAddWorker"),
  addWorkerForm: document.getElementById("addWorkerForm"),
  addWorkerSubmit: document.getElementById("addWorkerSubmit"),
  addWorkerError: document.getElementById("addWorkerError"),

  sosModal: document.getElementById("sosModal"),
  btnSos: document.getElementById("btnSos"),
  btnCloseSos: document.getElementById("btnCloseSos"),
};

let activeWorkerId = null;
let activeProfile = null;
let currentSchedule = null;
let hydrationTargetMl = null;

init();

async function init() {
  setupEventListeners();
  startLiveClock();

  activeWorkerId = localStorage.getItem(ACTIVE_WORKER_KEY);
  const known = loadKnownWorkers();

  if (!activeWorkerId && known.length) {
    activeWorkerId = known[known.length - 1].worker_id;
  }

  if (!activeWorkerId) {
    showOnboarding();
    return;
  }

  try {
    const status = await api(`/worker/${encodeURIComponent(activeWorkerId)}/status`, { method: "GET" });
    if (!status.onboarded) {
      forgetWorker(activeWorkerId);
      activeWorkerId = null;
      showOnboarding();
      return;
    }
    showApp();
    hydrateFromStatus(status);
  } catch (err) {
    console.error("Failed to restore session", err);
    showOnboarding();
  }
}

function setupEventListeners() {
  el.onboardingForm.addEventListener("submit", handleOnboardingSubmit);
  el.addWorkerForm.addEventListener("submit", handleAddWorkerSubmit);
  el.chatForm.addEventListener("submit", handleChatSubmit);
  el.btnLogSip.addEventListener("click", () => logHydration(250));
  el.ackBtn.addEventListener("click", handleAcknowledge);

  el.quickChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      el.chatInput.value = chip.getAttribute("data-msg");
      el.chatForm.requestSubmit();
    });
  });

  el.btnTogglePrism.addEventListener("click", () => {
    el.prismDrawer.classList.toggle("open");
    if (el.prismDrawer.classList.contains("open")) loadPrismTraces();
  });
  el.btnClosePrism.addEventListener("click", () => el.prismDrawer.classList.remove("open"));

  el.btnOpenSwitcher.addEventListener("click", openSwitcher);
  el.btnCloseSwitcher.addEventListener("click", closeSwitcher);
  el.btnShowAddWorker.addEventListener("click", () => {
    el.addWorkerForm.hidden = false;
    el.btnShowAddWorker.hidden = true;
  });

  el.btnSos.addEventListener("click", () => el.sosModal.classList.add("active"));
  el.btnCloseSos.addEventListener("click", () => el.sosModal.classList.remove("active"));
}

// ---------------------------------------------------------------------
// Live clock (local JS clock, IST - matches the backend's schedule timezone;
// no external "current time" API needed or more reliable than one)
// ---------------------------------------------------------------------

function startLiveClock() {
  tickClock();
  setInterval(tickClock, 1000);
}

function tickClock() {
  const now = new Date();
  el.liveClockTime.textContent = now.toLocaleTimeString("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  el.liveClockDate.textContent =
    now.toLocaleDateString("en-GB", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
    }) + " IST";
}

// ---------------------------------------------------------------------
// Known workers (client-side registry so "switch worker" can offer a
// picker instead of forcing a brand-new signup every time)
// ---------------------------------------------------------------------

function loadKnownWorkers() {
  try {
    return JSON.parse(localStorage.getItem(KNOWN_WORKERS_KEY)) || [];
  } catch (_) {
    return [];
  }
}

function saveKnownWorkers(list) {
  localStorage.setItem(KNOWN_WORKERS_KEY, JSON.stringify(list));
}

function rememberWorker(profile) {
  const known = loadKnownWorkers().filter((w) => w.worker_id !== profile.worker_id);
  known.push({ worker_id: profile.worker_id, work_type: profile.work_type, location: profile.location });
  saveKnownWorkers(known);
}

function forgetWorker(workerId) {
  saveKnownWorkers(loadKnownWorkers().filter((w) => w.worker_id !== workerId));
}

function setActiveWorker(workerId) {
  activeWorkerId = workerId;
  localStorage.setItem(ACTIVE_WORKER_KEY, workerId);
}

// ---------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------

function showOnboarding() {
  el.appRoot.hidden = true;
  el.onboardingScreen.hidden = false;
}

function showApp() {
  el.onboardingScreen.hidden = true;
  el.appRoot.hidden = false;
}

// ---------------------------------------------------------------------
// Onboarding (first-ever visit)
// ---------------------------------------------------------------------

async function handleOnboardingSubmit(e) {
  e.preventDefault();
  hideError(el.onboardingError);

  const payload = {
    worker_id: document.getElementById("obWorkerId").value.trim(),
    work_type: document.getElementById("obWorkType").value,
    work_start: document.getElementById("obStart").value,
    work_end: document.getElementById("obEnd").value,
    location: document.getElementById("obLocation").value.trim(),
  };

  if (!payload.worker_id || !payload.work_type || !payload.location) {
    showError(el.onboardingError, "Please fill in every field.");
    return;
  }

  setButtonLoading(el.onboardingSubmit, true);
  try {
    await api("/onboarding", { method: "POST", body: payload });
    rememberWorker(payload);
    setActiveWorker(payload.worker_id);
    activeProfile = payload;
    showApp();
    renderWorkerPill(payload);
    await sendMessage("Checking in for today", { fromUser: false });
  } catch (err) {
    showError(el.onboardingError, err.message || "Something went wrong. Please try again.");
  } finally {
    setButtonLoading(el.onboardingSubmit, false);
  }
}

async function handleAddWorkerSubmit(e) {
  e.preventDefault();
  hideError(el.addWorkerError);

  const payload = {
    worker_id: document.getElementById("awWorkerId").value.trim(),
    work_type: document.getElementById("awWorkType").value,
    work_start: document.getElementById("awStart").value,
    work_end: document.getElementById("awEnd").value,
    location: document.getElementById("awLocation").value.trim(),
  };

  if (!payload.worker_id || !payload.work_type || !payload.location) {
    showError(el.addWorkerError, "Please fill in every field.");
    return;
  }

  setButtonLoading(el.addWorkerSubmit, true);
  try {
    await api("/onboarding", { method: "POST", body: payload });
    rememberWorker(payload);
    setActiveWorker(payload.worker_id);
    closeSwitcher();
    showApp();
    resetDashboard();
    renderWorkerPill(payload);
    activeProfile = payload;
    await sendMessage("Checking in for today", { fromUser: false });
  } catch (err) {
    showError(el.addWorkerError, err.message || "Something went wrong. Please try again.");
  } finally {
    setButtonLoading(el.addWorkerSubmit, false);
  }
}

// ---------------------------------------------------------------------
// Worker switcher
// ---------------------------------------------------------------------

function openSwitcher() {
  renderWorkerList();
  el.addWorkerForm.hidden = true;
  el.btnShowAddWorker.hidden = false;
  el.addWorkerForm.reset();
  hideError(el.addWorkerError);
  el.switcherModal.classList.add("active");
}

function closeSwitcher() {
  el.switcherModal.classList.remove("active");
}

function renderWorkerList() {
  const known = loadKnownWorkers();
  el.workerList.innerHTML = "";

  if (!known.length) {
    el.workerList.innerHTML = `<p class="worker-list-empty">No other workers yet.</p>`;
    return;
  }

  for (const w of known) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "worker-row" + (w.worker_id === activeWorkerId ? " is-active" : "");
    row.innerHTML = `
      <div class="worker-avatar">👷</div>
      <div class="worker-row-info">
        <span class="worker-row-name">${escapeHtml(w.worker_id)}</span>
        <span class="worker-row-meta">${escapeHtml(titleCase(w.work_type))} • ${escapeHtml(w.location)}</span>
      </div>
      ${w.worker_id === activeWorkerId ? '<span class="worker-row-active-tag">Active</span>' : ""}
    `;
    row.addEventListener("click", () => switchToWorker(w.worker_id));
    el.workerList.appendChild(row);
  }
}

async function switchToWorker(workerId) {
  if (workerId === activeWorkerId) {
    closeSwitcher();
    return;
  }
  try {
    const status = await api(`/worker/${encodeURIComponent(workerId)}/status`, { method: "GET" });
    if (!status.onboarded) {
      forgetWorker(workerId);
      renderWorkerList();
      return;
    }
    setActiveWorker(workerId);
    closeSwitcher();
    resetDashboard();
    hydrateFromStatus(status);
  } catch (err) {
    console.error("Failed to switch worker", err);
  }
}

// ---------------------------------------------------------------------
// Dashboard hydrate / reset
// ---------------------------------------------------------------------

function resetDashboard() {
  el.chatHistory.innerHTML = "";
  el.scheduleList.innerHTML = "";
  el.scheduleList.hidden = true;
  el.ackBtn.hidden = true;
  el.escalationBanner.hidden = true;
  currentSchedule = null;
  hydrationTargetMl = null;

  // Clear every widget back to a neutral/loading state - otherwise, while a
  // new worker's first check-in is still in flight, their dashboard would
  // briefly show the *previous* worker's stale weather/risk/hydration data
  // under the new worker's name.
  renderRisk(null, false);
  el.heatIndexVal.textContent = "--";
  el.gaugeProgress.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE);
  el.locationVal.textContent = "—";
  el.tempVal.textContent = "—°C";
  el.humidityVal.textContent = "—%";
  el.peakHourVal.textContent = "—:00";
  el.nextStepVal.textContent = "—";
  el.nextStepSubtext.textContent = "No plan yet";
  el.waterTargetVal.textContent = "— ml/hr";
  el.hydrationCurrentText.textContent = "0 ml logged";
  el.hydrationBarFill.style.width = "0%";
  el.hydrationFill.setAttribute("y", "90");
  el.hydrationFill.setAttribute("height", "0");
}

function hydrateFromStatus(status) {
  activeProfile = status.profile;
  renderWorkerPill(status.profile);
  renderRisk(status.risk_level, false);
  renderWeather(status.temperature_c, status.feels_like_c, status.humidity_pct, status.peak_hour ?? status.peak_heat_hour, status.profile);
  hydrationTargetMl = status.hydration_target_ml;
  renderHydration(status.hydration_logged_ml, status.hydration_target_ml, { animate: false });

  if (status.schedule && status.schedule.length) {
    renderSchedule(status.schedule);
    renderAcknowledged(status.acknowledged);
  }

  for (const turn of status.history) {
    if (turn.role === "user") appendUserMessage(turn.content, { animate: false });
    else appendBotMessage(turn.content, { escalate: false, animate: false });
  }
  scrollChatToBottom();
}

function renderWorkerPill(profile) {
  el.workerPillName.textContent = profile.worker_id;
  el.workerPillRole.textContent = `${titleCase(profile.work_type)} • ${profile.location}`;
}

// ---------------------------------------------------------------------
// Risk badge + gauge + weather stats
// ---------------------------------------------------------------------

function renderRisk(riskLevel, escalate) {
  const level = riskLevel && RISK_LABELS[riskLevel] ? riskLevel : "unknown";
  el.heatRiskBadge.className = "badge-risk " + level + (escalate ? " escalate" : "");
  el.heatRiskBadgeText.textContent = RISK_LABELS[level];
}

function renderWeather(temperatureC, feelsLikeC, humidityPct, peakHour, profile) {
  if (temperatureC == null) return;

  el.heatIndexVal.textContent = Math.round(feelsLikeC);
  el.tempVal.textContent = `${Math.round(temperatureC)}°C`;
  el.humidityVal.textContent = `${Math.round(humidityPct)}%`;
  el.locationVal.textContent = profile ? profile.location : "—";
  el.peakHourVal.textContent = peakHour != null ? `${String(peakHour).padStart(2, "0")}:00` : "—:00";

  const fraction = Math.min(Math.max((feelsLikeC - GAUGE_MIN_C) / (GAUGE_MAX_C - GAUGE_MIN_C), 0), 1);
  el.gaugeProgress.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE * (1 - fraction));
}

function renderEscalation(escalate, message) {
  if (escalate) {
    el.escalationBanner.hidden = false;
    el.escalationText.textContent = message;
  } else {
    el.escalationBanner.hidden = true;
  }
}

// ---------------------------------------------------------------------
// Schedule / "Today's plan"
// ---------------------------------------------------------------------

function renderSchedule(schedule) {
  currentSchedule = schedule;
  el.scheduleList.innerHTML = "";

  for (const step of schedule) {
    const li = document.createElement("li");
    li.className = "schedule-step";
    li.dataset.time = step.time;
    li.innerHTML = `
      <span class="step-time">${escapeHtml(step.time)}</span>
      <span class="step-action">${escapeHtml(step.action)}</span>
      <span class="step-detail">${escapeHtml(step.detail)}</span>`;
    el.scheduleList.appendChild(li);
  }

  el.scheduleList.hidden = false;
  el.ackBtn.hidden = false;
  updateSchedulePassedState();
}

function updateSchedulePassedState() {
  if (!currentSchedule || !currentSchedule.length) return;

  const nowMinutes = nowMinutesIST();
  let passed = 0;
  let next = null;

  for (const li of el.scheduleList.children) {
    const stepMinutes = minutesSinceMidnight(li.dataset.time);
    const isPassed = stepMinutes <= nowMinutes;
    li.classList.toggle("step-passed", isPassed);
    if (isPassed) passed += 1;
    else if (!next) next = { time: li.dataset.time, action: li.querySelector(".step-action").textContent };
  }

  if (next) {
    el.nextStepVal.textContent = next.time;
    el.nextStepSubtext.textContent = next.action;
  } else {
    el.nextStepVal.textContent = "Done";
    el.nextStepSubtext.textContent = "Shift plan complete";
  }
}

function minutesSinceMidnight(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// The backend grounds schedule times in Asia/Kolkata (app.config.local_now).
// Comparing against the browser's own local time would misjudge which
// steps have passed for anyone not physically in IST.
function nowMinutesIST() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const hour = Number(parts.find((p) => p.type === "hour").value);
  const minute = Number(parts.find((p) => p.type === "minute").value);
  return hour * 60 + minute;
}

setInterval(updateSchedulePassedState, 60000);

function renderAcknowledged(acknowledged) {
  el.ackBtn.classList.toggle("is-acknowledged", acknowledged);
  el.ackBtn.disabled = acknowledged;
  el.ackBtnLabel.textContent = acknowledged ? "Got it — noted" : "Got it";
}

async function handleAcknowledge() {
  if (!activeWorkerId) return;
  try {
    await api(`/worker/${encodeURIComponent(activeWorkerId)}/acknowledge`, { method: "POST" });
    renderAcknowledged(true);
  } catch (err) {
    console.error("Failed to record acknowledgment", err);
  }
}

// ---------------------------------------------------------------------
// Hydration (animated bottle + bar, real logged intake)
// ---------------------------------------------------------------------

function renderHydration(loggedMl, targetMl, { animate = true } = {}) {
  const target = targetMl || 2000;
  const fraction = Math.min(loggedMl / target, 1);

  el.waterTargetVal.textContent = hydrationTargetMl ? `${Math.round(hydrationTargetMl / _shiftHoursGuess())} ml/hr` : "— ml/hr";
  el.hydrationCurrentText.textContent = `${loggedMl} ml / ${target} ml today`;
  el.hydrationBarFill.style.width = `${Math.round(fraction * 100)}%`;

  const bottleInnerHeight = 82;
  const fillHeight = bottleInnerHeight * fraction;
  el.hydrationFill.setAttribute("y", String(90 - fillHeight));
  el.hydrationFill.setAttribute("height", String(fillHeight));

  if (animate) {
    el.hydrationFill.classList.remove("just-logged");
    void el.hydrationFill.offsetWidth;
    el.hydrationFill.classList.add("just-logged");
  }
}

function _shiftHoursGuess() {
  if (!activeProfile) return 8;
  const [sh, sm] = activeProfile.work_start.split(":").map(Number);
  const [eh, em] = activeProfile.work_end.split(":").map(Number);
  let mins = eh * 60 + em - (sh * 60 + sm);
  if (mins <= 0) mins += 24 * 60;
  return Math.max(mins / 60, 1);
}

async function logHydration(amountMl) {
  if (!activeWorkerId) return;
  el.btnLogSip.disabled = true;
  try {
    const res = await api(`/worker/${encodeURIComponent(activeWorkerId)}/hydration`, {
      method: "POST",
      body: { amount_ml: amountMl },
    });
    renderHydration(res.hydration_logged_ml, hydrationTargetMl, { animate: true });
  } catch (err) {
    console.error("Failed to log hydration", err);
  } finally {
    el.btnLogSip.disabled = false;
  }
}

// ---------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------

async function handleChatSubmit(e) {
  e.preventDefault();
  const message = el.chatInput.value.trim();
  if (!message) return;
  el.chatInput.value = "";
  await sendMessage(message, { fromUser: true });
}

async function sendMessage(message, { fromUser }) {
  if (fromUser) appendUserMessage(message);

  el.btnSendMessage.disabled = true;
  const typingEl = appendTypingIndicator();
  scrollChatToBottom();

  try {
    const res = await api("/chat", { method: "POST", body: { worker_id: activeWorkerId, message } });
    typingEl.remove();

    appendBotMessage(res.reply, { escalate: res.escalate });
    renderRisk(res.risk_level, res.escalate);
    renderEscalation(res.escalate, res.reply);
    renderWeather(res.temperature_c, res.feels_like_c, res.humidity_pct, res.peak_heat_hour, activeProfile);
    hydrationTargetMl = res.hydration_target_ml;
    renderHydration(res.hydration_logged_ml, res.hydration_target_ml, { animate: false });

    if (res.schedule && res.schedule.length && JSON.stringify(res.schedule) !== JSON.stringify(currentSchedule)) {
      renderSchedule(res.schedule);
      renderAcknowledged(false);
    }

    if (el.prismDrawer.classList.contains("open")) loadPrismTraces();
  } catch (err) {
    typingEl.remove();
    appendBotMessage(err.message || "Couldn't reach Solaris. Check your connection and try again.", { escalate: false });
  } finally {
    el.btnSendMessage.disabled = false;
    scrollChatToBottom();
  }
}

function appendUserMessage(text, { animate = true } = {}) {
  const msg = document.createElement("div");
  msg.className = "chat-msg user";
  if (!animate) msg.style.animation = "none";
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  el.chatHistory.appendChild(msg);
  scrollChatToBottom();
}

function appendBotMessage(text, { escalate = false, animate = true } = {}) {
  const msg = document.createElement("div");
  msg.className = "chat-msg bot";
  if (!animate) msg.style.animation = "none";

  const avatar = document.createElement("div");
  avatar.className = "chat-avatar";
  avatar.textContent = "☀️";
  msg.appendChild(avatar);

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  if (escalate) {
    const card = document.createElement("div");
    card.className = "symptom-alert-card";
    card.innerHTML = `<h5>🚨 Seek care now</h5><p></p>`;
    card.querySelector("p").textContent = text;
    bubble.appendChild(card);
  } else {
    bubble.textContent = text;
  }

  msg.appendChild(bubble);
  el.chatHistory.appendChild(msg);
  scrollChatToBottom();
}

function appendTypingIndicator() {
  const msg = document.createElement("div");
  msg.className = "chat-msg bot";
  msg.innerHTML = `<div class="chat-avatar">☀️</div><div class="chat-bubble is-typing">Solaris is reasoning…</div>`;
  el.chatHistory.appendChild(msg);
  return msg;
}

function scrollChatToBottom() {
  el.chatHistory.scrollTop = el.chatHistory.scrollHeight;
}

// ---------------------------------------------------------------------
// PRISM live trace log
// ---------------------------------------------------------------------

async function loadPrismTraces() {
  if (!activeWorkerId) return;
  try {
    const data = await api(`/worker/${encodeURIComponent(activeWorkerId)}/traces`, { method: "GET" });
    el.prismSessionIdDisplay.textContent = data.session_id;

    if (!data.traces.length) {
      el.prismTracesContainer.innerHTML = `<div class="prism-empty">No traces yet — send a message to Solaris.</div>`;
      return;
    }

    el.prismTracesContainer.innerHTML = data.traces
      .slice()
      .reverse()
      .map((trace, idx) => {
        const outputPreview = trace.output.length > 140 ? trace.output.slice(0, 140) + "…" : trace.output;
        return `
        <div class="prism-trace-card">
          <div class="prism-trace-meta">
            <span>#${data.traces.length - idx} · ${escapeHtml(trace.model)}</span>
            <span>${trace.latency_ms}ms</span>
          </div>
          <div style="color:#c2410c;"><strong>Input:</strong> ${escapeHtml(trace.input)}</div>
          <div style="color:#0f766e;"><strong>Output:</strong> ${escapeHtml(outputPreview)}</div>
          <div style="color:${trace.delivered_to_prism ? "#0d9488" : "#94a3b8"};">
            ${trace.delivered_to_prism ? "✓ delivered to PRISM" : "○ PRISM tracing not configured"}
          </div>
          <details style="margin-top:6px; color:#94a3b8; cursor:pointer;">
            <summary>Full JSON</summary>
            <div class="prism-json">${escapeHtml(JSON.stringify(trace, null, 2))}</div>
          </details>
        </div>`;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load PRISM traces:", err);
  }
}

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function setButtonLoading(btn, isLoading) {
  btn.classList.toggle("is-loading", isLoading);
  btn.disabled = isLoading;
}

function showError(el_, text) {
  el_.textContent = text;
  el_.hidden = false;
}

function hideError(el_) {
  el_.hidden = true;
}

function titleCase(str) {
  return str.replace(/\b\w/g, (c) => c.toUpperCase());
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data.detail) detail = data.detail;
    } catch (_) {
      /* ignore parse errors */
    }
    throw new Error(detail);
  }

  return res.json();
}
