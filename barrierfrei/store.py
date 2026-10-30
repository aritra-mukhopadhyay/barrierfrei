"""SQLite storage, so every later step reads cached data instead of calling APIs again."""

import json
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd

from barrierfrei.mapillary import Photo

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    segment_id TEXT PRIMARY KEY,
    u INTEGER NOT NULL,
    v INTEGER NOT NULL,
    length_m REAL,
    highway TEXT,
    tags TEXT NOT NULL,          -- JSON of the accessibility tags
    geometry TEXT NOT NULL       -- WKT in WGS84
);
CREATE TABLE IF NOT EXISTS photos (
    photo_id TEXT PRIMARY KEY,
    lon REAL NOT NULL,
    lat REAL NOT NULL,
    captured_at INTEGER,
    compass_angle REAL,
    is_pano INTEGER NOT NULL,
    thumb_url TEXT
);
CREATE TABLE IF NOT EXISTS segment_photos (
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    photo_id TEXT NOT NULL REFERENCES photos(photo_id),
    distance_m REAL NOT NULL,
    PRIMARY KEY (segment_id, photo_id)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _clean(value):
    # OSM tags can be lists (several values) or NaN (missing); store plain JSON values.
    if isinstance(value, list):
        return value
    return None if pd.isna(value) else value


def save_segments(conn: sqlite3.Connection, segments: gpd.GeoDataFrame, tag_columns: list[str]) -> None:
    present = [c for c in tag_columns if c in segments.columns]
    rows = []
    for row in segments.itertuples(index=False):
        record = row._asdict()
        tags = {c: _clean(record[c]) for c in present if c != "highway"}
        rows.append(
            (
                record["segment_id"],
                int(record["u"]),
                int(record["v"]),
                float(record["length"]),
                json.dumps(_clean(record.get("highway"))),
                json.dumps({k: v for k, v in tags.items() if v is not None}),
                record["geometry"].wkt,
            )
        )
    with conn:  # one transaction: all or nothing
        conn.executemany("INSERT OR REPLACE INTO segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def save_photos(conn: sqlite3.Connection, photos: list[Photo]) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO photos VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (p.id, p.lon, p.lat, p.captured_at, p.compass_angle, int(p.is_pano), p.thumb_url)
                for p in photos
            ],
        )


def save_matches(conn: sqlite3.Connection, matches: pd.DataFrame) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO segment_photos VALUES (?, ?, ?)",
            [(r.segment_id, r.photo_id, float(r.distance_m)) for r in matches.itertuples(index=False)],
        )
