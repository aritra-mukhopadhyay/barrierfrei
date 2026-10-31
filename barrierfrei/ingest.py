"""Step 1 of the pipeline: download the street network and photos, link them, cache in SQLite.

Run: python -m barrierfrei.ingest --area darmstadt   (or --area frankfurt)
Areas add up in the same database, so ingest each area you want to route in.
If Overpass is down, cut the area from a downloaded extract first (see osm_extract) and pass --osm-file.
"""

import argparse
import logging
from pathlib import Path

from barrierfrei import mapillary, osm, store
from barrierfrei.config import AREAS, get_settings, require
from barrierfrei.http import make_session
from barrierfrei.matching import match_photos, photos_to_gdf

log = logging.getLogger("barrierfrei.ingest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", choices=sorted(AREAS), help="named area; default is the BBOX setting")
    parser.add_argument(
        "--osm-file", type=Path, help="read streets from this OSM XML (see osm_extract) instead of Overpass"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    bbox = AREAS[args.area] if args.area else settings.bbox

    segments, barrier_nodes = osm.walk_network(bbox, settings.data_dir / "osmnx", args.osm_file)
    photos = mapillary.fetch_photos(
        make_session(), require(settings.mapillary_token, "MAPILLARY_TOKEN"), bbox
    )
    matches = match_photos(segments, photos_to_gdf(photos), settings.max_photo_distance_m)

    conn = store.connect(settings.db_path)
    if removed := store.clear_area(conn, bbox):
        log.info("Replacing %d existing segments in this area", removed)
    store.save_segments(conn, segments, osm.ACCESS_TAGS)
    store.save_photos(conn, photos)
    store.save_barrier_nodes(conn, barrier_nodes, osm.NODE_TAGS)
    store.save_matches(conn, [p.id for p in photos], matches)

    covered = matches["segment_id"].nunique()
    log.info(
        "Saved %d segments, %d photos, %d links to %s",
        len(segments),
        len(photos),
        len(matches),
        settings.db_path,
    )
    log.info(
        "Photo coverage: %d of %d segments (%.0f%%)",
        covered,
        len(segments),
        100 * covered / max(len(segments), 1),
    )


if __name__ == "__main__":
    main()
