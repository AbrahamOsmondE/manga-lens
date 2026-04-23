"""
Test script: paint speech bubbles white, render lorem ipsum text inside them.

Usage:
    python test_typeset.py                        # generates synthetic panel
    python test_typeset.py --image path/to/panel.jpg  x y w h [x y w h ...]

The bubble coordinates (x y w h) mark which regions to whiten + typeset.
"""

import os
import sys
import argparse
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_render import set_font, FONTS_DIR
from text_render_eng import render_textblock_list_eng, SimpleTextBlock

LOREM = [
    "Lorem ipsum dolor sit amet consectetur.",
    "Adipiscing elit sed do eiusmod tempor incididunt ut labore.",
    "Duis aute irure dolor in reprehenderit voluptate.",
    "Excepteur sint occaecat cupidatat non proident.",
    "Sunt in culpa qui officia deserunt mollit anim.",
]


def make_synthetic_panel(width=900, height=650):
    """Create a manga-like panel with white speech bubbles."""
    img = np.full((height, width, 3), 200, dtype=np.uint8)

    # panel border
    cv2.rectangle(img, (5, 5), (width - 6, height - 6), (20, 20, 20), 4)

    # some background detail lines (mimics screentone/content)
    for i in range(0, height, 30):
        cv2.line(img, (5, i), (width - 6, i), (180, 180, 180), 1)

    # bubble 1 — top-left oval
    cv2.ellipse(img, (210, 130), (175, 95), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (210, 130), (175, 95), 0, 0, 360, (20, 20, 20), 3)

    # bubble 2 — bottom-right oval
    cv2.ellipse(img, (670, 490), (195, 110), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (670, 490), (195, 110), 0, 0, 360, (20, 20, 20), 3)

    # bubble 3 — small mid-left
    cv2.ellipse(img, (130, 420), (105, 65), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (130, 420), (105, 65), 0, 0, 360, (20, 20, 20), 3)

    return img


def paint_bubbles_white(img: np.ndarray, bubble_coords: list) -> np.ndarray:
    """NoneInpainter logic from manga_translator/inpainting/none.py:
        img_inpainted[mask > 0] = [255, 255, 255]
    Mask is built with rectangle fills (complete_mask_fill from mask_refinement).
    """
    mask = np.zeros(img.shape[:2], dtype=np.uint8)
    for (x, y, w, h) in bubble_coords:
        cv2.rectangle(mask, (x, y), (x + w, y + h), 255, -1)

    img_inpainted = np.copy(img)
    img_inpainted[mask > 0] = np.array([255, 255, 255], np.uint8)
    return img_inpainted


def build_regions(bubble_coords, texts):
    regions = []
    for i, (x, y, w, h) in enumerate(bubble_coords):
        text = texts[i % len(texts)]
        font_size = max(18, min(h // 4, 36))
        regions.append(SimpleTextBlock(
            x=x, y=y, w=w, h=h,
            translation=text,
            font_size=font_size,
            angle=0.0,
            fg_color=(0, 0, 0),
            stroke_color=(255, 255, 255),
        ))
    return regions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default=None, help='Path to manga panel image')
    parser.add_argument('--font', default=None, help='Path to .ttf font file (optional)')
    parser.add_argument('--output', default='output_typeset.png')
    parser.add_argument('coords', nargs='*', type=int,
                        help='Bubble coords as x y w h groups (when --image is provided)')
    args = parser.parse_args()

    # --- font setup ---
    font_path = args.font
    if font_path is None:
        # prefer anime_ace_3 (manga-style), fall back to Arial
        candidates = [
            os.path.join(FONTS_DIR, 'anime_ace_3.ttf'),
            os.path.join(FONTS_DIR, 'Arial-Unicode-Regular.ttf'),
        ]
        font_path = next((p for p in candidates if os.path.exists(p)), None)

    if font_path is None:
        print(f"ERROR: no font found in {FONTS_DIR}")
        sys.exit(1)

    print(f"Using font: {font_path}")
    set_font(font_path)

    # --- image + bubbles ---
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f"ERROR: could not read {args.image}")
            sys.exit(1)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        raw = args.coords
        if len(raw) % 4 != 0 or len(raw) == 0:
            print("ERROR: provide bubble coords as groups of 4 integers: x y w h")
            sys.exit(1)
        bubble_coords = [(raw[i], raw[i+1], raw[i+2], raw[i+3]) for i in range(0, len(raw), 4)]
    else:
        print("No image provided — generating synthetic manga panel.")
        img = make_synthetic_panel()
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # coords matching the ellipses drawn in make_synthetic_panel
        bubble_coords = [
            (35,  35,  350, 190),   # bubble 1
            (475, 380, 390, 220),   # bubble 2
            (25,  355, 210, 130),   # bubble 3
        ]

    # --- paint bubbles white (NoneInpainter from repo) ---
    img = paint_bubbles_white(img, bubble_coords)

    # --- build text regions ---
    regions = build_regions(bubble_coords, LOREM)

    # --- render ---
    result = render_textblock_list_eng(
        img=img,
        text_regions=regions,
        original_img=img,
    )

    # --- save ---
    out_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    cv2.imwrite(args.output, out_bgr)
    print(f"Saved: {args.output}")

    # show if display available
    try:
        cv2.imshow('result', out_bgr)
        print("Press any key to close.")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except Exception:
        pass


if __name__ == '__main__':
    main()
