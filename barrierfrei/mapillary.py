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


MIN_TILE_DEG = 0.0002  # ~20 m; below this, accept a truncated tile instead of recursing forever


def _fetch_tile(session: requests.Session, token: str, tile: tuple[float, float, float, float]) -> list[dict]:
    resp = session.get(
        API_URL,
        # Token goes in a header, not the URL: URLs end up in exception messages and logs.
        headers={"Authorization": f"OAuth {token}"},
        params={"bbox": ",".join(f"{v:.6f}" for v in tile), "fields": FIELDS, "limit": PAGE_LIMIT},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("data", [])
    if len(items) < PAGE_LIMIT:
        return items
    min_lon, min_lat, max_lon, max_lat = tile
    half_lon, half_lat = (max_lon - min_lon) / 2, (max_lat - min_lat) / 2
    if half_lon < MIN_TILE_DEG:
        log.warning("Tile %s still has %d+ photos at minimum size; keeping the first page", tile, PAGE_LIMIT)
        return items
    # Saturated tile: split it into four quarters and fetch each one.
    mid_lon, mid_lat = min_lon + half_lon, min_lat + half_lat
    quarters = [
        (min_lon, min_lat, mid_lon, mid_lat),
        (mid_lon, min_lat, max_lon, mid_lat),
        (min_lon, mid_lat, mid_lon, max_lat),
        (mid_lon, mid_lat, max_lon, max_lat),
    ]
    return [item for quarter in quarters for item in _fetch_tile(session, token, quarter)]


def fetch_photos(
    session: requests.Session, token: str, bbox: tuple[float, float, float, float]
) -> list[Photo]:
    photos: dict[str, Photo] = {}
    for tile in tiles(bbox):
        for item in _fetch_tile(session, token, tile):
            photo = parse_photo(item)
            if photo:
                photos[photo.id] = photo  # tiles share edges, so de-duplicate by id
    log.info("Fetched %d photos", len(photos))
    return list(photos.values())
