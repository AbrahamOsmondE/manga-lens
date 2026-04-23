"""
Calls Google Cloud Vision Document Text Detection on an image,
draws the returned block-level bounding boxes, and prints detected text.
"""

import os
import sys
import base64
import json
import requests
import cv2
import numpy as np


def detect_text_blocks(image_path: str):
    api_key = os.environ.get('GOOGLE_VISION_API_KEY')
    if not api_key:
        print("ERROR: GOOGLE_VISION_API_KEY environment variable not set")
        sys.exit(1)

    with open(image_path, 'rb') as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "requests": [{
            "image": {"content": image_b64},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}]
        }]
    }

    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()


def draw_blocks(image_path: str, response: dict, output_path: str):
    img = cv2.imread(image_path)

    pages = response['responses'][0].get('fullTextAnnotation', {}).get('pages', [])
    block_num = 0
    for page in pages:
        for block in page.get('blocks', []):
            vertices = block['boundingBox']['vertices']
            pts = [(v.get('x', 0), v.get('y', 0)) for v in vertices]
            pts_np = np.array(pts, dtype=np.int32)

            # collect block text
            text = ''
            for para in block.get('paragraphs', []):
                for word in para.get('words', []):
                    for symbol in word.get('symbols', []):
                        text += symbol.get('text', '')
                    text += ' '

            cv2.polylines(img, [pts_np], True, (0, 0, 255), 2)
            cv2.putText(img, str(block_num), (pts[0][0], pts[0][1] - 5),
                        cv2.FONT_HERSHEY_PLAIN, 1.2, (0, 0, 255), 2)
            print(f"Block {block_num}: {text.strip()}")
            print(f"  vertices: {pts}")
            block_num += 1

    cv2.imwrite(output_path, img)
    print(f"\nSaved: {output_path}")


if __name__ == '__main__':
    image_path = sys.argv[1] if len(sys.argv) > 1 else 'image.png'
    response = detect_text_blocks(image_path)
    draw_blocks(image_path, response, 'debug_vision_blocks.png')
