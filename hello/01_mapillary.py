"""Fetch a few Mapillary street photos inside the demo area.

Run: python hello/01_mapillary.py
Prints image ids, GPS points and photo URLs. Copy one URL for scripts 02 and 03.
"""
import requests

from common import DEMO_BBOX, env

resp = requests.get(
    "https://graph.mapillary.com/images",
    params={
        "access_token": env("MAPILLARY_TOKEN"),
        "bbox": ",".join(str(v) for v in DEMO_BBOX),
        "fields": "id,captured_at,computed_geometry,thumb_1024_url",
        "limit": 10,
    },
    timeout=30,
)
resp.raise_for_status()
images = resp.json().get("data", [])
print(f"{len(images)} images in demo area")
for img in images:
    lon, lat = img["computed_geometry"]["coordinates"]
    print(img["id"], f"{lat:.5f},{lon:.5f}", img["thumb_1024_url"])
