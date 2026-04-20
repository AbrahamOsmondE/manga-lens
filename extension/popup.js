const BACKEND_URL = "https://api.manga-lens.com";
const STRIPE_PAYMENT_URL = "https://manga-lens.com/pricing";

// ── DOM refs ──────────────────────────────────────────────────────────────────

const viewSignout = document.getElementById("view-signout");
const viewSignin  = document.getElementById("view-signin");
const signInBtn   = document.getElementById("signInBtn");
const signOutBtn  = document.getElementById("signOutBtn");
const userEmail   = document.getElementById("userEmail");
const tierBadge   = document.getElementById("tierBadge");
const quotaText   = document.getElementById("quotaText");
const quotaBar    = document.getElementById("quotaBar");
const quotaBarWrap = document.getElementById("quotaBarWrap");
const upgradeBtn  = document.getElementById("upgradeBtn");
const toggleBtn   = document.getElementById("toggleBtn");
const statusLine  = document.getElementById("statusLine");

// ── Helpers ───────────────────────────────────────────────────────────────────

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function fetchUsage(token) {
  try {
    const resp = await fetch(`${BACKEND_URL}/usage`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) return null;
    return resp.json();   // { tier, pages_today, daily_limit }
  } catch {
    return null;
  }
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderSignedOut() {
  viewSignout.style.display = "flex";
  viewSignin.style.display  = "none";
}

function renderSignedIn(email, usage, tabEnabled, translated) {
  viewSignout.style.display = "none";
  viewSignin.style.display  = "flex";

  userEmail.textContent = email;

  const tier  = usage?.tier || "free";
  const used  = usage?.pages_today || 0;
  const limit = usage?.daily_limit;   // null = unlimited

  tierBadge.textContent  = tier;
  tierBadge.className    = `tier-badge ${tier}`;

  if (limit != null) {
    quotaText.textContent = `${used} / ${limit} pages today`;
    quotaBar.style.width  = `${Math.min(100, Math.round((used / limit) * 100))}%`;
    quotaBar.style.background = used >= limit ? "#ef4444" : "#2563eb";
    quotaBarWrap.style.display = "block";
    upgradeBtn.style.display   = used >= limit ? "block" : "none";
  } else {
    quotaText.textContent = `${used} pages today`;
    quotaBar.style.width  = "0%";
    quotaBarWrap.style.display = "none";
    upgradeBtn.style.display   = "none";
  }

  // Toggle button
  const quotaExceeded = limit !== null && used >= limit;
  if (quotaExceeded) {
    toggleBtn.disabled    = true;
    toggleBtn.textContent = "Limit reached";
    statusLine.textContent = "Upgrade for more translations";
    statusLine.className  = "status-line warn";
    return;
  }

  toggleBtn.disabled = false;
  if (tabEnabled) {
    toggleBtn.textContent = "Disable Translation";
    toggleBtn.className   = "toggle-btn on";
    statusLine.innerHTML  = `Translated: <span>${translated}</span> image${translated === 1 ? "" : "s"}`;
    statusLine.className  = "status-line";
  } else {
    toggleBtn.textContent = "Enable Translation";
    toggleBtn.className   = "toggle-btn off";
    statusLine.textContent = "Translation is off";
    statusLine.className  = "status-line";
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  // Try silent token refresh
  const token = await chrome.runtime.sendMessage({ type: "GET_AUTH_TOKEN" });
  if (!token) { renderSignedOut(); return; }

  // Get stored email
  const { userInfo } = await chrome.storage.local.get("userInfo");
  const email = userInfo?.email || "";

  // Fetch live usage from backend
  const usage = await fetchUsage(token);

  // Get tab translation state
  const tab = await getActiveTab();
  const state = tab
    ? await chrome.runtime.sendMessage({ type: "GET_STATE", tabId: tab.id })
    : { enabled: false, translated: 0 };

  renderSignedIn(email, usage, state.enabled, state.translated || 0);
}

// ── Sign in ───────────────────────────────────────────────────────────────────

signInBtn.addEventListener("click", async () => {
  signInBtn.disabled = true;
  signInBtn.textContent = "Signing in…";

  const token = await chrome.runtime.sendMessage({ type: "SIGN_IN" });
  if (!token) {
    signInBtn.disabled = false;
    signInBtn.textContent = "Sign in with Google";
    return;
  }

  // Fetch profile to store email locally
  try {
    const resp = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.ok) {
      const profile = await resp.json();
      await chrome.storage.local.set({ userInfo: { email: profile.email } });
    }
  } catch { /* non-fatal */ }

  init();
});

// ── Sign out ──────────────────────────────────────────────────────────────────

signOutBtn.addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "SIGN_OUT" });
  renderSignedOut();
});

// ── Toggle translation ────────────────────────────────────────────────────────

toggleBtn.addEventListener("click", async () => {
  const tab = await getActiveTab();
  if (!tab) return;

  const state = await chrome.runtime.sendMessage({ type: "GET_STATE", tabId: tab.id });
  const turningOn = !state.enabled;

  await chrome.runtime.sendMessage({ type: "SET_ENABLED", tabId: tab.id, enabled: turningOn });

  if (turningOn) {
    // Inject content script if needed
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "PING" });
    } catch {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    }
    await chrome.tabs.sendMessage(tab.id, { type: "TOGGLE_ON" });
  } else {
    try { await chrome.tabs.sendMessage(tab.id, { type: "TOGGLE_OFF" }); } catch { /* ok */ }
    if (confirm("Reload page to restore original images?")) {
      chrome.tabs.reload(tab.id);
    }
  }

  init();
});

// ── Upgrade link ──────────────────────────────────────────────────────────────

upgradeBtn.addEventListener("click", () => {
  chrome.tabs.create({ url: STRIPE_PAYMENT_URL });
});

// ── Listen for messages from content.js ──────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "AUTH_REQUIRED") {
    renderSignedOut();
  } else if (msg.type === "QUOTA_EXCEEDED") {
    statusLine.textContent = msg.detail || "Daily limit reached.";
    statusLine.className = "status-line error";
    upgradeBtn.style.display = "block";
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────────

init();
