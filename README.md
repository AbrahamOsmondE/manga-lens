# MangaLens

Translates manga pages to English in real time via a Chrome extension. Visit any manga reading site, toggle the extension on, and every panel is sent to a backend translation service — speech bubbles are replaced with English text in place.

Supports Japanese and Korean manga.

> **Disclaimer:** Translation quality is limited. OCR and machine translation work best on clean, high-resolution panels with standard fonts. Handwritten text, stylised lettering, and small or overlapping bubbles may produce inaccurate or missing translations. Results are intended as a reading aid, not a polished translation.

---

## Architecture

```
Chrome Extension (Manifest V3)
        │
        │  POST https://api.manga-lens.com/translate/image
        │  Authorization: Bearer <google_access_token>
        ▼
nginx  (port 443, TLS termination — GCE instance)
        │
        ▼
Auth Proxy  (port 8080, internal — FastAPI)
        │  1. Verify Google OAuth token
        │  2. Upsert user in Supabase
        │  3. Track daily usage
        ▼
Translator  (port 5003, internal)
        │  Google Vision API → OCR
        │  Google Translate  → text
        │  freetype          → typeset English into bubbles
        ▼
  Translated image returned to extension

Supabase (PostgreSQL) ← proxy reads/writes users, daily_usage
```

Only port 443 is publicly exposed. Ports 8080 and 5003 are internal only.

---

## Repository structure

```
manga-lens/
├── backend/
│   ├── docker-compose.yml     — runs translator + auth proxy
│   ├── .env.example
│   ├── proxy/                 — FastAPI auth proxy
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── translator/            — lightweight Vision API translator (~150 MB)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── nginx/
│       └── mangalens.conf
├── extension/                 — Chrome extension (Manifest V3)
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── popup.html
│   ├── popup.js
│   └── icons/
├── bubble_typeset/            — OCR + typesetting pipeline (used by translator)
└── website/                   — Static landing page (manga-lens.com)
```

---

## Local development

### Prerequisites

- Docker and Docker Compose
- A [Google Cloud](https://console.cloud.google.com) project with **Cloud Vision API** enabled
- A [Google OAuth 2.0 client ID](https://console.cloud.google.com/apis/credentials) (Chrome Extension type)
- A [Supabase](https://supabase.com) project (free tier)

### 1. Configure environment

```bash
cp backend/.env.example backend/.env
# Fill in GOOGLE_VISION_API_KEY, GOOGLE_CLIENT_ID, DATABASE_URL
```

### 2. Start the services

```bash
make up
```

Both the translator and auth proxy start. Ready when you see:
```
translator_1  | INFO: Application startup complete.
proxy_1       | INFO: Application startup complete.
```

### 3. Stop

```bash
make down
```

---

## Deployment (GCE)

The production stack runs on a single GCE `e2-small` instance with nginx fronting both services.

### Instance setup (once)

```bash
make install-docker   # installs Docker on the remote instance
```

### Deploy

```bash
make deploy           # rsync code + .env, restart containers
make logs-remote      # follow live logs
```

### nginx setup (once, on the instance)

```bash
sudo cp backend/nginx/mangalens.conf /etc/nginx/sites-available/mangalens
sudo ln -s /etc/nginx/sites-available/mangalens /etc/nginx/sites-enabled/mangalens
sudo mkdir -p /var/www/manga-lens.com
sudo cp ~/manga-lens/website/* /var/www/manga-lens.com/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.manga-lens.com -d manga-lens.com -d www.manga-lens.com
```

Certbot adds SSL certificates and auto-renews via systemd timer.

---

## Chrome Extension

Available on the [Chrome Web Store](https://chromewebstore.google.com/detail/mangalens/oddcfdifonkninodiokblpheefnkmaig).

To load unpacked for development:

1. Open `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select the `extension/` folder
2. Sign in with Google via the popup
3. Visit any manga page and toggle **Enable Translation**

---

## Environment variables

| Variable | Service | Description |
|---|---|---|
| `GOOGLE_VISION_API_KEY` | translator | Google Cloud Vision API key for OCR |
| `GOOGLE_CLIENT_ID` | proxy + extension | OAuth client ID from GCP Console |
| `DATABASE_URL` | proxy | Supabase postgres connection string |
| `GCE_IP` | Makefile | External IP of the GCE instance |
| `GCE_INSTANCE` | Makefile | VM instance name |
| `GCE_ZONE` | Makefile | GCP zone (e.g. `asia-southeast1-b`) |
