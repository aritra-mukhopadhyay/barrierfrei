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


# Node tags that mark obstacles at a point, e.g. a raised kerb at a crossing.
NODE_TAGS = ["barrier", "kerb", "highway", "crossing", "wheelchair", "step_count", "tactile_paving"]
BARRIER_NODE_KEYS = ["barrier", "kerb", "wheelchair", "step_count"]


def walk_network(bbox: tuple[float, float, float, float]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return (segments, barrier_nodes) for the walking network, in WGS84.

    Obstacles such as kerbs are often tagged on single OSM nodes, so we keep those nodes
    through graph simplification and return them separately.
    """
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | set(ACCESS_TAGS))
    ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | set(NODE_TAGS))
    graph = ox.graph_from_bbox(bbox, network_type="walk", simplify=False, retain_all=False)
    # Simplification would merge away mid-street nodes; keep any node with a barrier-type tag.
    graph = ox.simplify_graph(graph, node_attrs_include=BARRIER_NODE_KEYS)
    nodes, edges = ox.graph_to_gdfs(graph)
    edges = edges.reset_index()
    edges["segment_id"] = (
        edges["u"].astype(str) + "-" + edges["v"].astype(str) + "-" + edges["key"].astype(str)
    )
    nodes = nodes.reset_index()
    present = [k for k in BARRIER_NODE_KEYS if k in nodes.columns]
    barrier_nodes = nodes[nodes[present].notna().any(axis=1)] if present else nodes.iloc[0:0]
    log.info("Downloaded %d walk segments and %d barrier nodes", len(edges), len(barrier_nodes))
    return edges, barrier_nodes
