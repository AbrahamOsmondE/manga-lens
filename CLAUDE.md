# MangaLens — Master Build Document

## Project Overview

MangaLens is a system that translates Japanese manga pages into English in real time via a Chrome extension. The user visits a manga website, toggles the extension on, and every manga panel on the page is sent to a backend translation service and replaced with an English-rendered version — speech bubbles and all.

---

## System Architecture

```
Chrome Extension (content script)
        │
        │  POST /translate/image  (X-API-Key header)
        ▼
Auth Proxy  (port 8080, public)
        │
        │  internal only
        ▼
manga-image-translator  (port 5003, internal)
        │
        │  Gemini API (translation)
        ▼
  Translated PNG returned
```

The auth proxy is the only publicly exposed service. The translator is internal.

---

## Repository Structure

```
manga-lens/
├── CLAUDE.md                        ← this file
├── README.md
├── .gitignore
├── reference/                       ← visual spec (do not modify)
│   ├── image.png                    ← original manga page (INPUT)
│   └── reference.png                ← target output (TARGET)
├── backend/
│   ├── docker-compose.yml           ← runs translator + auth proxy
│   ├── .env.example
│   └── proxy/                       ← Phase 2: auth proxy service
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile
├── extension/                       ← Phase 2: Chrome extension
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── popup.html
│   ├── popup.js
│   └── icons/
├── tests/
│   ├── test_translation.py
│   ├── fixtures/
│   │   └── sample.jpg               ← not committed
│   └── output/
│       └── translated.jpg           ← not committed
└── website/                         ← Phase 3: companion website
```

---

## Phase 1 — Local Translation Pipeline ✅ COMPLETE

Phase 1 is done. The pipeline works locally via Docker. Do not re-implement it.

### What Was Built

- `manga-image-translator` Docker container running locally
- Integration test at `tests/test_translation.py` — passes in ~6.5s
- Translator: Gemini 2.5 Flash Lite
- Renderer: manga2eng (text sized to fit speech bubble)
- Inpainter: none (tight mask, dil=0, ks=1)

### Verified Config (locked — do not change without good reason)

```json
{
  "ocr": { "ocr": "48px", "ignore_bubble": 5 },
  "detector": { "detection_size": 1024, "unclip_ratio": 2.3 },
  "inpainter": { "inpainter": "none" },
  "render": { "renderer": "manga2eng", "disable_font_border": false },
  "translator": { "translator": "gemini", "target_lang": "ENG" },
  "mask_dilation_offset": 0,
  "kernel_size": 1
}
```

### Verified docker-compose.yml (current — reflects actual image CLI)

The Docker image entrypoint changed from the original spec. The correct working command is:

```yaml
version: '3.8'
services:
  manga-translator:
    image: zyddnys/manga-image-translator:main
    entrypoint: ["python", "/app/server/main.py"]
    command: ["--host=0.0.0.0", "--port=5003", "--nonce=None"]
    working_dir: /app
    ports:
      - "5003:5003"
      - "5004:5004"
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - GEMINI_MODEL=gemini-2.5-flash-lite
    ipc: host
    restart: unless-stopped
```

Notes:
- `server/main.py` auto-spawns a worker instance on port+1 (5004)
- Both ports must be exposed
- `--nonce=None` disables the internal nonce check (auth is handled by the proxy)
- The image is ~15GB — first pull is slow

### Verified API Usage

The API accepts JSON, not multipart form data:

```python
import base64, requests

with open("page.jpg", "rb") as f:
    image_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

response = requests.post(
    "http://localhost:5003/translate/image",
    json={"image": image_b64, "config": CONFIG},
    timeout=300
)
# response.content is a PNG image
```

Endpoint used: `POST /translate/image` — returns `image/png`  
Do NOT use `/translate/bytes` — it returns a custom binary format, not a plain image.

### Batch Translation

The server's `/translate/batch/images` endpoint has a bug (upstream — `fetch_data()` missing config arg). Use sequential single-image calls instead — the server queues anyway so there is no performance difference:

```python
def translate_batch(image_paths, output_dir, config=CONFIG, backend_url=BACKEND_URL):
    # see tests/test_translation.py — translate_batch() is already implemented
```

### Known API Facts

- OpenAPI docs: `http://localhost:5003/docs`
- `/translate/batch/images` — broken upstream (bug in sent_batch)
- Models already cached in image: detection, OCR, inpainting (lama, aot)
- `inpainter: lama_large` causes bubble transparency on this manga style — do not use
- `inpainter: original` leaves Japanese text visible — useful for debugging only

---

## Phase 2 — Chrome Extension + GCP Deployment

**Begin only after Phase 1 is complete. It is complete.**

Phase 2 has two parallel components: the auth proxy + GCP deployment, and the Chrome extension. Build them in the order listed.

---

### Phase 2A — Auth Proxy (`backend/proxy/`)

The manga-image-translator has no auth. The proxy sits in front of it and validates every request.

#### What the proxy does

- Listens on port `8080` (the only publicly exposed port)
- Requires `X-API-Key: <key>` header on every request
- Returns `401` if the key is missing or wrong
- Forwards valid requests to `http://localhost:5003/translate/image`
- Streams the image response back to the client
- Logs every request (timestamp, IP, status, duration)

#### Implementation — `backend/proxy/main.py`

Use FastAPI + httpx. Keep it under 100 lines. Do not add features not listed here.

```python
# Minimal shape — implement this exactly
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import StreamingResponse
import httpx, os, time, logging

app = FastAPI()
API_KEY = os.environ["MANGA_API_KEY"]
TRANSLATOR_URL = os.environ.get("TRANSLATOR_URL", "http://localhost:5003")

@app.post("/translate/image")
async def translate(request: Request, x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")
    body = await request.body()
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{TRANSLATOR_URL}/translate/image", content=body,
                                  headers={"Content-Type": "application/json"})
    return StreamingResponse(iter([resp.content]), media_type=resp.headers["content-type"])
```

#### `backend/proxy/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

#### `backend/proxy/requirements.txt`

```
fastapi
uvicorn
httpx
```

#### Updated `docker-compose.yml` (Phase 2A)

Add the proxy service alongside the translator:

```yaml
version: '3.8'
services:
  manga-translator:
    image: zyddnys/manga-image-translator:main
    entrypoint: ["python", "/app/server/main.py"]
    command: ["--host=0.0.0.0", "--port=5003", "--nonce=None"]
    working_dir: /app
    ports:
      - "5003:5003"
      - "5004:5004"
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - GEMINI_MODEL=gemini-2.5-flash-lite
    ipc: host
    restart: unless-stopped

  proxy:
    build: ./proxy
    ports:
      - "8080:8080"
    environment:
      - MANGA_API_KEY=${MANGA_API_KEY}
      - TRANSLATOR_URL=http://manga-translator:5003
    depends_on:
      - manga-translator
    restart: unless-stopped
```

#### `.env.example` (updated)

```
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
MANGA_API_KEY=your_secret_api_key_here   # shared secret with the Chrome extension
```

#### Phase 2A complete when

- `docker-compose up` starts both services
- `curl -H "X-API-Key: wrong" http://localhost:8080/translate/image` returns 401
- `pytest tests/test_translation.py -v -s` passes with `BACKEND_URL=http://localhost:8080` and `X-API-Key` header

---

### Phase 2B — GCP Deployment

**Deploy Phase 2A to GCP before building the Chrome extension.**  
The extension needs a real public URL to point at.

#### Instance Spec

| Setting | Value |
|---|---|
| Machine type | `e2-medium` (2 vCPU, 4GB RAM) — do not use e2-small, OOM risk |
| OS | Ubuntu 22.04 LTS |
| Boot disk | 50GB (Docker image is ~15GB, leave room for logs) |
| Region | Choose closest to target users |
| Cost | ~$25/month |

Do not use Terraform — manual GCP Console setup is sufficient for v1.

#### Setup Steps (SSH into GCE instance)

```bash
# 1. Install Docker + Compose
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo usermod -aG docker $USER
newgrp docker

# 2. Copy files from local machine
gcloud compute scp backend/docker-compose.yml INSTANCE_NAME:~/
gcloud compute scp backend/.env INSTANCE_NAME:~/          # never commit .env
gcloud compute scp -r backend/proxy INSTANCE_NAME:~/proxy/

# 3. Start
docker-compose up -d

# 4. Firewall — GCP Console → VPC Network → Firewall
# Allow TCP 8080 from 0.0.0.0/0 (proxy — public)
# Do NOT expose 5003 or 5004 publicly (translator — internal only)
```

#### Smoke Test Against Live Instance

```bash
BACKEND_URL=http://<GCE_EXTERNAL_IP>:8080 pytest tests/test_translation.py -v -s
# Add X-API-Key header to the test first
```

#### Phase 2B complete when

- Proxy is reachable at `http://<GCE_IP>:8080`
- Port 5003 is NOT reachable from outside
- Integration test passes against the live IP
- Translation time is under 15s on the live instance

---

### Phase 2C — Chrome Extension (`extension/`)

Build this after Phase 2B. The extension needs a real deployed backend URL.

#### What the extension does

1. Adds a toggle button to the browser toolbar (popup)
2. When toggled ON for a tab:
   - Content script scans the page for manga image elements (`<img>` tags)
   - For each image: overlays a custom spinner in the top-left corner of the image
   - Encodes the image as base64 and POSTs to the backend
   - On response: replaces the original `<img>` src with the translated PNG (as a blob URL)
   - Removes the spinner
3. When toggled OFF: reloads the page to restore originals

#### File structure

```
extension/
├── manifest.json        ← Manifest V3
├── background.js        ← service worker, stores toggle state per tab
├── content.js           ← injected into pages, handles image detection + replacement
├── popup.html           ← toggle UI
├── popup.js             ← reads/writes toggle state via chrome.storage
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

#### `manifest.json`

```json
{
  "manifest_version": 3,
  "name": "MangaLens",
  "version": "1.0.0",
  "description": "Translate Japanese manga pages to English in your browser",
  "permissions": ["activeTab", "storage", "scripting"],
  "host_permissions": ["<all_urls>"],
  "background": {
    "service_worker": "background.js"
  },
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    }
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content.js"]
    }
  ]
}
```

#### `content.js` — core logic

Key behaviours to implement:

1. **Image detection**: target `<img>` elements with `naturalWidth > 200 && naturalHeight > 300` (filters out icons/thumbnails). Do not target SVGs or background images.

2. **Spinner**: inject an absolutely-positioned `<div>` overlay on the top-left of each image being processed. The spinner should be visually unique — a circular CSS animation with the MangaLens icon or initials "ML". Do not use a plain browser spinner.
   ```
   Position: absolute, top: 8px, left: 8px
   Size: 40x40px
   Style: dark semi-transparent circle, white "ML" text, CSS rotate animation
   ```

3. **Translation request**: POST to `BACKEND_URL/translate/image` with:
   - `Content-Type: application/json`
   - `X-API-Key: <MANGA_API_KEY>`  
   - Body: `{ "image": "data:image/jpeg;base64,...", "config": CONFIG }`

4. **Image replacement**: on success, create `URL.createObjectURL(blob)` and set it as the `<img>` src. Do not modify the DOM structure — only the src.

5. **Error handling**: if translation fails, remove the spinner and leave the original image. Log the error to console. Do not show alerts.

6. **Config** (hardcoded in content.js — matches Phase 1 verified config):
   ```js
   const CONFIG = {
     ocr: { ocr: "48px", ignore_bubble: 5 },
     detector: { detection_size: 1024, unclip_ratio: 2.3 },
     inpainter: { inpainter: "none" },
     render: { renderer: "manga2eng", disable_font_border: false },
     translator: { translator: "gemini", target_lang: "ENG" },
     mask_dilation_offset: 0,
     kernel_size: 1
   };
   ```

7. **Concurrency**: translate images one at a time (the server is single-worker). Queue them and process sequentially.

#### `popup.html` / `popup.js`

Simple toggle UI:
- Show current state: ON / OFF for the active tab
- A single large toggle button
- A status line showing how many images have been translated on the current page
- When toggled ON: send a message to content.js to begin translation
- When toggled OFF: send a message to content.js to stop (and offer to reload)

#### Backend URL + API key

Hardcode these in `content.js` for Phase 2. Do not build a settings UI yet (that is Phase 3):

```js
const BACKEND_URL = "http://<GCE_EXTERNAL_IP>:8080";  // set after Phase 2B
const MANGA_API_KEY = "<your_key>";                    // matches backend .env
```

#### Phase 2C complete when

1. Extension loads in Chrome (`chrome://extensions` → Load unpacked)
2. Visiting a manga page and toggling ON causes:
   - Spinner appears on each manga-sized image
   - Image is replaced with the translated version
   - Spinner is removed after replacement
3. Translation quality matches `reference/reference.png`
4. No console errors on happy path

---

### Definition of Done — Phase 2

All of the following must be true:

1. Auth proxy rejects requests without the API key
2. GCE instance is running and publicly reachable on port 8080
3. Port 5003 is NOT publicly accessible
4. Chrome extension translates a real manga page end-to-end
5. Spinner appears during processing and disappears after
6. A human visually verifies the translated page in a real browser

---

## Phase 3 — Companion Website

**Do not begin Phase 3 until Phase 2 is fully complete and verified.**

Phase 3 adds a companion website accessible from a button inside the Chrome extension popup.

### Pages

| Page | Description |
|---|---|
| Home | Product landing page — what MangaLens is, screenshots, install button |
| Login | User login / sign-up. Required before Donate page |
| Contact | Simple contact form (name, email, message) |
| Donate | Donation page with server lifetime progress bar (see below) |

### Donate Page — Server Lifetime Bar

The donate page shows a visual bar indicating how long the server can keep running based on cumulative donations. Logic:

- Server costs ~$25/month (GCE e2-medium)
- Each donation adds to a running total stored in a database
- The bar shows: `total donated / (months_to_fund × $25)` as a percentage
- Display: "Server funded for X more months" below the bar
- Payments via Stripe (donation = one-time payment, any amount)
- The bar updates in real time (or on page load — polling is fine for v1)

### Chrome Extension Integration (Phase 3 addition to popup)

Add a single "Open Website" button to `popup.html` that opens the companion website in a new tab. That is the only change to the extension in Phase 3.

### Tech Stack for Website

Keep it simple — do not over-engineer:
- Framework: Next.js (React) — single repo under `website/`
- Auth: NextAuth.js with email/password (no OAuth required for Phase 3)
- Payments: Stripe Checkout (one-time donations)
- Database: PostgreSQL on Cloud SQL (GCP), or a simple hosted option like Supabase
- Hosting: Vercel (free tier is fine for a low-traffic companion site)

### Definition of Done — Phase 3

1. All four pages are live and publicly accessible
2. A user can navigate from the Chrome extension popup to the website
3. A user can make a donation and see the server lifetime bar update
4. Login gates the Donate page (unauthenticated users are redirected to Login)

---

## Key Constraints — Do Not Violate

- Never expose port 5003 or 5004 publicly — only the proxy (8080) is public
- Never commit `.env` to git
- `inpainter: lama_large` removes bubble backgrounds on this manga style — do not use
- `inpainter: original` leaves Japanese text visible — debug only
- `/translate/batch/images` is broken upstream — use sequential single-image calls
- The Docker image is ~15GB — first pull is slow, this is expected
- Lambda is not viable — Docker image exceeds Lambda's 10GB container image limit
- No GPU required — do not add GPU requirements
- Do not build Phase 3 features during Phase 2
- The Chrome extension API key is hardcoded for Phase 2 — do not build a settings UI until Phase 3

---

## Environment Variables Reference

| Variable | Used by | Description |
|---|---|---|
| `GEMINI_API_KEY` | translator container | Gemini API key |
| `GEMINI_MODEL` | translator container | Model name, default `gemini-2.5-flash-lite` |
| `MANGA_API_KEY` | proxy + extension | Shared secret — extension sends this, proxy validates it |

---

## When You Need User Input

1. **GEMINI_API_KEY** — Google AI Studio → free tier available
2. **MANGA_API_KEY** — generate any strong random string (e.g. `openssl rand -hex 32`)
3. **GCP account** — needed for Phase 2B GCE instance
4. **Stripe account** — needed for Phase 3 Donate page
5. **Domain name** — needed for Phase 3 website (optional for v1, Vercel subdomain works)
