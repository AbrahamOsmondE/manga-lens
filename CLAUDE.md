# MangaLens — Master Build Document

## Project Overview

MangaLens is a system that translates Japanese manga pages into English in real time via a Chrome extension. The user visits a manga website, toggles the extension on, and every manga panel on the page is sent to a backend translation service and replaced with an English-rendered version — speech bubbles and all.

---

## System Architecture

```
Chrome Extension (Manifest V3)
        │
        │  POST https://api.mangalens.app/translate/image
        │  Authorization: Bearer <google_id_token>
        ▼
nginx (port 443, TLS termination)
        │
        ▼
Auth Proxy (port 8080, internal)
        │  verifies Google token → checks quota → rate limits
        ▼
manga-image-translator (port 5003, internal)
        │
        │  Gemini API (translation)
        ▼
  Translated PNG returned

Supabase (PostgreSQL) ← proxy reads/writes users, daily_usage, subscriptions
Stripe               ← webhook updates subscription tier in DB
```

nginx is the only publicly exposed port (443/80). Ports 8080, 5003, 5004 are internal.

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

## Phase 2 — Chrome Extension + Auth Backend + Deployment

**Begin only after Phase 1 is complete. It is complete.**

Phase 2 is the full production system: a secure backend with Google OAuth, per-user rate limiting, Stripe subscriptions, a domain, HTTPS, and a GitHub Actions deployment pipeline. Build in the order listed.

---

### System Architecture (Phase 2)

```
Chrome Extension (Manifest V3)
        │
        │  POST https://api.mangalens.app/translate/image
        │  Authorization: Bearer <google_id_token>
        ▼
Auth Proxy  (port 443 via nginx, GCE instance)
        │  1. Verify Google ID token with Google's public keys
        │  2. Identify user — create record if first visit
        │  3. Check daily quota (free: 50/day, paid: unlimited)
        │  4. Enforce per-IP + per-user rate limits
        │  5. Increment usage counter in DB
        ▼
manga-image-translator  (port 5003, internal only)
        ▼
Translated PNG returned to extension
```

No hardcoded secrets in the extension. Every request is tied to a verified Google identity.

---

### Phase 2A — Backend: Auth, Rate Limiting, Database

#### Why Google OAuth instead of a hardcoded key

A hardcoded API key embedded in a Chrome extension is visible to anyone who installs it (extensions can be unpacked and inspected). Google OAuth tokens:
- Are user-specific and short-lived (~1 hour)
- Cannot be obtained without a real Google login
- Carry a verifiable identity the backend can tie to a subscription
- Are bound to a specific OAuth client ID — tokens issued for other apps are rejected

#### Database schema (Supabase / PostgreSQL)

```sql
-- One row per Google identity
CREATE TABLE users (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  google_id    text UNIQUE NOT NULL,
  email        text UNIQUE NOT NULL,
  tier         text NOT NULL DEFAULT 'free',   -- 'free' | 'paid'
  created_at   timestamptz DEFAULT now()
);

-- Daily usage — one row per user per UTC day
CREATE TABLE daily_usage (
  user_id      uuid REFERENCES users(id),
  day          date NOT NULL,                  -- CURRENT_DATE (UTC)
  page_count   int  NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

-- Stripe subscription records
CREATE TABLE subscriptions (
  id                   text PRIMARY KEY,       -- Stripe subscription ID
  user_id              uuid REFERENCES users(id),
  status               text NOT NULL,          -- 'active' | 'canceled' | 'past_due'
  current_period_end   timestamptz,
  updated_at           timestamptz DEFAULT now()
);
```

#### Proxy logic — `backend/proxy/main.py`

Stack: FastAPI + httpx + asyncpg + slowapi (rate limiting).

Key behaviours:

1. **Token verification**: call `https://oauth2.googleapis.com/tokeninfo?id_token=<token>`. Verify:
   - `aud` claim matches `GOOGLE_CLIENT_ID` env var (prevents tokens from other apps)
   - `exp` is in the future (not expired)
   - Response HTTP 200
2. **User upsert**: `INSERT INTO users ... ON CONFLICT (google_id) DO UPDATE` — creates on first visit, returns existing on subsequent visits.
3. **Quota check**:
   ```python
   DAILY_LIMIT = {"free": 50, "paid": None}

   # Atomically increment and read
   row = await db.fetchrow("""
     INSERT INTO daily_usage (user_id, day, page_count)
     VALUES ($1, CURRENT_DATE, 1)
     ON CONFLICT (user_id, day)
     DO UPDATE SET page_count = daily_usage.page_count + 1
     RETURNING page_count
   """, user_id)

   limit = DAILY_LIMIT[tier]
   if limit and row["page_count"] > limit:
       raise HTTPException(429, "Daily limit reached. Upgrade at mangalens.app")
   ```
4. **Rate limiting** (slowapi):
   - Per IP: 30 requests/minute (blocks scrapers)
   - Per user: 10 requests/minute (prevents one user hammering)
5. **Forward to translator**: same as Phase 1 proxy — `POST http://manga-translator:5003/translate/image`
6. **Logging**: every request logs timestamp, google_id (hashed), tier, status, duration

#### `backend/proxy/requirements.txt`

```
fastapi
uvicorn
httpx
asyncpg
databases[asyncpg]
slowapi
httpx
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

#### Updated `docker-compose.yml`

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
      - TRANSLATOR_URL=http://manga-translator:5003
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
      - DATABASE_URL=${DATABASE_URL}
      - STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET}
    depends_on:
      - manga-translator
    restart: unless-stopped
```

#### Stripe webhook endpoint

Add `POST /webhook/stripe` to the proxy. Handle two events:
- `customer.subscription.updated` — set `tier = 'paid'` if `status = 'active'`, else `tier = 'free'`
- `customer.subscription.deleted` — set `tier = 'free'`

Verify the Stripe signature header before processing. Never trust the payload without verification.

#### Phase 2A complete when

- `docker-compose up` starts both services
- Request without `Authorization` header → `401`
- Request with invalid/expired Google token → `401`
- Request with valid token, user over quota → `429`
- Request with valid token, user under quota → translated image returned
- Stripe webhook correctly upgrades/downgrades `tier`

---

### Phase 2B — Chrome Extension (`extension/`)

No hardcoded secrets. Auth flows entirely through Google OAuth.

#### manifest.json additions

```json
{
  "permissions": ["activeTab", "storage", "scripting", "identity"],
  "oauth2": {
    "client_id": "<GOOGLE_CLIENT_ID>.apps.googleusercontent.com",
    "scopes": ["openid", "email", "profile"]
  }
}
```

The `client_id` in the manifest is intentionally public — it cannot be used to impersonate users. Only the backend holds the secret.

#### Authentication flow

1. User clicks **Sign in with Google** in the popup
2. `chrome.identity.getAuthToken({ interactive: true })` → returns a short-lived access token
3. Extension exchanges it for a Google ID token:
   ```js
   const resp = await fetch(
     `https://www.googleapis.com/oauth2/v3/userinfo`,
     { headers: { Authorization: `Bearer ${accessToken}` } }
   );
   // store the access token — sent as Bearer token with each translate request
   ```
4. Token stored in `chrome.storage.session` (cleared when browser closes — not persisted to disk)
5. Every translation request includes `Authorization: Bearer <token>`
6. Backend verifies token on every request (no server-side session)

#### `content.js` changes

- Remove hardcoded `BACKEND_URL` and `MANGA_API_KEY` constants
- Read auth token from `chrome.storage.session`
- If token is missing or expired, post a message to the popup to re-authenticate
- Translation request header: `Authorization: Bearer <token>`

```js
const BACKEND_URL = "https://api.mangalens.app";

async function getToken() {
  return new Promise(resolve =>
    chrome.storage.session.get("authToken", r => resolve(r.authToken))
  );
}
```

#### `popup.html` / `popup.js` — updated

States:
1. **Signed out** — show "Sign in with Google" button
2. **Signed in, free tier** — show toggle, pages used today / 50, "Upgrade" link
3. **Signed in, paid tier** — show toggle, pages used today (no cap shown)
4. **Quota exceeded** — show "Daily limit reached. Upgrade at mangalens.app"

Token refresh: call `chrome.identity.getAuthToken({ interactive: false })` on popup open to silently refresh; only prompt interactively if silent refresh fails.

#### Phase 2B complete when

1. Signed-out user sees Google sign-in button
2. After sign-in, toggle appears with correct tier and usage count
3. Translating a manga page works end-to-end with a real Google account
4. Over-quota users see the limit message instead of a translation
5. No hardcoded credentials anywhere in the extension source

---

### Phase 2C — Domain + HTTPS

#### Purchase domain

Buy `mangalens.app` (or similar) from any registrar (Namecheap, Cloudflare Registrar, etc.).

#### DNS setup

| Record | Name | Value |
|---|---|---|
| A | `api` | `<GCE_EXTERNAL_IP>` |
| A | `@` | `<GCE_EXTERNAL_IP>` (Phase 3 frontend, placeholder for now) |

#### HTTPS with Let's Encrypt (on GCE instance)

```bash
# Install nginx + certbot
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Nginx config: /etc/nginx/sites-available/mangalens
server {
    listen 80;
    server_name api.mangalens.app;
    location / { proxy_pass http://localhost:8080; }
}

# Get certificate
sudo certbot --nginx -d api.mangalens.app

# certbot auto-renews via systemd timer — verify with:
sudo certbot renew --dry-run
```

After this, the proxy is reachable at `https://api.mangalens.app/translate/image`.
Update `BACKEND_URL` in `content.js` to use this URL.

#### GCP Firewall updates

- Allow TCP `443` (HTTPS) inbound — add to the `allow-manga-proxy` rule or create a new rule
- Allow TCP `80` inbound — needed for Let's Encrypt HTTP-01 challenge
- Port `8080` can be closed publicly (nginx now fronts it)

#### Phase 2C complete when

- `https://api.mangalens.app` responds
- HTTP redirects to HTTPS
- Certificate is valid and auto-renewing
- Port `8080` and `5003` are not publicly accessible

---

### Phase 2D — GitHub Actions CI/CD Pipeline

#### Workflow: `.github/workflows/deploy.yml`

Triggers on push to `main`. Steps:

1. **Test** — run `pytest tests/test_translation.py` against a mock (or skip if translator not available in CI)
2. **Build proxy image** — `docker build backend/proxy`
3. **Deploy to GCE** — SSH into the instance, pull latest code, restart containers

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to GCE
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.GCE_IP }}
          username: ${{ secrets.GCE_USER }}
          key: ${{ secrets.GCE_SSH_KEY }}
          script: |
            cd ~/manga-lens
            git pull origin main
            docker-compose up -d --build proxy
```

#### GitHub Actions secrets to configure

| Secret | Value |
|---|---|
| `GCE_IP` | External IP of the GCE instance |
| `GCE_USER` | SSH username (e.g. your GCP username) |
| `GCE_SSH_KEY` | Private key for SSH access (generate a deploy key) |

#### Generating a deploy key (run locally)

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/manga-lens-deploy
# Add the public key to the GCE instance:
gcloud compute ssh manga-lens-server -- "echo '$(cat ~/.ssh/manga-lens-deploy.pub)' >> ~/.ssh/authorized_keys"
# Add the private key to GitHub → repo Settings → Secrets → GCE_SSH_KEY
cat ~/.ssh/manga-lens-deploy
```

#### Phase 2D complete when

- Pushing to `main` triggers the workflow
- Workflow completes without errors
- The running proxy on GCE reflects the latest code within 2 minutes of push

---

### Stripe Setup (for Phase 2A webhook)

1. Create account at stripe.com
2. Create two products:
   - **MangaLens Basic** — recurring $9.90/month. Note the Price ID.
   - Create a coupon for $4.90 (launch discount) — apply it at checkout, not as a separate price
3. Add webhook endpoint: `https://api.mangalens.app/webhook/stripe`
   - Events: `customer.subscription.updated`, `customer.subscription.deleted`
4. Copy the webhook signing secret to `backend/.env` as `STRIPE_WEBHOOK_SECRET`

---

### Google OAuth Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID → Application type: **Chrome Extension**
3. Set the Extension ID (get it from `chrome://extensions` after first load)
4. Copy the Client ID → add to `manifest.json` and `backend/.env` as `GOOGLE_CLIENT_ID`
5. Add authorised JavaScript origins: `https://api.mangalens.app`

---

### Definition of Done — Phase 2

1. Unauthenticated requests to the proxy return `401`
2. Free users are blocked after 50 translations in a day (proxy returns `429`)
3. Stripe payment upgrades user to `paid` tier; proxy allows unlimited translations immediately
4. Cancelling a Stripe subscription downgrades user back to `free`
5. Backend is reachable at `https://api.mangalens.app` with a valid TLS certificate
6. Pushing to `main` deploys automatically via GitHub Actions
7. Extension translates a real manga page end-to-end with a real Google account
8. No hardcoded secrets anywhere in the extension source

---

## Phase 3 — Companion Website

**Do not begin Phase 3 until Phase 2 is fully complete and verified.**

Phase 3 adds a public-facing website that serves as the product landing page, user dashboard, and subscription management portal. Auth and payments already exist from Phase 2 — Phase 3 adds the web UI on top of the same database.

---

### Pages

| Page | Description |
|---|---|
| Home | Landing page — what MangaLens is, screenshots, pricing table, Chrome Web Store install button |
| Dashboard | Sign in with Google → shows current tier, pages used today, upgrade/cancel subscription link |
| Pricing | Tier comparison table ($0 free / $9.90 paid with $4.90 launch coupon) with Stripe Checkout |
| Contact | Simple contact form (name, email, message) |

No separate sign-up page needed — Google OAuth handles auth for both the extension and the website.

---

### Tech Stack

- **Framework**: Next.js 14 (App Router), under `website/`
- **Auth**: NextAuth.js with `GoogleProvider` — same Google OAuth app as Phase 2
- **Payments**: Stripe Checkout (`mode: "subscription"`) — links to existing Phase 2 Stripe products
- **Database**: same Supabase instance as Phase 2 — website reads `users`, `daily_usage`, `subscriptions`
- **Hosting**: Vercel (free tier sufficient for low traffic)
- **Domain**: `mangalens.app` → already set up in Phase 2C

DNS additions for Phase 3:

| Record | Name | Value |
|---|---|---|
| A or CNAME | `@` / `www` | Vercel deployment URL |

The `api` subdomain already points to GCE. The apex domain now points to Vercel.

---

### Infrastructure Cost Comparison

Choose **Option B** (Supabase + Vercel + Railway). It is significantly cheaper at this scale and requires less ops work.

#### Option A — All-in GCP

| Service | What it does | Monthly cost |
|---|---|---|
| GCE e2-medium | Translator + proxy | ~$25 |
| Cloud SQL (db-f1-micro) | PostgreSQL | ~$10 |
| Cloud Run or App Engine | Website hosting | ~$5–15 |
| Cloud Load Balancer | HTTPS for website | ~$18 minimum |
| **Total** | | **~$58–68/month** |

Notes: Cloud SQL has a minimum instance charge even at zero load. Cloud Load Balancer has a fixed $18/month forwarding rule cost. Ops overhead is higher — you manage VMs, DB backups, and SSL separately.

#### Option B — GCP (translator) + Supabase + Vercel

| Service | What it does | Monthly cost |
|---|---|---|
| GCE e2-medium | Translator + proxy only | ~$25 |
| Supabase (free tier) | PostgreSQL + auth helpers | $0 (up to 500 MB DB, 2 GB bandwidth) |
| Vercel (hobby tier) | Website hosting | $0 |
| **Total** | | **~$25/month** |

If Supabase free tier is outgrown (unlikely for early users):
- Supabase Pro: $25/month → total ~$50/month

**Recommendation**: Start on Option B. The GCE instance is already required for the translator regardless. Supabase and Vercel free tiers comfortably handle hundreds of users. Migrate to Option A only if you need GCP-specific compliance or the free tiers are exhausted.

---

### Domain Email Setup

Use **Cloudflare Email Routing** (free) to receive email at your domain and forward to your personal Gmail. For sending, use **Gmail** with a custom "From" address. This avoids paying for Google Workspace ($6/user/month).

#### Step 1 — Move DNS to Cloudflare (recommended, free)

1. Create a Cloudflare account and add `mangalens.app`
2. Cloudflare will import your existing DNS records automatically
3. Update your registrar's nameservers to Cloudflare's nameservers
4. Wait for propagation (~minutes to hours)

#### Step 2 — Enable Email Routing in Cloudflare

1. In Cloudflare dashboard → **Email** → **Email Routing** → Enable
2. Add a custom address: e.g. `hello@mangalens.app` → forwards to `your.gmail@gmail.com`
3. Cloudflare adds the required MX and SPF records automatically
4. Verify your Gmail address when prompted

You can now receive email at `hello@mangalens.app`.

#### Step 3 — Send email from Gmail using your domain address

1. In Gmail → Settings → **Accounts and Import** → **Send mail as** → Add another email address
2. Enter `hello@mangalens.app`, untick "Treat as alias"
3. SMTP server: `smtp.gmail.com`, port `587`, your Gmail address and an **App Password**
   - Generate App Password: Google Account → Security → 2-Step Verification → App Passwords
4. Verify ownership via the confirmation email
5. Gmail now lets you choose `hello@mangalens.app` as the From address when composing

#### Suggested email addresses to set up

| Address | Purpose |
|---|---|
| `hello@mangalens.app` | General contact (shown on website Contact page) |
| `noreply@mangalens.app` | Transactional emails (Stripe receipts forward here) |
| `support@mangalens.app` | Support alias — forward to same Gmail inbox |

All three can be set up as separate forwarding rules in Cloudflare pointing to the same Gmail.

---

### Definition of Done — Phase 3

1. Website is live at `https://mangalens.app`
2. "Sign in with Google" on the website logs the user in and shows their tier + daily usage
3. Stripe Checkout on the Pricing page completes a real subscription
4. After payment, the dashboard immediately reflects `paid` tier
5. Extension popup has an "Open Dashboard" button linking to `https://mangalens.app`
6. Email at `hello@mangalens.app` receives and forwards correctly

---

## Key Constraints — Do Not Violate

- Never expose port 5003 or 5004 publicly — only nginx/443 is public
- Never commit `.env` to git
- `inpainter: lama_large` removes bubble backgrounds on this manga style — do not use
- `inpainter: original` leaves Japanese text visible — debug only
- `/translate/batch/images` is broken upstream — use sequential single-image calls
- The Docker image is ~15GB — first pull is slow, this is expected
- Lambda is not viable — Docker image exceeds Lambda's 10GB container image limit
- No GPU required — do not add GPU requirements
- Do not build Phase 3 features during Phase 2
- Never hardcode secrets in the Chrome extension — use Google OAuth tokens only
- Verify the `aud` claim on every Google token — reject tokens issued for other client IDs

---

## Environment Variables Reference

| Variable | Used by | Phase | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | translator container | 1+ | Gemini API key |
| `GEMINI_MODEL` | translator container | 1+ | Model name, default `gemini-2.5-flash-lite` |
| `GOOGLE_CLIENT_ID` | proxy + extension manifest | 2+ | OAuth client ID from GCP Console |
| `DATABASE_URL` | proxy | 2+ | Supabase postgres connection string |
| `STRIPE_SECRET_KEY` | proxy | 2+ | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | proxy | 2+ | Stripe webhook signing secret |
| `STRIPE_PRICE_ID` | proxy/website | 2+ | Stripe Price ID for $9.90/month plan |
| `NEXTAUTH_SECRET` | website | 3+ | Random secret for NextAuth session signing |
| `NEXTAUTH_URL` | website | 3+ | Public URL of the website (`https://mangalens.app`) |

---

## When You Need User Input

1. **GEMINI_API_KEY** — Google AI Studio → free tier available
2. **GOOGLE_CLIENT_ID** — GCP Console → APIs & Services → Credentials → Chrome Extension OAuth client
3. **GCP account** — needed for Phase 2 GCE instance
4. **Supabase project** — create at supabase.com, copy the `DATABASE_URL` connection string
5. **Stripe account** — needed for Phase 2 subscription ($9.90/month product + $4.90 coupon)
6. **Domain name** — `mangalens.app` (or similar) — needed before Phase 2C
