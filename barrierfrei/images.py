"""Download street photos once, keyed by Mapillary photo id, and encode them for model input."""

import base64
import io
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from barrierfrei.http import DEFAULT_TIMEOUT


def load_photo(session: requests.Session, photo_id: str, url: str, cache_dir: Path) -> bytes:
    """Return the photo's bytes, downloading only on the first call.

    Keyed by photo id, not URL: Mapillary re-signs its URLs, so the same photo gets new links.
    """
    path = cache_dir / f"{photo_id}.jpg"
    if not path.exists():
        resp = session.get(url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return path.read_bytes()


def media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def data_uri(data: bytes) -> str:
    return f"data:{media_type(data)};base64," + base64.b64encode(data).decode()


def draw_boxes(data: bytes, boxes: list[tuple[str, tuple[int, int, int, int]]]) -> bytes:
    """Draw labelled boxes on the photo so the judging model sees what SAM found."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    canvas = ImageDraw.Draw(image)
    for label, (x1, y1, x2, y2) in boxes:
        canvas.rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=3)
        canvas.text((x1 + 4, max(y1 - 14, 0)), label, fill=(255, 0, 0))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()
