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


def image_data_uri(url: str) -> str:
    """Download an image once, cache it locally, and return it as a base64 data URI.

    Meta's API refuses to fetch Mapillary CDN URLs, and those URLs are signed and expire,
    so we always send the image bytes inline instead of a link.
    """
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    path = IMAGE_CACHE / f"{hashlib.sha256(url.split('?')[0].encode()).hexdigest()[:16]}.jpg"
    if not path.exists():
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()
