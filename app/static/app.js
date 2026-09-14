// Solaris — Heat Safety Companion
// Vanilla JS app: onboarding -> chat, backed by /onboarding, /chat,
// /worker/{id}/status. No build step, no framework.

const STORAGE_KEY = "solaris_worker_id";

const el = {
  onboardingScreen: document.getElementById("onboarding-screen"),
  appScreen: document.getElementById("app-screen"),
  onboardingForm: document.getElementById("onboarding-form"),
  onboardingSubmit: document.getElementById("onboarding-submit"),
  onboardingError: document.getElementById("onboarding-error"),
  switchWorkerBtn: document.getElementById("switch-worker-btn"),
  riskBadge: document.getElementById("risk-badge"),
  riskLabel: document.getElementById("risk-label"),
  profileWorker: document.getElementById("profile-worker"),
  profileWorkType: document.getElementById("profile-work-type"),
  profileShift: document.getElementById("profile-shift"),
  profileLocation: document.getElementById("profile-location"),
  messageList: document.getElementById("message-list"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  sendBtn: document.getElementById("send-btn"),
};

const RISK_LABELS = {
  low: "Low risk",
  moderate: "Moderate risk",
  high: "High risk",
  extreme: "Extreme risk",
  unknown: "Checking today's risk…",
};

const ICONS = {
  assistant: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
};

let workerId = localStorage.getItem(STORAGE_KEY);

init();

async function init() {
  if (!workerId) {
    showOnboarding();
    return;
  }

  try {
    const status = await api(`/worker/${encodeURIComponent(workerId)}/status`, { method: "GET" });
    if (!status.onboarded) {
      localStorage.removeItem(STORAGE_KEY);
      workerId = null;
      showOnboarding();
      return;
    }
    showApp(status.profile);
    setRiskLevel(status.risk_level || null, { animate: false });
    for (const turn of status.history) {
      renderStoredTurn(turn);
    }
    scrollToBottom();
  } catch (err) {
    console.error("Failed to restore session", err);
    showOnboarding();
  }
}

function showOnboarding() {
  el.appScreen.hidden = true;
  el.onboardingScreen.hidden = false;
}

function showApp(profile) {
  el.onboardingScreen.hidden = true;
  el.appScreen.hidden = false;
  el.profileWorker.textContent = profile.worker_id;
  el.profileWorkType.textContent = titleCase(profile.work_type);
  el.profileShift.textContent = `${profile.work_start} – ${profile.work_end}`;
  el.profileLocation.textContent = profile.location;

  // Autofocus (and the scroll-into-view a browser does for it) is a desktop-
  // only nicety: on narrow viewports it yanks the page past the profile
  // card the instant onboarding finishes, and pops the keyboard unasked.
  if (window.innerWidth > 860) {
    el.chatInput.focus();
  }
}

el.onboardingForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideOnboardingError();

  const formData = new FormData(el.onboardingForm);
  const payload = {
    worker_id: formData.get("worker_id").trim(),
    work_type: formData.get("work_type"),
    work_start: formData.get("work_start"),
    work_end: formData.get("work_end"),
    location: formData.get("location").trim(),
  };

  if (!payload.worker_id || !payload.work_type || !payload.location) {
    showOnboardingError("Please fill in every field.");
    return;
  }

  setButtonLoading(el.onboardingSubmit, true);

  try {
    await api("/onboarding", { method: "POST", body: payload });
    workerId = payload.worker_id;
    localStorage.setItem(STORAGE_KEY, workerId);
    showApp(payload);
    await sendMessage("Checking in for today", { fromUser: false });
  } catch (err) {
    showOnboardingError(err.message || "Something went wrong. Please try again.");
  } finally {
    setButtonLoading(el.onboardingSubmit, false);
  }
});

el.switchWorkerBtn.addEventListener("click", () => {
  localStorage.removeItem(STORAGE_KEY);
  workerId = null;
  el.messageList.innerHTML = "";
  el.onboardingForm.reset();
  setRiskLevel(null, { animate: false });
  showOnboarding();
});

el.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = el.chatInput.value.trim();
  if (!message) return;
  el.chatInput.value = "";
  await sendMessage(message, { fromUser: true });
});

async function sendMessage(message, { fromUser }) {
  if (fromUser) {
    appendUserBubble(message);
  }

  el.sendBtn.disabled = true;
  const typingEl = appendTypingIndicator();
  scrollToBottom();

  try {
    const res = await api("/chat", { method: "POST", body: { worker_id: workerId, message } });
    typingEl.remove();

    if (res.escalate) {
      appendAlertCard(res.reply);
    } else {
      appendAssistantBubble(res.reply);
    }

    setRiskLevel(res.risk_level, { animate: true });
  } catch (err) {
    typingEl.remove();
    appendErrorBubble(err.message || "Couldn't reach Solaris. Check your connection and try again.");
  } finally {
    el.sendBtn.disabled = false;
    scrollToBottom();
  }
}

function renderStoredTurn(turn) {
  if (turn.role === "user") {
    appendUserBubble(turn.content, { animate: false });
  } else {
    appendAssistantBubble(turn.content, { animate: false });
  }
}

function appendUserBubble(text, { animate = true } = {}) {
  const msg = document.createElement("div");
  msg.className = "msg msg-user";
  if (!animate) msg.style.animation = "none";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  el.messageList.appendChild(msg);
  scrollToBottom();
}

function appendAssistantBubble(text, { animate = true } = {}) {
  const msg = document.createElement("div");
  msg.className = "msg msg-assistant";
  if (!animate) msg.style.animation = "none";
  msg.innerHTML = `<div class="avatar">${ICONS.assistant}</div>`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  el.messageList.appendChild(msg);
  scrollToBottom();
}

function appendAlertCard(text) {
  const msg = document.createElement("div");
  msg.className = "msg msg-escalate";
  msg.innerHTML = `
    <div class="alert-card">
      <div class="alert-icon">${ICONS.alert}</div>
      <div class="alert-body">
        <strong>Seek care now</strong>
        <p></p>
      </div>
    </div>`;
  msg.querySelector(".alert-body p").textContent = text;
  el.messageList.appendChild(msg);
}

function appendErrorBubble(text) {
  const msg = document.createElement("div");
  msg.className = "msg msg-assistant msg-error";
  msg.innerHTML = `<div class="avatar">${ICONS.assistant}</div>`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  el.messageList.appendChild(msg);
}

function appendTypingIndicator() {
  const msg = document.createElement("div");
  msg.className = "msg msg-assistant msg-typing";
  msg.innerHTML = `
    <div class="avatar">${ICONS.assistant}</div>
    <div class="bubble typing-dots"><span></span><span></span><span></span></div>`;
  el.messageList.appendChild(msg);
  return msg;
}

function setRiskLevel(level, { animate }) {
  const resolved = level && RISK_LABELS[level] ? level : level === null ? null : "unknown";
  if (resolved === null) {
    el.riskBadge.dataset.level = "";
    el.riskLabel.textContent = "Awaiting check-in";
    return;
  }
  el.riskBadge.dataset.level = resolved === "unknown" ? "" : resolved;
  el.riskLabel.textContent = RISK_LABELS[resolved];

  if (animate) {
    el.riskBadge.classList.remove("risk-updated");
    // Force reflow so the animation can restart.
    void el.riskBadge.offsetWidth;
    el.riskBadge.classList.add("risk-updated");
  }
}

function setButtonLoading(btn, isLoading) {
  btn.classList.toggle("is-loading", isLoading);
  btn.disabled = isLoading;
}

function showOnboardingError(text) {
  el.onboardingError.textContent = text;
  el.onboardingError.hidden = false;
}

function hideOnboardingError() {
  el.onboardingError.hidden = true;
}

function scrollToBottom() {
  el.messageList.scrollTop = el.messageList.scrollHeight;
}

function titleCase(str) {
  return str.replace(/\b\w/g, (c) => c.toUpperCase());
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
