import base64
import os
import time
import pytest
import requests

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:5003")
FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "img.jpg")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "translated.jpg")

CONFIG = {
    "ocr": {
        "ocr": "48px",
        "ignore_bubble": 5
    },
    "detector": {
        "detection_size": 1024,
        "unclip_ratio": 2.3
    },
    "inpainter": {
        "inpainter": "none"
    },
    "render": {
        "renderer": "manga2eng",
        "disable_font_border": False
    },
    "translator": {
        "translator": "gemini",
        "target_lang": "ENG"
    },
    "mask_dilation_offset": 0,
    "kernel_size": 1
}


def test_available_endpoints():
    """Print available endpoints from OpenAPI docs before running translation test."""
    docs_url = BACKEND_URL + "/docs"
    print(f"\nChecking OpenAPI docs at: {docs_url}")
    try:
        resp = requests.get(docs_url, timeout=10)
        print(f"Docs status: {resp.status_code}")
        if resp.status_code == 200:
            print("OpenAPI docs available — check the browser for field names if translation fails")
    except Exception as e:
        print(f"Could not reach docs: {e}")


def test_translate_manga_page():
    """
    Integration test: POST a real manga page to the translation service.
    Asserts the response is a valid image that differs from the input.
    Saves output to tests/output/translated.jpg for manual visual inspection.
    """
    # Verify fixture exists
    assert os.path.exists(FIXTURE_PATH), (
        f"Sample image not found at {FIXTURE_PATH}. "
        "Place a real Japanese manga page there before running this test."
    )

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Read input image
    with open(FIXTURE_PATH, "rb") as f:
        input_bytes = f.read()

    print(f"\nInput image size: {len(input_bytes):,} bytes")
    print(f"Sending to: {BACKEND_URL}/translate/image")

    # Encode image as base64 data URI for JSON request
    image_b64 = base64.b64encode(input_bytes).decode("utf-8")
    image_data_uri = f"data:image/jpeg;base64,{image_b64}"

    payload = {
        "image": image_data_uri,
        "config": CONFIG
    }

    # Time the translation
    start = time.time()
    response = requests.post(
        f"{BACKEND_URL}/translate/image",
        json=payload,
        timeout=300   # translation can take 2-5 minutes on first run
    )
    elapsed = time.time() - start

    print(f"Response status: {response.status_code}")
    print(f"Response content-type: {response.headers.get('content-type')}")
    print(f"Response size: {len(response.content):,} bytes")
    print(f"Total time: {elapsed:.1f}s")

    # Assert valid image response
    assert response.status_code == 200, (
        f"Translation failed with status {response.status_code}. "
        f"Body: {response.text[:500]}"
    )
    assert len(response.content) > 0, "Response body is empty"
    content_type = response.headers.get("content-type", "")
    assert "image" in content_type, f"Expected image content-type, got: {content_type}"

    # Assert image was modified
    assert response.content != input_bytes, (
        "Response is byte-for-byte identical to input — translation did not modify the image. "
        "Check the config payload field names against /docs"
    )

    # Save output for visual inspection
    with open(OUTPUT_PATH, "wb") as f:
        f.write(response.content)

    print(f"\nOutput saved to: {OUTPUT_PATH}")
    print("Open this file to visually verify English text is inside speech bubbles.")


def translate_batch(image_paths: list[str], output_dir: str, config: dict = None, backend_url: str = None) -> list[str]:
    """
    Translate a list of manga image paths sequentially.
    Returns list of output file paths.

    Usage:
        results = translate_batch(
            image_paths=['page1.jpg', 'page2.jpg', 'page3.jpg'],
            output_dir='tests/output/batch'
        )
    """
    backend_url = backend_url or BACKEND_URL
    config = config or CONFIG
    os.makedirs(output_dir, exist_ok=True)

    output_paths = []
    total_start = time.time()

    for i, path in enumerate(image_paths, 1):
        with open(path, "rb") as f:
            image_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

        start = time.time()
        resp = requests.post(
            f"{backend_url}/translate/image",
            json={"image": image_b64, "config": config},
            timeout=300
        )
        elapsed = time.time() - start

        if resp.status_code != 200:
            raise RuntimeError(f"Page {i} failed ({resp.status_code}): {resp.text[:200]}")

        ext = os.path.splitext(path)[1] or ".png"
        out_path = os.path.join(output_dir, f"translated_{i:03d}{ext}")
        with open(out_path, "wb") as f:
            f.write(resp.content)

        print(f"  [{i}/{len(image_paths)}] {os.path.basename(path)} -> {os.path.basename(out_path)}  ({elapsed:.1f}s)")
        output_paths.append(out_path)

    print(f"  Done: {len(output_paths)} pages in {time.time() - total_start:.1f}s total")
    return output_paths
