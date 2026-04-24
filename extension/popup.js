const BACKEND_URL = "https://api.manga-lens.com";

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
const toggleBtn   = document.getElementById("toggleBtn");
const scanBtn     = document.getElementById("scanBtn");
const statusLine  = document.getElementById("statusLine");
const refreshBtn  = document.getElementById("refreshBtn");

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

  const tier  = usage?.tier || "free";   // "free" only if fetchUsage failed; sign out/in to refresh token
  const used  = usage?.pages_today || 0;
  const limit = usage?.daily_limit;     // null = unlimited (paid / admin)

  tierBadge.textContent  = tier;
  tierBadge.className    = `tier-badge ${tier}`;

  if (limit != null) {
    quotaText.textContent = `${used} / ${limit} pages today`;
    quotaBar.style.width  = `${Math.min(100, Math.round((used / limit) * 100))}%`;
    quotaBar.style.background = used >= limit ? "#ef4444" : "#2563eb";
    quotaBarWrap.style.display = "block";
  } else {
    quotaText.textContent = `${used} pages today`;
    quotaBar.style.width  = "0%";
    quotaBarWrap.style.display = "none";
  }

  // Toggle button
  const quotaExceeded = limit !== null && used >= limit;
  if (quotaExceeded) {
    toggleBtn.disabled    = true;
    toggleBtn.textContent = "Limit reached";
    scanBtn.style.display = "none";
    statusLine.textContent = "Daily limit reached";
    statusLine.className  = "status-line warn";
    return;
  }

  toggleBtn.disabled = false;
  if (tabEnabled) {
    toggleBtn.textContent  = "Disable Translation";
    toggleBtn.className    = "toggle-btn on";
    scanBtn.style.display  = "block";
    scanBtn.disabled       = false;
    scanBtn.textContent    = "Scan for untranslated images";
    statusLine.innerHTML   = `Translated: <span>${translated}</span> image${translated === 1 ? "" : "s"}`;
    statusLine.className   = "status-line";
  } else {
    toggleBtn.textContent  = "Enable Translation";
    toggleBtn.className    = "toggle-btn off";
    scanBtn.style.display  = "none";
    statusLine.textContent = "Translation is off";
    statusLine.className   = "status-line";
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

  // Fetch live usage; fall back to cached value if network is unavailable (e.g. after sleep)
  let usage = await fetchUsage(token);
  if (usage) {
    await chrome.storage.local.set({ cachedUsage: usage });
  } else {
    const { cachedUsage } = await chrome.storage.local.get("cachedUsage");
    usage = cachedUsage || null;
  }

  const tab = await getActiveTab();

  // Ask the content script directly for enabled state — it persists as long as
  // the tab lives, so it survives the background service worker being killed on sleep.
  let tabEnabled = false;
  let translated  = 0;
  if (tab) {
    try {
      const cs = await chrome.tabs.sendMessage(tab.id, { type: "GET_IS_ENABLED" });
      tabEnabled = cs?.enabled ?? false;
    } catch { /* content script not injected on this tab */ }

    // Translation count still comes from SW (best-effort; resets if SW was killed)
    const swState = await chrome.runtime.sendMessage({ type: "GET_STATE", tabId: tab.id });
    translated = swState?.translated || 0;

    // If content says enabled but SW lost track, re-sync so subsequent messages work
    if (tabEnabled && !swState?.enabled) {
      chrome.runtime.sendMessage({ type: "SET_ENABLED", tabId: tab.id, enabled: true });
    }
  }

  renderSignedIn(email, usage, tabEnabled, translated);
}

// ── Refresh account ───────────────────────────────────────────────────────────

refreshBtn.addEventListener("click", async () => {
  refreshBtn.classList.add("spinning");
  // Force a fresh fetch — bypass the cache so we always hit the server
  await chrome.storage.local.remove("cachedUsage");
  await init();
  refreshBtn.classList.remove("spinning");
});

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
  await chrome.storage.local.remove("cachedUsage");
  renderSignedOut();
});

// ── Toggle translation ────────────────────────────────────────────────────────

toggleBtn.addEventListener("click", async () => {
  const tab = await getActiveTab();
  if (!tab) return;

  // Ask content script directly — SW state may be stale after a sleep/restart
  let currentlyEnabled = false;
  try {
    const cs = await chrome.tabs.sendMessage(tab.id, { type: "GET_IS_ENABLED" });
    currentlyEnabled = cs?.enabled ?? false;
  } catch {
    // Content script not injected — treat as disabled
  }
  const turningOn = !currentlyEnabled;

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

// ── Scan for untranslated ─────────────────────────────────────────────────────

scanBtn.addEventListener("click", async () => {
  const tab = await getActiveTab();
  if (!tab) return;
  scanBtn.disabled    = true;
  scanBtn.textContent = "Scanning…";
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "SCAN_PAGE" });
  } catch { /* content script not injected yet — ignore */ }
  // Brief delay so user sees feedback, then refresh count
  setTimeout(init, 800);
});

// ── Listen for messages from content.js ──────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "AUTH_REQUIRED") {
    renderSignedOut();
  } else if (msg.type === "QUOTA_EXCEEDED") {
    statusLine.textContent = msg.detail || "Daily limit reached.";
    statusLine.className = "status-line error";
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────────

init();
