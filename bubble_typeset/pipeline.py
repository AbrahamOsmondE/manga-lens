"""
Full pipeline:
  1. Google Vision API  -> text blocks + bounding boxes
  2. Cluster nearby blocks -> one group per bubble
  3. Paint bubble regions white (NoneInpainter logic)
  4. Translate grouped text (Google Translate free tier via requests)
  5. Typeset translated text back into bubble

Usage:
    python pipeline.py image.png --target-lang en --output result.png
"""

import os, sys, base64, json, requests, cv2, numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_render import set_font, FONTS_DIR
from text_render_eng import render_textblock_list_eng, SimpleTextBlock

# ── 1. Google Vision ──────────────────────────────────────────────────────────

def vision_detect(image_path: str) -> list:
    """Returns list of {text, vertices} dicts, one per block."""
    api_key = os.environ.get('GOOGLE_VISION_API_KEY')
    if not api_key:
        sys.exit('ERROR: GOOGLE_VISION_API_KEY not set')

    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()

    resp = requests.post(
        f'https://vision.googleapis.com/v1/images:annotate?key={api_key}',
        json={'requests': [{'image': {'content': b64},
                            'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}]}]}
    )
    resp.raise_for_status()
    data = resp.json()

    blocks = []
    pages = data['responses'][0].get('fullTextAnnotation', {}).get('pages', [])
    for page in pages:
        for block in page.get('blocks', []):
            verts = block['boundingBox']['vertices']
            xs = [v.get('x', 0) for v in verts]
            ys = [v.get('y', 0) for v in verts]
            text = ''
            for para in block.get('paragraphs', []):
                for word in para.get('words', []):
                    for sym in word.get('symbols', []):
                        text += sym.get('text', '')
                    text += ' '
            blocks.append({
                'text': text.strip(),
                'x1': min(xs), 'y1': min(ys),
                'x2': max(xs), 'y2': max(ys),
            })
    return blocks


# ── 2. Cluster blocks into bubbles ────────────────────────────────────────────

def box_distance(a, b):
    """Minimum distance between two axis-aligned boxes."""
    dx = max(0, max(a['x1'], b['x1']) - min(a['x2'], b['x2']))
    dy = max(0, max(a['y1'], b['y1']) - min(a['y2'], b['y2']))
    return (dx**2 + dy**2) ** 0.5


def cluster_blocks(blocks: list, threshold: int = 60) -> list:
    """Union-find clustering: blocks within `threshold` px -> same bubble."""
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
            if box_distance(blocks[i], blocks[j]) < threshold:
                union(i, j)

    groups = {}
    for i, block in enumerate(blocks):
        root = find(i)
        groups.setdefault(root, []).append(block)

    bubbles = []
    for group in groups.values():
        x1 = min(b['x1'] for b in group)
        y1 = min(b['y1'] for b in group)
        x2 = max(b['x2'] for b in group)
        y2 = max(b['y2'] for b in group)
        text = ''.join(b['text'] for b in group)
        bubbles.append({'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'text': text})

    return bubbles


# ── 3. Paint white ────────────────────────────────────────────────────────────

def paint_white(img: np.ndarray, bubbles: list, padding: int = 15) -> np.ndarray:
    """NoneInpainter: img[mask > 0] = 255  (manga_translator/inpainting/none.py)"""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for b in bubbles:
        x1 = max(0, b['x1'] - padding)
        y1 = max(0, b['y1'] - padding)
        x2 = min(w, b['x2'] + padding)
        y2 = min(h, b['y2'] + padding)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    result = np.copy(img)
    result[mask > 0] = np.array([255, 255, 255], np.uint8)
    return result


# ── 4. Translate ──────────────────────────────────────────────────────────────

def translate_google(text: str, target: str = 'en') -> str:
    """Google Translate (no auth needed for small requests)."""
    try:
        resp = requests.get(
            'https://translate.googleapis.com/translate_a/single',
            params={'client': 'gtx', 'sl': 'ja', 'tl': target,
                    'dt': 't', 'q': text},
            timeout=10
        )
        resp.raise_for_status()
        parts = resp.json()[0]
        return ''.join(p[0] for p in parts if p[0])
    except Exception as e:
        print(f'  Translation failed: {e}')
        return text


# ── 5. Typeset ────────────────────────────────────────────────────────────────

def build_regions(bubbles: list) -> list:
    regions = []
    for b in bubbles:
        w = b['x2'] - b['x1']
        h = b['y2'] - b['y1']
        # scale font to fit: smaller of width/height divided by estimated chars per line
        font_size = max(10, min(w // 6, h // 6, 20))
        regions.append(SimpleTextBlock(
            x=b['x1'], y=b['y1'], w=w, h=h,
            translation=b.get('translated', b['text']),
            font_size=font_size,
        ))
    return regions


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('--target-lang', default='en')
    parser.add_argument('--output', default='result.png')
    parser.add_argument('--cluster-threshold', type=int, default=40)
    parser.add_argument('--padding', type=int, default=5)
    parser.add_argument('--no-translate', action='store_true')
    args = parser.parse_args()

    # font
    font_path = next(
        (os.path.join(FONTS_DIR, f) for f in ['anime_ace_3.ttf', 'Arial-Unicode-Regular.ttf']
         if os.path.exists(os.path.join(FONTS_DIR, f))), None)
    if not font_path:
        sys.exit(f'No font found in {FONTS_DIR}')
    set_font(font_path)

    img_bgr = cv2.imread(args.image)
    if img_bgr is None:
        sys.exit(f'Cannot read {args.image}')
    img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    import time
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    print('1. Detecting text with Google Vision...')
    blocks = vision_detect(args.image)
    print(f'   {len(blocks)} blocks found  [{time.perf_counter()-t0:.2f}s]')

    t0 = time.perf_counter()
    print('2. Clustering into bubbles...')
    bubbles = cluster_blocks(blocks, threshold=args.cluster_threshold)
    print(f'   {len(bubbles)} bubbles found  [{time.perf_counter()-t0:.2f}s]')
    for i, b in enumerate(bubbles):
        print(f'   Bubble {i}: "{b["text"][:40]}..."' if len(b["text"]) > 40 else f'   Bubble {i}: "{b["text"]}"')

    if not args.no_translate:
        t0 = time.perf_counter()
        print('3. Translating...')
        for b in bubbles:
            b['translated'] = translate_google(b['text'], args.target_lang)
            print(f'   JP: {b["text"][:40]}')
            print(f'   EN: {b["translated"][:40]}')
        print(f'   [{time.perf_counter()-t0:.2f}s]')
    else:
        for b in bubbles:
            b['translated'] = b['text']

    t0 = time.perf_counter()
    print('4. Painting bubbles white...')
    img_white = paint_white(img, bubbles, padding=args.padding)
    print(f'   [{time.perf_counter()-t0:.2f}s]')

    t0 = time.perf_counter()
    print('5. Typesetting...')
    regions = build_regions(bubbles)
    result = render_textblock_list_eng(
        img=img_white,
        text_regions=regions,
        original_img=img_white,
    )
    print(f'   [{time.perf_counter()-t0:.2f}s]')

    out_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    cv2.imwrite(args.output, out_bgr)
    print(f'\nDone -> {args.output}  [total: {time.perf_counter()-t_total:.2f}s]')


if __name__ == '__main__':
    main()
