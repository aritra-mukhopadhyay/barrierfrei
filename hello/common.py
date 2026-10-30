import os

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
