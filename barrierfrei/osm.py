"""Download the walking network from OpenStreetMap, keeping accessibility tags."""

import logging

import geopandas as gpd
import osmnx as ox

log = logging.getLogger(__name__)

# OSM tags that say something about step-free access.
ACCESS_TAGS = [
    "highway",
    "name",
    "surface",
    "smoothness",
    "incline",
    "width",
    "step_count",
    "kerb",
    "wheelchair",
    "ramp",
    "ramp:wheelchair",
    "handrail",
    "tactile_paving",
    "sidewalk",
    "footway",
    "crossing",
    "lit",
]


def walk_segments(bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Return one row per walkable street segment, in WGS84, with accessibility tags."""
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | set(ACCESS_TAGS))
    graph = ox.graph_from_bbox(bbox, network_type="walk", simplify=True, retain_all=False)
    edges = ox.graph_to_gdfs(graph, nodes=False).reset_index()
    edges["segment_id"] = (
        edges["u"].astype(str) + "-" + edges["v"].astype(str) + "-" + edges["key"].astype(str)
    )
    log.info("Downloaded %d walk segments", len(edges))
    return edges
