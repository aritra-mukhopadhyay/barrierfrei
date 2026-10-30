"""SQLite storage, so every later step reads cached data instead of calling APIs again."""

import json
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd

from barrierfrei.judge import Judgement
from barrierfrei.mapillary import Photo
from barrierfrei.sam import Detection

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
CREATE TABLE IF NOT EXISTS detections (
    photo_id TEXT NOT NULL REFERENCES photos(photo_id),
    prompt TEXT NOT NULL,
    object_id INTEGER NOT NULL,
    x1 INTEGER NOT NULL, y1 INTEGER NOT NULL, x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
    PRIMARY KEY (photo_id, prompt, object_id)
);
CREATE TABLE IF NOT EXISTS judgements (
    photo_id TEXT PRIMARY KEY REFERENCES photos(photo_id),
    model TEXT NOT NULL,
    judgement TEXT NOT NULL,     -- validated Judgement JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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


def photos_to_judge(conn: sqlite3.Connection, limit: int | None = None) -> list[tuple[str, str]]:
    """Photos linked to a path that have not been judged yet, closest to their path first."""
    sql = """
        SELECT p.photo_id, p.thumb_url
        FROM photos p
        JOIN (SELECT photo_id, MIN(distance_m) AS d FROM segment_photos GROUP BY photo_id) sp USING (photo_id)
        LEFT JOIN judgements j USING (photo_id)
        WHERE j.photo_id IS NULL AND p.thumb_url IS NOT NULL AND p.is_pano = 0
        ORDER BY sp.d
    """
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def save_perception(
    conn: sqlite3.Connection, photo_id: str, detections: list[Detection], judgement: Judgement, model: str
) -> None:
    with conn:
        conn.execute("DELETE FROM detections WHERE photo_id = ?", (photo_id,))
        conn.executemany(
            "INSERT INTO detections VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(photo_id, d.prompt, d.object_id, *d.box) for d in detections],
        )
        conn.execute(
            "INSERT OR REPLACE INTO judgements (photo_id, model, judgement) VALUES (?, ?, ?)",
            (photo_id, model, judgement.model_dump_json()),
        )
