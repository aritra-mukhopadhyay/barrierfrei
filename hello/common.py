import base64
import hashlib
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

META_BASE_URL = "https://api.meta.ai/v1"

# Demo area: Darmstadt Hbf to Luisenplatz (min_lon, min_lat, max_lon, max_lat)
DEMO_BBOX = (8.628, 49.869, 8.653, 49.876)


def meta_client() -> OpenAI:
    return OpenAI(base_url=META_BASE_URL, api_key=os.environ["MODEL_API_KEY"])


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name} in .env")
    return value


IMAGE_CACHE = Path(__file__).resolve().parent.parent / "data" / "images"


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def image_data_uri(url: str) -> str:
    """Download an image once, cache it locally, and return it as a base64 data URI.

    Meta's API refuses to fetch Mapillary CDN URLs, and those URLs are signed and expire,
    so we always send the image bytes inline instead of a link. The cache key is the full
    URL; the real pipeline will key by Mapillary image id instead.
    """
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    path = IMAGE_CACHE / hashlib.sha256(url.encode()).hexdigest()[:16]
    if not path.exists():
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    data = path.read_bytes()
    return f"data:{_media_type(data)};base64," + base64.b64encode(data).decode()
