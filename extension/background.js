// ── Auth helpers ──────────────────────────────────────────────────────────────

/**
 * Get a fresh Google ID token silently. Returns null if not signed in.
 * Pass interactive=true to show the consent screen when needed.
 */
async function getIdToken(interactive = false) {
  return new Promise((resolve) => {
    chrome.identity.getAuthToken({ interactive }, async (accessToken) => {
      if (chrome.runtime.lastError || !accessToken) {
        resolve(null);
        return;
      }
      // Exchange access token for ID token via userinfo endpoint
      try {
        const resp = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!resp.ok) { resolve(null); return; }
        // The access token itself is what we send to our backend's tokeninfo check.
        // Store it in session storage (cleared when browser closes).
        await chrome.storage.session.set({ authToken: accessToken });
        resolve(accessToken);
      } catch {
        resolve(null);
      }
    });
  });
}

/**
 * Sign out: revoke the token and clear session storage.
 */
async function signOut() {
  const { authToken } = await chrome.storage.session.get("authToken");
  if (authToken) {
    chrome.identity.removeCachedAuthToken({ token: authToken });
    await fetch(`https://accounts.google.com/o/oauth2/revoke?token=${authToken}`);
  }
  await chrome.storage.session.remove("authToken");
  await chrome.storage.local.remove("userInfo");
}

// ── Per-tab state ─────────────────────────────────────────────────────────────

const tabState = {};

// ── Message handler ───────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = msg.tabId || sender.tab?.id;

  if (msg.type === "GET_AUTH_TOKEN") {
    getIdToken(false).then(sendResponse);
    return true;
  }

  if (msg.type === "FETCH_IMAGE_DATA") {
    fetch(msg.url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.blob();
      })
      .then(async blob => {
        // Normalise to JPEG — handles WebP, data-saver PNGs, etc.
        // OffscreenCanvas is available in service workers (Chrome 69+).
        try {
          const bitmap = await createImageBitmap(blob);
          const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
          canvas.getContext("2d").drawImage(bitmap, 0, 0);
          return canvas.convertToBlob({ type: "image/jpeg", quality: 0.95 });
        } catch {
          return blob; // fallback: send as-is
        }
      })
      .then(blob => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      }))
      .then(sendResponse)
      .catch(() => sendResponse(null));
    return true;
  }

  if (msg.type === "SIGN_IN") {
    getIdToken(true).then(sendResponse);
    return true;
  }

  if (msg.type === "SIGN_OUT") {
    signOut().then(() => sendResponse({ ok: true }));
    return true;
  }

  if (!tabId) return;

  if (msg.type === "GET_STATE") {
    sendResponse(tabState[tabId] || { enabled: false, translated: 0 });
  } else if (msg.type === "SET_ENABLED") {
    tabState[tabId] = { ...(tabState[tabId] || { translated: 0 }), enabled: msg.enabled };
    sendResponse({ ok: true });
  } else if (msg.type === "INCREMENT_TRANSLATED") {
    if (!tabState[tabId]) tabState[tabId] = { enabled: true, translated: 0 };
    tabState[tabId].translated += 1;
    sendResponse({ translated: tabState[tabId].translated });
  }
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  delete tabState[tabId];
});
