"""Download the walking network from OpenStreetMap, keeping accessibility tags."""

import logging
import re
from pathlib import Path

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


# Node tags that mark obstacles at a point, e.g. a raised kerb at a crossing.
NODE_TAGS = ["barrier", "kerb", "highway", "crossing", "wheelchair", "step_count", "tactile_paving"]
BARRIER_NODE_KEYS = ["barrier", "kerb", "wheelchair", "step_count"]
# Never merge two ways into one segment across a change in these: one bad stretch would otherwise
# give its tag to the whole merged segment (a 15 m rough patch blocking a 160 m forecourt).
SPLIT_ON = [
    "highway",
    "surface",
    "smoothness",
    "incline",
    "wheelchair",
    "step_count",
    "kerb",
    "ramp",
    "ramp:wheelchair",
]


# The same rules osmnx sends to Overpass for network_type="walk", for filtering an offline extract.
# Each pair is (tag, regex); a way is excluded if the tag's value matches anywhere, as in Overpass "!~".
WALK_EXCLUDE = [
    ("area", "yes"),
    ("access", "private"),
    (
        "highway",
        "abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|proposed|raceway"
        "|razed|rest_area|services",
    ),
    ("foot", "no"),
    ("service", "private"),
    ("sidewalk", "separate"),
    ("sidewalk:both", "separate"),
    ("sidewalk:left", "separate"),
    ("sidewalk:right", "separate"),
]


def walkable(tags: dict) -> bool:
    """Would osmnx's walk network include a way with these tags?"""
    if "highway" not in tags:
        return False
    return not any(key in tags and re.search(pattern, tags[key]) for key, pattern in WALK_EXCLUDE)


def walk_network(
    bbox: tuple[float, float, float, float], cache_dir: Path, osm_file: Path | None = None
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return (segments, barrier_nodes) for the walking network, in WGS84.

    Downloads from Overpass, or reads osm_file (OSM XML from osm_extract, already filtered to
    walkable ways) when given. Obstacles such as kerbs are often tagged on single OSM nodes, so we
    keep those nodes through graph simplification and return them separately.
    """
    # osmnx caches Overpass responses in ./cache by default; keep them under the gitignored data dir.
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | set(ACCESS_TAGS))
    ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | set(NODE_TAGS))
    if osm_file is None:
        graph = ox.graph_from_bbox(bbox, network_type="walk", simplify=False, retain_all=False)
    else:
        # Walking works both ways along a one-way street, hence bidirectional.
        graph = ox.graph_from_xml(osm_file, bidirectional=True, simplify=False, retain_all=True)
        graph = ox.truncate.truncate_graph_bbox(graph, bbox)  # same as graph_from_bbox
        graph = ox.truncate.largest_component(graph)  # same as retain_all=False above
    # Simplification would merge away mid-street nodes; keep any node with a barrier-type tag.
    graph = ox.simplify_graph(graph, node_attrs_include=BARRIER_NODE_KEYS, edge_attrs_differ=SPLIT_ON)
    nodes, edges = ox.graph_to_gdfs(graph)
    edges = edges.reset_index()
    edges["segment_id"] = (
        edges["u"].astype(str) + "-" + edges["v"].astype(str) + "-" + edges["key"].astype(str)
    )
    nodes = nodes.reset_index()
    present = [k for k in BARRIER_NODE_KEYS if k in nodes.columns]
    barrier_nodes = nodes[nodes[present].notna().any(axis=1)] if present else nodes.iloc[0:0]
    log.info("Loaded %d walk segments and %d barrier nodes", len(edges), len(barrier_nodes))
    return edges, barrier_nodes
