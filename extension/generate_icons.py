"""
Run once to generate icons/icon16.png, icon48.png, icon128.png.
Requires: pip install Pillow
"""
from PIL import Image, ImageDraw, ImageFont
import os

SIZES = [16, 48, 128]
OUT_DIR = os.path.join(os.path.dirname(__file__), "icons")
os.makedirs(OUT_DIR, exist_ok=True)

for size in SIZES:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    margin = max(1, size // 12)
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(30, 30, 30, 230))

    # "ML" text
    font_size = max(5, size // 3)
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = "ML"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    out_path = os.path.join(OUT_DIR, f"icon{size}.png")
    img.save(out_path)
    print(f"Wrote {out_path}")

print("Done.")
