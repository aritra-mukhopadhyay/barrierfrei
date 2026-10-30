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
CREATE TABLE IF NOT EXISTS barrier_nodes (
    node_id INTEGER PRIMARY KEY, -- an OSM node with accessibility tags, e.g. barrier=kerb
    lon REAL NOT NULL,
    lat REAL NOT NULL,
    tags TEXT NOT NULL           -- JSON
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


def _tags(record: dict, columns: list[str]) -> str:
    tags = {c: _clean(record[c]) for c in columns}
    return json.dumps({k: v for k, v in tags.items() if v is not None})


def save_segments(conn: sqlite3.Connection, segments: gpd.GeoDataFrame, tag_columns: list[str]) -> None:
    present = [c for c in tag_columns if c in segments.columns and c != "highway"]
    rows = []
    # to_dict keeps original column names; itertuples would rename keys like "ramp:wheelchair".
    for record in segments.to_dict("records"):
        rows.append(
            (
                record["segment_id"],
                int(record["u"]),
                int(record["v"]),
                float(record["length"]),
                json.dumps(_clean(record.get("highway"))),
                _tags(record, present),
                record["geometry"].wkt,
            )
        )
    with conn:  # one transaction: all or nothing
        conn.executemany("INSERT OR REPLACE INTO segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def save_barrier_nodes(conn: sqlite3.Connection, nodes: gpd.GeoDataFrame, tag_columns: list[str]) -> None:
    present = [c for c in tag_columns if c in nodes.columns]
    rows = [
        (int(r["osmid"]), r["geometry"].x, r["geometry"].y, _tags(r, present))
        for r in nodes.to_dict("records")
    ]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO barrier_nodes VALUES (?, ?, ?, ?)", rows)


def save_photos(conn: sqlite3.Connection, photos: list[Photo]) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO photos VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (p.id, p.lon, p.lat, p.captured_at, p.compass_angle, int(p.is_pano), p.thumb_url)
                for p in photos
            ],
        )


def save_matches(conn: sqlite3.Connection, photo_ids: list[str], matches: pd.DataFrame) -> None:
    """Replace the links of every processed photo, so re-matched or unmatched photos leave no stale rows."""
    with conn:
        conn.executemany("DELETE FROM segment_photos WHERE photo_id = ?", [(pid,) for pid in photo_ids])
        conn.executemany(
            "INSERT INTO segment_photos VALUES (?, ?, ?)",
            [(r.segment_id, r.photo_id, float(r.distance_m)) for r in matches.itertuples(index=False)],
        )
