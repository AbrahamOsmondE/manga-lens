const BACKEND_URL = "https://api.mangalens.app";

const CONFIG = {
  ocr: { ocr: "48px", ignore_bubble: 5 },
  detector: { detection_size: 1024, unclip_ratio: 2.3 },
  inpainter: { inpainter: "none" },
  render: { renderer: "manga2eng", disable_font_border: false },
  translator: { translator: "gemini", target_lang: "ENG" },
  mask_dilation_offset: 0,
  kernel_size: 1,
};

let isEnabled = false;
const queue = [];
let processing = false;

// ── Auth ──────────────────────────────────────────────────────────────────────

async function getToken() {
  const { authToken } = await chrome.storage.session.get("authToken");
  return authToken || null;
}

// ── Spinner ───────────────────────────────────────────────────────────────────

function createSpinner() {
  const wrapper = document.createElement("div");
  wrapper.style.cssText = `
    position: absolute;
    top: 8px;
    left: 8px;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 999999;
    animation: ml-spin 1s linear infinite;
    pointer-events: none;
  `;
  const label = document.createElement("span");
  label.textContent = "ML";
  label.style.cssText = `
    color: #fff;
    font-size: 11px;
    font-weight: bold;
    font-family: sans-serif;
    letter-spacing: 0.5px;
    user-select: none;
  `;
  wrapper.appendChild(label);

  if (!document.getElementById("ml-spinner-style")) {
    const style = document.createElement("style");
    style.id = "ml-spinner-style";
    style.textContent = `
      @keyframes ml-spin {
        0%   { box-shadow: 0 0 0 2px rgba(255,255,255,0.15), 0 -18px 0 2px rgba(255,255,255,0.8); }
        25%  { box-shadow: 0 0 0 2px rgba(255,255,255,0.15), 18px 0 0 2px rgba(255,255,255,0.8); }
        50%  { box-shadow: 0 0 0 2px rgba(255,255,255,0.15), 0 18px 0 2px rgba(255,255,255,0.8); }
        75%  { box-shadow: 0 0 0 2px rgba(255,255,255,0.15), -18px 0 0 2px rgba(255,255,255,0.8); }
        100% { box-shadow: 0 0 0 2px rgba(255,255,255,0.15), 0 -18px 0 2px rgba(255,255,255,0.8); }
      }
    `;
    document.head.appendChild(style);
  }
  return wrapper;
}

function attachSpinner(img) {
  const parent = img.parentElement;
  if (!parent) return null;
  if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
  const spinner = createSpinner();
  spinner.dataset.mlSpinner = "1";
  parent.insertBefore(spinner, img.nextSibling);
  return spinner;
}

function removeSpinner(img) {
  img.parentElement?.querySelector("[data-ml-spinner]")?.remove();
}

// ── Translation ───────────────────────────────────────────────────────────────

async function translateImage(img) {
  const spinner = attachSpinner(img);
  try {
    const token = await getToken();
    if (!token) {
      // User signed out while queue was running — stop silently
      chrome.runtime.sendMessage({ type: "AUTH_REQUIRED" });
      return;
    }

    const blob = await fetchImageBlob(img);
    const b64 = await blobToDataURI(blob);

    const response = await fetch(`${BACKEND_URL}/translate/image`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({ image: b64, config: CONFIG }),
    });

    if (response.status === 401) {
      chrome.runtime.sendMessage({ type: "AUTH_REQUIRED" });
      return;
    }
    if (response.status === 429) {
      const body = await response.json().catch(() => ({}));
      chrome.runtime.sendMessage({ type: "QUOTA_EXCEEDED", detail: body.detail || "" });
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const resultBlob = await response.blob();
    img.src = URL.createObjectURL(resultBlob);
    img.dataset.mlTranslated = "1";
    chrome.runtime.sendMessage({ type: "INCREMENT_TRANSLATED" });
  } catch (err) {
    console.error("[MangaLens] Translation failed:", err);
  } finally {
    if (spinner) spinner.remove();
    else removeSpinner(img);
  }
}

async function fetchImageBlob(img) {
  if (img.src && img.src.startsWith("http")) {
    const resp = await fetch(img.src);
    return resp.blob();
  }
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

function blobToDataURI(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

// ── Queue ─────────────────────────────────────────────────────────────────────

function enqueue(img) {
  queue.push(img);
  if (!processing) drainQueue();
}

async function drainQueue() {
  processing = true;
  while (queue.length > 0) {
    const img = queue.shift();
    if (!document.contains(img)) continue;
    if (img.dataset.mlTranslated) continue;
    await translateImage(img);
  }
  processing = false;
}

// ── Image detection ───────────────────────────────────────────────────────────

function isMangaImage(img) {
  if (!img.complete || !img.naturalWidth) return false;
  if (img.dataset.mlTranslated || img.dataset.mlQueued) return false;
  return img.naturalWidth > 200 && img.naturalHeight > 300;
}

function processPage() {
  document.querySelectorAll("img").forEach((img) => {
    if (isMangaImage(img)) {
      img.dataset.mlQueued = "1";
      enqueue(img);
    }
  });
}

// ── Message listener ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "TOGGLE_ON") {
    isEnabled = true;
    processPage();
    sendResponse({ ok: true });
  } else if (msg.type === "TOGGLE_OFF") {
    isEnabled = false;
    sendResponse({ ok: true });
  } else if (msg.type === "PING") {
    sendResponse({ ok: true });
  }
  return true;
});
