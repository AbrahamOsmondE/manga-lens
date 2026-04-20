const BACKEND_URL = "https://api.manga-lens.com";

const CONFIG = {
  ocr: { ocr: "48px", ignore_bubble: 0 },
  detector: { detection_size: 1536, unclip_ratio: 2.5 },
  inpainter: { inpainter: "none" },
  render: { renderer: "manga2eng", disable_font_border: false },
  translator: { translator: "gemini", target_lang: "ENG" },
  mask_dilation_offset: 2,
  kernel_size: 3,
};

let isEnabled = false;

// ── Auth ──────────────────────────────────────────────────────────────────────

async function getToken() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ type: "GET_AUTH_TOKEN" }, token => {
      resolve(token || null);
    });
  });
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
  spinner.style.top  = `${img.offsetTop  + 8}px`;
  spinner.style.left = `${img.offsetLeft + 8}px`;
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

    const b64 = await fetchImageDataURI(img);

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

async function fetchImageDataURI(img) {
  // Ask the background SW to fetch the image — it has host_permissions and
  // is exempt from CORS, so cross-origin CDN images (e.g. MangaDex) work.
  if (img.src && img.src.startsWith("http")) {
    const dataURI = await new Promise(resolve =>
      chrome.runtime.sendMessage({ type: "FETCH_IMAGE_DATA", url: img.src }, resolve)
    );
    if (dataURI) return dataURI;
  }
  // Fallback: canvas (same-origin or images with CORS headers)
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (!blob) { reject(new Error("Cannot capture image (cross-origin)")); return; }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    }, "image/jpeg", 0.92);
  });
}

// ── Image detection ───────────────────────────────────────────────────────────

function isMangaImage(img) {
  if (!img.complete || !img.naturalWidth) return false;
  if (img.dataset.mlTranslated || img.dataset.mlQueued) return false;
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  // Must be large enough to be a manga panel
  if (w < 300 || h < 400) return false;
  // Skip wide landscape banners/logos (aspect ratio > 2.5 means wider than tall)
  if (w / h > 2.5) return false;
  return true;
}

function processPage() {
  document.querySelectorAll("img").forEach((img) => {
    if (isMangaImage(img)) {
      img.dataset.mlQueued = "1";
      translateImage(img); // fire concurrently — all spinners appear at once
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
