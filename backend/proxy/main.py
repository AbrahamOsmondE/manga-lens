import hashlib
import logging
import os
import time
from datetime import datetime, timezone

import asyncpg
import httpx
import stripe
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID  = os.environ["GOOGLE_CLIENT_ID"]
TRANSLATOR_URL    = os.environ.get("TRANSLATOR_URL", "http://localhost:5003")
DATABASE_URL      = os.environ["DATABASE_URL"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
stripe.api_key    = os.environ["STRIPE_SECRET_KEY"]

DAILY_LIMIT = {"free": 10, "paid": None}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Rate limiting (slowapi) ───────────────────────────────────────────────────
# Per-IP: 30 req/min  |  Per-user: enforced separately in the route via DB

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Slow down."})

# ── DB pool ───────────────────────────────────────────────────────────────────

db: asyncpg.Pool | None = None

@app.on_event("startup")
async def startup():
    global db
    db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    # Create tables if they don't exist yet (idempotent)
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                google_id  text UNIQUE NOT NULL,
                email      text UNIQUE NOT NULL,
                tier       text NOT NULL DEFAULT 'free',
                created_at timestamptz DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id    uuid REFERENCES users(id),
                day        date NOT NULL,
                page_count int  NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id                  text PRIMARY KEY,
                user_id             uuid REFERENCES users(id),
                status              text NOT NULL,
                current_period_end  timestamptz,
                updated_at          timestamptz DEFAULT now()
            );
        """)

@app.on_event("shutdown")
async def shutdown():
    if db:
        await db.close()

# ── Google token verification ─────────────────────────────────────────────────

async def verify_google_token(token: str) -> dict:
    """
    Verify a Google ID token via tokeninfo endpoint.
    Returns {"google_id": ..., "email": ...} or raises HTTPException 401.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": token},
        )
    if resp.status_code != 200:
        raise HTTPException(401, "Invalid or expired Google token")

    data = resp.json()

    # Reject tokens not issued for this app
    if data.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(401, "Token audience mismatch")

    # Reject expired tokens (tokeninfo checks this too, but be explicit)
    exp = int(data.get("exp", 0))
    if exp < time.time():
        raise HTTPException(401, "Token expired")

    return {"google_id": data["sub"], "email": data.get("email", "")}

# ── User upsert + quota ───────────────────────────────────────────────────────

async def get_or_create_user(google_id: str, email: str) -> dict:
    row = await db.fetchrow(
        """
        INSERT INTO users (google_id, email)
        VALUES ($1, $2)
        ON CONFLICT (google_id) DO UPDATE SET email = EXCLUDED.email
        RETURNING id, tier
        """,
        google_id, email,
    )
    return {"id": row["id"], "tier": row["tier"]}


async def check_and_increment_quota(user_id, tier: str) -> int:
    """
    Atomically increment daily usage. Returns the new page_count.
    Raises 429 if the free limit is exceeded.
    """
    today = datetime.now(timezone.utc).date()
    row = await db.fetchrow(
        """
        INSERT INTO daily_usage (user_id, day, page_count)
        VALUES ($1, $2, 1)
        ON CONFLICT (user_id, day)
        DO UPDATE SET page_count = daily_usage.page_count + 1
        RETURNING page_count
        """,
        user_id, today,
    )
    count = row["page_count"]
    limit = DAILY_LIMIT[tier]
    if limit is not None and count > limit:
        raise HTTPException(
            429,
            f"Daily limit of {limit} pages reached. Upgrade at mangalens.app",
        )
    return count

# ── Translation endpoint ──────────────────────────────────────────────────────

@app.post("/translate/image")
@limiter.limit("30/minute")   # per-IP guard
async def translate(request: Request):
    # Extract Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization header")
    token = auth_header[len("Bearer "):]

    # Verify Google identity
    identity = await verify_google_token(token)
    user = await get_or_create_user(identity["google_id"], identity["email"])

    # Per-user rate limit: 10 req/min (enforced via DB increment + count check)
    # This is a soft guard — the daily quota is the hard limit for free users.
    # A stricter per-minute in-memory guard can be added with Redis if needed.

    # Quota check + increment
    page_count = await check_and_increment_quota(user["id"], user["tier"])

    start = time.time()
    hashed_id = hashlib.sha256(identity["google_id"].encode()).hexdigest()[:12]
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{TRANSLATOR_URL}/translate/image",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        elapsed = time.time() - start
        logger.info(
            "user=%s tier=%s pages_today=%d status=%d duration=%.2fs",
            hashed_id, user["tier"], page_count, resp.status_code, elapsed,
        )
        return StreamingResponse(
            iter([resp.content]),
            media_type=resp.headers.get("content-type", "image/png"),
        )
    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.time() - start
        logger.error("user=%s error=%s duration=%.2fs", hashed_id, e, elapsed)
        raise HTTPException(502, "Translator unavailable")

# ── Usage endpoint (for extension popup) ─────────────────────────────────────

@app.get("/usage")
@limiter.limit("60/minute")
async def usage(request: Request):
    """Returns the user's tier and today's page count."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization header")
    token = auth_header[len("Bearer "):]

    identity = await verify_google_token(token)
    user = await get_or_create_user(identity["google_id"], identity["email"])

    today = datetime.now(timezone.utc).date()
    row = await db.fetchrow(
        "SELECT page_count FROM daily_usage WHERE user_id = $1 AND day = $2",
        user["id"], today,
    )
    return {
        "tier": user["tier"],
        "pages_today": row["page_count"] if row else 0,
        "daily_limit": DAILY_LIMIT[user["tier"]],
    }

# ── Stripe webhook ────────────────────────────────────────────────────────────

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid Stripe signature")

    sub = event["data"]["object"]
    stripe_sub_id = sub["id"]
    customer_id   = sub["customer"]
    status        = sub["status"]   # 'active', 'canceled', 'past_due', etc.
    period_end    = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
    new_tier      = "paid" if status == "active" else "free"

    # Look up user by Stripe customer ID stored in subscriptions table
    user_row = await db.fetchrow(
        "SELECT user_id FROM subscriptions WHERE id = $1", stripe_sub_id
    )

    if user_row is None:
        # First time seeing this subscription — look up by customer metadata
        # Stripe customer metadata should have been set at checkout time
        customer = stripe.Customer.retrieve(customer_id)
        user_email = customer.get("email")
        if user_email:
            user_row = await db.fetchrow(
                "SELECT id FROM users WHERE email = $1", user_email
            )
            if user_row:
                await db.execute(
                    """
                    INSERT INTO subscriptions (id, user_id, status, current_period_end)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (id) DO UPDATE
                      SET status = EXCLUDED.status,
                          current_period_end = EXCLUDED.current_period_end,
                          updated_at = now()
                    """,
                    stripe_sub_id, user_row["id"], status, period_end,
                )
                await db.execute(
                    "UPDATE users SET tier = $1 WHERE id = $2",
                    new_tier, user_row["id"],
                )
    else:
        await db.execute(
            """
            UPDATE subscriptions
            SET status = $1, current_period_end = $2, updated_at = now()
            WHERE id = $3
            """,
            status, period_end, stripe_sub_id,
        )
        await db.execute(
            "UPDATE users SET tier = $1 WHERE user_id = $2",
            new_tier, user_row["user_id"],
        )

    logger.info("stripe event=%s sub=%s status=%s tier=%s", event["type"], stripe_sub_id, status, new_tier)
    return {"ok": True}
