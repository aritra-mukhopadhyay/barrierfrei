"""Fetch street-level photo metadata from the Mapillary Graph API (v4)."""

import logging
from dataclasses import dataclass

import requests

from barrierfrei.http import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

API_URL = "https://graph.mapillary.com/images"
FIELDS = "id,captured_at,compass_angle,is_pano,computed_geometry,thumb_1024_url"
PAGE_LIMIT = 2000  # the API's maximum per request
TILE_DEG = 0.005  # small tiles keep each request well under the limit


@dataclass(frozen=True)
class Photo:
    id: str
    lon: float
    lat: float
    captured_at: int | None
    compass_angle: float | None
    is_pano: bool
    thumb_url: str | None


def parse_photo(item: dict) -> Photo | None:
    geometry = item.get("computed_geometry")
    if not geometry:
        return None  # not yet positioned by Mapillary; useless for us
    lon, lat = geometry["coordinates"]
    return Photo(
        id=str(item["id"]),
        lon=lon,
        lat=lat,
        captured_at=item.get("captured_at"),
        compass_angle=item.get("compass_angle"),
        is_pano=bool(item.get("is_pano", False)),
        thumb_url=item.get("thumb_1024_url"),
    )


def tiles(bbox: tuple[float, float, float, float], step: float = TILE_DEG):
    min_lon, min_lat, max_lon, max_lat = bbox
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            yield (lon, lat, min(lon + step, max_lon), min(lat + step, max_lat))
            lat += step
        lon += step


def fetch_photos(
    session: requests.Session, token: str, bbox: tuple[float, float, float, float]
) -> list[Photo]:
    photos: dict[str, Photo] = {}
    for tile in tiles(bbox):
        resp = session.get(
            API_URL,
            params={
                "access_token": token,
                "bbox": ",".join(f"{v:.6f}" for v in tile),
                "fields": FIELDS,
                "limit": PAGE_LIMIT,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if len(items) >= PAGE_LIMIT:
            log.warning("Tile %s hit the %d-photo limit; some photos are missing", tile, PAGE_LIMIT)
        for item in items:
            photo = parse_photo(item)
            if photo:
                photos[photo.id] = photo  # tiles share edges, so de-duplicate by id
    log.info("Fetched %d photos", len(photos))
    return list(photos.values())
