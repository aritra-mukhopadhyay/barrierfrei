"""Step 1 of the pipeline: download the street network and photos, link them, cache in SQLite.

Run: python -m barrierfrei.ingest
"""

import logging

from barrierfrei import mapillary, osm, store
from barrierfrei.config import get_settings, require
from barrierfrei.http import make_session
from barrierfrei.matching import match_photos, photos_to_gdf

log = logging.getLogger("barrierfrei.ingest")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    segments, barrier_nodes = osm.walk_network(settings.bbox)
    photos = mapillary.fetch_photos(
        make_session(), require(settings.mapillary_token, "MAPILLARY_TOKEN"), settings.bbox
    )
    matches = match_photos(segments, photos_to_gdf(photos), settings.max_photo_distance_m)

    conn = store.connect(settings.db_path)
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
