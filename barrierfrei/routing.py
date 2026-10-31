"""Step-free walking routes on the cached OSM network, with costs per mobility profile.

Every segment gets a cost in "effective metres": its length, times a factor for rough surfaces or
slopes, plus fixed penalties for obstacles. A cost of None means the segment is impassable for that
profile (stairs for a wheelchair), so the router never uses it.
"""

import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

import networkx as nx
from shapely import wkt

from barrierfrei.judge import Judgement

log = logging.getLogger(__name__)

Profile = Literal["wheelchair", "stroller", "suitcase"]
PROFILES: tuple[Profile, ...] = ("wheelchair", "stroller", "suitcase")

ROUGH_SURFACES = {
    "cobblestone",
    "sett",
    "unhewn_cobblestone",
    "gravel",
    "pebblestone",
    "grass",
    "sand",
    "dirt",
}
BAD_SMOOTHNESS = {"bad", "very_bad", "horrible", "very_horrible", "impassable"}
# A photo verdict only counts when the model is reasonably sure; it is evidence, not ground truth.
MIN_PHOTO_CONFIDENCE = 0.7
PHOTO_PENALTY_M = 400.0  # strong enough to detour around, weak enough to use if there is no other way
MAX_SNAP_M = 150.0  # farther than this from any mapped street means we have no data there
MAX_EXTRA_SNAP_M = 50.0  # how much farther than the nearest street a route may start or end
ON_NODE_M = 5.0  # a point this close to a node is at that node (e.g. the top of a staircase)
CUT_OFF_MARGIN_M = 10.0  # a point counts as cut off only if the nearest usable node is this much farther


@dataclass(frozen=True)
class Segment:
    segment_id: str
    u: int
    v: int
    length_m: float
    highway: str | None
    tags: dict
    coords: list[tuple[float, float]]  # (lon, lat), from u to v


@dataclass
class Network:
    segments: dict[str, Segment]
    node_coords: dict[int, tuple[float, float]]
    barrier_nodes: dict[int, dict] = field(default_factory=dict)
    judgements: dict[str, list[Judgement]] = field(default_factory=dict)  # segment_id -> photo verdicts


def _values(tag) -> list[str]:
    """OSM values can be a single string, a list (several ways merged), or 'a;b'."""
    if tag is None:
        return []
    items = tag if isinstance(tag, list) else [tag]
    return [part.strip().lower() for item in items for part in str(item).split(";")]


def _incline_percent(tag) -> float | None:
    """Steepest numeric incline; merged ways can carry several values and any steep part counts."""
    found = [float(m.group(1)) for v in _values(tag) if (m := re.fullmatch(r"-?(\d+(?:\.\d+)?)\s*%", v))]
    return max(found) if found else None


def segment_cost(
    segment: Segment, profile: Profile, judgements: list[Judgement] = ()
) -> tuple[float | None, list[str]]:
    """Return (cost in effective metres or None if impassable, human-readable reasons)."""
    tags = segment.tags
    highway = _values(segment.highway)
    reasons: list[str] = []
    cost = segment.length_m
    # A wheelchair-specific "no" wins over a generic ramp=yes (that ramp may be for bicycles).
    wheelchair_ramp = _values(tags.get("ramp:wheelchair"))
    has_ramp = "yes" in wheelchair_ramp or (
        "no" not in wheelchair_ramp and "yes" in _values(tags.get("ramp"))
    )

    if "steps" in highway and not has_ramp:
        if profile in ("wheelchair", "stroller"):
            return None, ["stairs"]
        cost = segment.length_m * 6 + 30  # carrying a suitcase up stairs is possible but slow
        reasons.append("stairs")
    if "elevator" in highway:
        reasons.append("elevator")

    if "no" in _values(tags.get("wheelchair")):
        if profile == "wheelchair":
            return None, ["tagged wheelchair=no"]
        cost += 100
        reasons.append("tagged not wheelchair-accessible")
    if "raised" in _values(tags.get("kerb")):
        if profile == "wheelchair":
            return None, ["raised kerb"]
        cost += 20
        reasons.append("raised kerb")

    if (incline := _incline_percent(tags.get("incline"))) is not None:
        if profile == "wheelchair" and incline > 12:
            return None, [f"{incline:g}% slope"]
        if incline > 6:
            cost *= 1 + incline / 10
            reasons.append(f"{incline:g}% slope")
    if ROUGH_SURFACES & set(_values(tags.get("surface"))):
        cost *= 1.8 if profile in ("wheelchair", "stroller") else 1.3
        reasons.append("rough surface")
    if BAD_SMOOTHNESS & set(_values(tags.get("smoothness"))):
        if profile == "wheelchair":
            return None, ["very uneven surface"]
        cost *= 1.5
        reasons.append("uneven surface")

    for verdict in judgements:
        if verdict.confidence >= MIN_PHOTO_CONFIDENCE and not getattr(verdict.passable_for, profile):
            cost += PHOTO_PENALTY_M
            reasons.append(
                "street photo shows " + (", ".join(b.type for b in verdict.barriers) or "a barrier")
            )
            break
    return cost, reasons


def node_cost(tags: dict, profile: Profile) -> tuple[float | None, str | None]:
    """Obstacles mapped on a single point, e.g. a raised kerb at a crossing."""
    kerb = _values(tags.get("kerb"))
    if "raised" in kerb:
        return (None, "raised kerb") if profile == "wheelchair" else (15.0, "raised kerb")
    # On a point, wheelchair=no only blocks the way if the point is an obstacle (a gate, a crossing).
    # On a bus stop or shop node it describes that venue, not whether you can roll past it.
    obstacle = tags.get("barrier") or set(_values(tags.get("highway"))) & {"crossing", "elevator"}
    if obstacle and "no" in _values(tags.get("wheelchair")) and profile == "wheelchair":
        return None, "tagged wheelchair=no"
    if "steps" in _values(tags.get("barrier")) or _values(tags.get("step_count")):
        return (None, "step") if profile in ("wheelchair", "stroller") else (10.0, "step")
    return 0.0, None


def load_network(conn: sqlite3.Connection) -> Network:
    segments = {}
    node_coords: dict[int, tuple[float, float]] = {}
    for segment_id, u, v, length_m, highway, tags, geometry in conn.execute(
        "SELECT segment_id, u, v, length_m, highway, tags, geometry FROM segments"
    ):
        coords = [(x, y) for x, y in wkt.loads(geometry).coords]
        segments[segment_id] = Segment(
            segment_id, u, v, length_m or 0.0, json.loads(highway), json.loads(tags), coords
        )
        node_coords.setdefault(u, coords[0])
        node_coords.setdefault(v, coords[-1])
    barrier_nodes = {
        node_id: json.loads(tags) for node_id, tags in conn.execute("SELECT node_id, tags FROM barrier_nodes")
    }
    judgements: dict[str, list[Judgement]] = {}
    for segment_id, verdict in conn.execute(
        "SELECT sp.segment_id, j.judgement FROM segment_photos sp JOIN judgements j USING (photo_id)"
    ):
        judgements.setdefault(segment_id, []).append(Judgement.model_validate_json(verdict))
    log.info("Loaded %d segments, %d judged", len(segments), len(judgements))
    return Network(segments, node_coords, barrier_nodes, judgements)


def build_graph(network: Network, profile: Profile | None) -> nx.Graph:
    """Undirected graph keeping the cheapest segment between two nodes. profile=None ignores accessibility."""
    graph = nx.Graph(profile=profile)
    for seg in network.segments.values():
        if profile is None:
            cost, reasons = seg.length_m, []
        else:
            cost, reasons = segment_cost(seg, profile, network.judgements.get(seg.segment_id, []))
            if cost is not None:
                for node in (seg.u, seg.v):
                    extra, why = node_cost(network.barrier_nodes.get(node, {}), profile)
                    if extra is None:
                        cost = None
                        break
                    # Half per end: a route passes through a node once but touches two of its segments.
                    cost += extra / 2
                    if why:
                        reasons.append(why)
        if cost is None:
            continue
        current = graph.get_edge_data(seg.u, seg.v)
        if current is None or cost < current["cost"]:
            graph.add_edge(seg.u, seg.v, cost=cost, segment_id=seg.segment_id, reasons=reasons)
    return graph


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def nearest_node(
    graph: nx.Graph, network: Network, point: tuple[float, float], min_component: int = 50
) -> int:
    """Closest graph node to (lon, lat), skipping tiny islands such as a lone mapped platform."""
    if "routable" not in graph.graph:  # computed once per graph
        graph.graph["routable"] = {
            node
            for component in nx.connected_components(graph)
            if len(component) >= min_component
            for node in component
        }
    candidates = graph.graph["routable"] or set(graph.nodes)
    if not candidates:
        raise ValueError("The walking network is empty; run ingest for this area first")
    return min(candidates, key=lambda n: haversine_m(network.node_coords[n], point))


@dataclass
class WalkRoute:
    profile: Profile | None
    length_m: float
    cost: float
    coords: list[tuple[float, float]]
    segment_ids: list[str]
    notes: list[str]  # obstacles the route still has to cross (e.g. "rough surface")


def walk(
    network: Network,
    graph: nx.Graph,
    start: tuple[float, float],
    end: tuple[float, float],
    exact_start: bool = True,
    exact_end: bool = True,
) -> WalkRoute | None:
    """Cheapest route between two (lon, lat) points, or None if no passable route exists.

    An exact point (a front door) must be reachable itself. A non-exact one (a station's map pin,
    which may sit on a platform or above a staircase) only marks an area: we go to its nearest
    step-free spot.
    """
    source, target = nearest_node(graph, network, start), nearest_node(graph, network, end)
    # If the closest usable node is much farther than the closest street of any kind, the point
    # itself is cut off (e.g. only reachable by stairs): say so instead of stopping short.
    for point, node, exact in ((start, source, exact_start), (end, target, exact_end)):
        closest = min(network.node_coords, key=lambda n: haversine_m(network.node_coords[n], point))
        snapped = haversine_m(network.node_coords[node], point)
        closest_m = haversine_m(network.node_coords[closest], point)
        if closest_m > MAX_SNAP_M:  # no street data here at all (area not ingested)
            return None
        if not exact:
            if snapped > MAX_SNAP_M:
                return None
            continue  # the walk then starts or ends at the snapped node itself, see below
        # The closest node only has impassable segments (e.g. the top of a staircase): cut off.
        if closest not in graph and (closest_m <= ON_NODE_M or snapped > closest_m + CUT_OFF_MARGIN_M):
            return None
        if snapped > closest_m + MAX_EXTRA_SNAP_M:
            return None
    # A non-exact point is replaced by its step-free node, so the line never crosses the stairs.
    start = start if exact_start else network.node_coords[source]
    end = end if exact_end else network.node_coords[target]
    try:
        nodes = nx.shortest_path(graph, source, target, weight="cost")
    except nx.NetworkXNoPath:
        return None
    coords: list[tuple[float, float]] = [start]
    segment_ids, notes = [], []
    length = cost = 0.0
    for a, b in zip(nodes, nodes[1:], strict=False):
        edge = graph.edges[a, b]
        seg = network.segments[edge["segment_id"]]
        part = seg.coords if seg.u == a else list(reversed(seg.coords))
        coords.extend(part)
        segment_ids.append(seg.segment_id)
        notes.extend(r for r in edge["reasons"] if r not in notes)
        length += seg.length_m
        cost += edge["cost"]
    coords.append(end)
    connectors = haversine_m(start, network.node_coords[source]) + haversine_m(
        end, network.node_coords[target]
    )
    length += connectors
    cost += connectors  # the short hops onto the network cost plain walking distance
    return WalkRoute(graph.graph.get("profile"), length, cost, coords, segment_ids, notes)


def covered(network: Network, point: tuple[float, float], radius_m: float = 200.0) -> bool:
    """Is this point inside the area whose streets we have downloaded?"""
    return any(haversine_m(c, point) <= radius_m for c in network.node_coords.values())


def avoided(network: Network, accessible: WalkRoute, direct: WalkRoute) -> list[str]:
    """What the plain shortest route would have crossed that the accessible one avoids."""
    profile = accessible.profile
    if profile is None:
        return []
    found = []
    for segment_id in direct.segment_ids:
        if segment_id in accessible.segment_ids:
            continue
        seg = network.segments[segment_id]
        cost, reasons = segment_cost(seg, profile, network.judgements.get(segment_id, []))
        # Only claim what the chosen route really avoids, not what it still crosses elsewhere.
        found.extend(r for r in reasons if r not in found and r not in accessible.notes)
    return found
