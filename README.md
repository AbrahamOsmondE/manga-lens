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
- A [Gemini API key](https://aistudio.google.com/) (free tier available)
- Python 3.10+ and `pytest` / `requests` (for the integration test)

## Running the backend

1. Copy the example env file and fill in your keys:
   ```bash
   cp backend/.env.example backend/.env
   # Edit backend/.env and set GEMINI_API_KEY
   ```

2. Start the translator:
   ```bash
   cd backend
   docker-compose up
   ```

   The first run downloads the `zyddnys/manga-image-translator` image (~15 GB) — this is expected and only happens once. The server is ready when you see:
   ```
   manga-translator  | INFO:     Application startup complete.
   ```

## Running the integration test

The test sends a real manga page to the running translator and saves the translated output to `tests/output/translated.jpg`.

1. Place a Japanese manga page at `tests/fixtures/sample.jpg` (any `.jpg` manga scan works).

2. With the backend running, execute:
   ```bash
   pytest tests/test_translation.py -v -s
   ```

3. Open `tests/output/translated.jpg` to visually verify English text appears inside the speech bubbles. Expected runtime is ~6–7 seconds per page.

### Running against a remote backend

Set `BACKEND_URL` to point at any deployed instance:

```bash
BACKEND_URL=http://<your-server-ip>:8080 pytest tests/test_translation.py -v -s
```

### Batch translation

Use the `translate_batch` helper directly from Python:

```python
from tests.test_translation import translate_batch

translate_batch(
    image_paths=["page1.jpg", "page2.jpg", "page3.jpg"],
    output_dir="tests/output/batch"
)
```

Pages are translated sequentially (the server is single-worker). Output files are saved as `translated_001.png`, `translated_002.png`, etc.

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
