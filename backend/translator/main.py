import os
import sys
import time
import base64
import logging
import threading
from contextlib import asynccontextmanager

import cv2
import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_render import set_font, FONTS_DIR
from text_render_eng import render_textblock_list_eng, SimpleTextBlock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VISION_KEY = os.environ.get("GOOGLE_VISION_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# freetype face objects are not thread-safe — serialize render calls within each worker
_render_lock = threading.Lock()

# Shared session for Vision API calls — reuses TCP connections
_vision_session = requests.Session()

_gemini_model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gemini_model
    font_path = next(
        (os.path.join(FONTS_DIR, f) for f in ["anime_ace_3.ttf", "Arial-Unicode-Regular.ttf"]
         if os.path.exists(os.path.join(FONTS_DIR, f))),
        None,
    )
    if not font_path:
        raise RuntimeError(f"No font found in {FONTS_DIR}")
    set_font(font_path)
    logger.info("Font loaded: %s", font_path)
    if GEMINI_KEY:
        _gemini_model = True  # flag — REST API is stateless, no client object needed
        logger.info("Gemini translation enabled: %s", GEMINI_MODEL)
    else:
        logger.warning("GEMINI_API_KEY not set — falling back to Google Translate")
    yield


app = FastAPI(lifespan=lifespan)


class TranslateRequest(BaseModel):
    image: str
    config: dict = {}


# ── Vision API ────────────────────────────────────────────────────────────────

def _vision_detect(image_bytes: bytes) -> list:
    b64 = base64.b64encode(image_bytes).decode()
    resp = _vision_session.post(
        f"https://vision.googleapis.com/v1/images:annotate?key={VISION_KEY}",
        json={"requests": [{"image": {"content": b64},
                            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    blocks = []
    for page in resp.json()["responses"][0].get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            verts = block["boundingBox"]["vertices"]
            xs = [v.get("x", 0) for v in verts]
            ys = [v.get("y", 0) for v in verts]
            text = ""
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    for sym in word.get("symbols", []):
                        text += sym.get("text", "")
                    text += " "
            blocks.append({
                "text": text.strip(),
                "x1": min(xs), "y1": min(ys),
                "x2": max(xs), "y2": max(ys),
            })
    return blocks


# ── Cluster blocks → bubbles ──────────────────────────────────────────────────

def _cluster(blocks: list, threshold: int = 60) -> list:
    n = len(blocks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            a, b = blocks[i], blocks[j]
            dx = max(0, max(a["x1"], b["x1"]) - min(a["x2"], b["x2"]))
            dy = max(0, max(a["y1"], b["y1"]) - min(a["y2"], b["y2"]))
            if (dx ** 2 + dy ** 2) ** 0.5 < threshold:
                union(i, j)

    groups: dict = {}
    for i, blk in enumerate(blocks):
        groups.setdefault(find(i), []).append(blk)

    return [
        {
            "x1": min(b["x1"] for b in g),
            "y1": min(b["y1"] for b in g),
            "x2": max(b["x2"] for b in g),
            "y2": max(b["y2"] for b in g),
            "text": " ".join(b["text"] for b in g),
        }
        for g in groups.values()
    ]


# ── Paint bubbles white ───────────────────────────────────────────────────────

def _paint_white(img: np.ndarray, bubbles: list, pad: int = 15) -> np.ndarray:
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for b in bubbles:
        cv2.rectangle(
            mask,
            (max(0, b["x1"] - pad), max(0, b["y1"] - pad)),
            (min(w, b["x2"] + pad), min(h, b["y2"] + pad)),
            255, -1,
        )
    out = img.copy()
    out[mask > 0] = 255
    return out


# ── Translation ───────────────────────────────────────────────────────────────

def _translate_batch(texts: list[str]) -> list[str]:
    """Translate all bubble texts in a single Gemini API call."""
    if not texts:
        return texts
    if _gemini_model:
        try:
            import json
            cleaned = [" ".join(t.split()) for t in texts]
            prompt = (
                "You are translating manga/manhwa speech bubbles to English.\n"
                "The text was extracted by OCR and may have extra spaces between characters — treat each item as continuous text.\n"
                "Translate naturally into colloquial English, preserving tone, emotion, and speech style.\n"
                "Keep each translation SHORT — speech bubbles have limited space. Use contractions and casual language.\n"
                "Korean/Japanese interjections like '자!' mean 'Come on!' or 'Now!' — not the word 'ruler'.\n"
                "Sound effects and single characters should get a natural English equivalent.\n\n"
                f"Input JSON array: {json.dumps(cleaned, ensure_ascii=False)}\n\n"
                "Return a JSON array of the same length with English translations only. No explanation, no markdown."
            )
            t0 = time.time()
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            translations = json.loads(raw)
            if isinstance(translations, list) and len(translations) == len(texts):
                logger.info("gemini batch %.2fs | %d bubbles", time.time() - t0, len(texts))
                for orig, trans in zip(texts, translations):
                    logger.info("  %s → %s", orig[:50], trans[:50])
                return [str(t) for t in translations]
        except Exception as e:
            logger.warning("Gemini batch translation failed: %s — falling back to Google Translate", e)
    # Fallback: Google Translate per bubble
    results = []
    for text in texts:
        try:
            resp = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text},
                timeout=10,
            )
            parts = resp.json()[0]
            results.append("".join(p[0] for p in parts if p[0]))
        except Exception as e:
            logger.warning("Translation failed for %r: %s", text[:30], e)
            results.append(text)
    return results


# ── Endpoint ──────────────────────────────────────────────────────────────────

@app.post("/translate/image")
def translate(req: TranslateRequest):
    if not VISION_KEY:
        raise HTTPException(500, "GOOGLE_VISION_API_KEY not configured")

    t_start = time.time()

    try:
        b64data = req.image.split(",", 1)[-1]
        image_bytes = base64.b64decode(b64data)
    except Exception:
        raise HTTPException(400, "Invalid image data")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(400, "Cannot decode image")
    img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # OCR — one Vision API call returns all text in the image
    t_ocr = time.time()
    try:
        blocks = _vision_detect(image_bytes)
    except Exception as e:
        logger.error("Vision API error: %s", e)
        raise HTTPException(502, "OCR unavailable")
    t_ocr_done = time.time()

    if not blocks:
        _, buf = cv2.imencode(".png", img_bgr)
        return Response(content=buf.tobytes(), media_type="image/png")

    bubbles = _cluster(blocks)

    # Drop page numbers and very short noise (≤2 chars, all digits/punctuation)
    bubbles = [b for b in bubbles if len(b["text"].replace(" ", "")) > 2
               or not b["text"].replace(" ", "").replace(".", "").isdigit()]

    if not bubbles:
        _, buf = cv2.imencode(".png", img_bgr)
        return Response(content=buf.tobytes(), media_type="image/png")

    t_translate = time.time()
    translations = _translate_batch([b["text"] for b in bubbles])
    t_translate_done = time.time()
    for b, t in zip(bubbles, translations):
        b["translated"] = t

    img_white = _paint_white(img, bubbles)
    regions = [
        SimpleTextBlock(
            x=b["x1"], y=b["y1"],
            w=b["x2"] - b["x1"], h=b["y2"] - b["y1"],
            translation=b["translated"],
            font_size=max(24, min((b["x2"] - b["x1"]) // 3, (b["y2"] - b["y1"]) // 2, 40)),
        )
        for b in bubbles if b.get("translated", "").strip()
    ]

    if not regions:
        _, buf = cv2.imencode(".png", cv2.cvtColor(img_white, cv2.COLOR_RGB2BGR))
        return Response(content=buf.tobytes(), media_type="image/png")

    # Rendering uses freetype globals — serialize within this worker process
    with _render_lock:
        result, failed_xywhs = render_textblock_list_eng(img=img_white, text_regions=regions, original_img=img_white)

    # Restore original pixels for any bubble where rendering failed — leave
    # the Japanese text visible rather than showing an empty white box.
    if failed_xywhs:
        h, w = img.shape[:2]
        pad = 15
        for xywh in failed_xywhs:
            x1 = max(0, int(xywh[0]) - pad)
            y1 = max(0, int(xywh[1]) - pad)
            x2 = min(w, int(xywh[0]) + int(xywh[2]) + pad)
            y2 = min(h, int(xywh[1]) + int(xywh[3]) + pad)
            result[y1:y2, x1:x2] = img[y1:y2, x1:x2]

    result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    _, buf = cv2.imencode(".png", result_bgr)
    t_total = time.time() - t_start
    logger.info(
        "done | bubbles=%d ocr=%.2fs translate=%.2fs total=%.2fs",
        len(bubbles),
        t_ocr_done - t_ocr,
        t_translate_done - t_translate,
        t_total,
    )
    return Response(content=buf.tobytes(), media_type="image/png")


@app.get("/health")
def health():
    return {"ok": True}
