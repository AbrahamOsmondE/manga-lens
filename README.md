# MangaLens

Translates Japanese manga pages to English in real time via a Chrome extension. Manga panels are sent to a backend translation service and returned with English text rendered inside the original speech bubbles.

## Architecture

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

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [GNU Make](https://www.gnu.org/software/make/)
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) (for GCP deployment)
- A [Gemini API key](https://aistudio.google.com/) (free tier available)
- Python 3.10+ with `pytest` and `requests`

## Local development

### 1. Configure environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and fill in GEMINI_API_KEY
```

### 2. Start the local translator

```bash
make up
```

The first run downloads the `zyddnys/manga-image-translator` image (~15 GB) — expected, happens once only.
The server is ready when you see `manga-translator | INFO: Application startup complete`.

### 3. Run the integration test

Place a Japanese manga page at `tests/fixtures/sample.jpg`, then:

```bash
make test
```

Open `tests/output/translated.jpg` to visually verify English text inside the speech bubbles.
Expected runtime: ~6–7 seconds per page.

### 4. Stop the backend

```bash
make down
```

### Batch translation

```python
from tests.test_translation import translate_batch

translate_batch(
    image_paths=["page1.jpg", "page2.jpg", "page3.jpg"],
    output_dir="tests/output/batch"
)
```

Output files are saved as `translated_001.png`, `translated_002.png`, etc.

---

## GCP Deployment

### 1. Generate an API key

```bash
make generate-key
# Copy the output into backend/.env as MANGA_API_KEY
```

### 2. Create a GCE VM (manual — GCP Console)

- **Machine type**: `e2-medium` (2 vCPU, 4 GB RAM)
- **OS**: Ubuntu 22.04 LTS
- **Boot disk**: 50 GB standard persistent disk
- **Firewall**: allow TCP `8080` inbound; do **not** expose `5003` or `5004`

After creation, note the **External IP** and add it to `backend/.env` as `GCE_IP`.
Also set `GCE_INSTANCE` and `GCE_ZONE` in `backend/.env` to match your VM.

### 3. Install Docker on the instance (run once)

```bash
make install-docker
```

Log out and SSH back in (or run `newgrp docker`) before the next step.

### 4. Deploy

```bash
make deploy
```

Copies `docker-compose.yml`, `.env`, and `backend/proxy/` to the instance and starts the containers.
Follow startup with:

```bash
make logs-remote
```

### 5. Verify the deployment

```bash
make smoke-test
```

Checks:
- `401` returned for a wrong API key
- Valid key reaches the proxy
- Port `5003` is not publicly reachable

### 6. Run the integration test against the live backend

```bash
make test-remote
```

Target: passes in under 15 seconds.

### Other useful commands

| Command | Description |
|---|---|
| `make ssh` | SSH into the GCE instance |
| `make logs-remote` | Follow live container logs |
| `make check-ports` | Quick manual port reachability check |

---

## Chrome Extension

1. Fill in `extension/content.js` lines 1–2 with your `GCE_IP` and `MANGA_API_KEY`
2. Open `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select the `extension/` folder
3. Visit a manga page, click the MangaLens icon, and toggle **Enable Translation**

---

## Translation config

The verified config used in all tests (locked in `tests/test_translation.py`):

| Setting | Value | Notes |
|---|---|---|
| OCR | `48px` | Fixed-size OCR pass |
| Detector | `detection_size: 1024`, `unclip_ratio: 2.3` | 2.3× box inflation gives best bubble coverage |
| Inpainter | `none` | Fills detected text area with white before re-rendering |
| Renderer | `manga2eng` | Sizes English text to fit the original bubble |
| Translator | `gemini` / `ENG` | Gemini 2.5 Flash Lite via `GEMINI_MODEL` env var |
| Mask dilation | `0` | Keeps white fill as tight as possible |
| Kernel size | `1` | Minimal smoothing on mask edges |
