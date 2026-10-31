"""Cut one area's walkable streets out of a Geofabrik .osm.pbf, so ingest works without Overpass.

Overpass (the free OSM query API) can be down for hours; a demo should not depend on it. Download a
regional extract once, e.g. https://download.geofabrik.de/europe/germany/hessen-latest.osm.pbf, then:

    pip install "osmium>=4,<5"
    python -m barrierfrei.osm_extract hessen-latest.osm.pbf --area frankfurt
    python -m barrierfrei.ingest --area frankfurt --osm-file data/osm/frankfurt.osm
"""

import argparse
import logging
from pathlib import Path

from barrierfrei.config import AREAS, get_settings
from barrierfrei.osm import walkable

log = logging.getLogger("barrierfrei.osm_extract")


def extract(source: Path, bbox: tuple[float, float, float, float], target: Path) -> int:
    """Write every walkable way touching bbox, plus its nodes (with their tags), to an OSM XML file."""
    import osmium  # optional dependency, only needed for offline ingest

    west, south, east, north = bbox
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    count = 0
    # remove_tags=False keeps tags such as kerb=raised on the nodes the ways reference.
    with osmium.BackReferenceWriter(str(target), ref_src=str(source), remove_tags=False) as writer:
        # Nodes are read only to fill the location cache; the C++ filters keep the millions of
        # them, and every way without a highway tag, from ever reaching this Python loop.
        processor = (
            osmium.FileProcessor(str(source), osmium.osm.NODE | osmium.osm.WAY)
            .with_locations()
            .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
            .with_filter(osmium.filter.KeyFilter("highway"))
        )
        for obj in processor:
            if not walkable(dict(obj.tags)):
                continue
            # Keep the whole way if any node is inside: osmnx clips to the bbox later.
            if any(
                n.location.valid() and west <= n.lon <= east and south <= n.lat <= north for n in obj.nodes
            ):
                writer.add_way(obj)
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path, help="a .osm.pbf extract, e.g. hessen-latest.osm.pbf")
    parser.add_argument("--area", choices=sorted(AREAS), required=True)
    parser.add_argument("--out", type=Path, help="default: <data dir>/osm/<area>.osm")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    target = args.out or get_settings().data_dir / "osm" / f"{args.area}.osm"
    count = extract(args.source, AREAS[args.area], target)
    log.info("Wrote %d walkable ways to %s", count, target)


if __name__ == "__main__":
    main()
