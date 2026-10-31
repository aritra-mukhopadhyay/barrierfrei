from barrierfrei import routing
from barrierfrei.judge import Judgement
from barrierfrei.routing import Network, Segment


def seg(segment_id, u, v, coords, highway="footway", length=100.0, **tags):
    return Segment(segment_id, u, v, length, highway, tags, coords)


A, B, C, D = (8.640, 49.870), (8.641, 49.870), (8.642, 49.870), (8.6415, 49.8705)


def network(**step_tags) -> Network:
    # A - B - C is the short way, but B - C is a staircase; B - D - C is a longer step-free detour.
    segments = [
        seg("ab", 1, 2, [A, B]),
        seg("bc", 2, 3, [B, C], highway="steps", length=20.0, **step_tags),
        seg("bd", 2, 4, [B, D]),
        seg("dc", 4, 3, [D, C]),
    ]
    return Network({s.segment_id: s for s in segments}, {1: A, 2: B, 3: C, 4: D})


def test_wheelchair_route_avoids_stairs_that_shortest_path_takes():
    net = network()
    accessible = routing.walk(net, routing.build_graph(net, "wheelchair"), A, C)
    direct = routing.walk(net, routing.build_graph(net, None), A, C)
    assert direct.segment_ids == ["ab", "bc"]
    assert accessible.segment_ids == ["ab", "bd", "dc"]
    assert routing.avoided(net, accessible, direct) == ["stairs"]


def test_suitcase_may_take_short_stairs():
    net = network()
    route = routing.walk(net, routing.build_graph(net, "suitcase"), A, C)
    assert route.segment_ids == ["ab", "bc"]  # 20 m of stairs cost 20 * 6 + 30 = 150 < 200 m detour
    assert "stairs" in route.notes


def test_stairs_with_wheelchair_ramp_are_fine():
    net = network(**{"ramp:wheelchair": "yes"})
    route = routing.walk(net, routing.build_graph(net, "wheelchair"), A, C)
    assert route.segment_ids == ["ab", "bc"]


def test_raised_kerb_node_blocks_wheelchair_only():
    net = network()
    net.barrier_nodes = {4: {"barrier": "kerb", "kerb": "raised"}}
    assert routing.walk(net, routing.build_graph(net, "wheelchair"), A, C) is None  # no way left
    stroller = routing.walk(net, routing.build_graph(net, "stroller"), A, C)  # a kerb is a bump, not a wall
    assert stroller.segment_ids == ["ab", "bd", "dc"] and stroller.notes == ["raised kerb"]
    net.segments["bc"] = seg("bc", 2, 3, [B, C], **{"ramp:wheelchair": "yes"})
    assert routing.walk(net, routing.build_graph(net, "wheelchair"), A, C).segment_ids == ["ab", "bc"]


def test_photo_verdict_adds_penalty_only_when_confident():
    blocked = Judgement.model_validate(
        {
            "barriers": [{"type": "construction", "severity": "high", "reason": "fence"}],
            "passable_for": {"wheelchair": False, "stroller": False, "suitcase": True},
            "confidence": 0.9,
        }
    )
    unsure = blocked.model_copy(update={"confidence": 0.4})
    segment = seg("x", 1, 2, [A, B])
    cost, reasons = routing.segment_cost(segment, "wheelchair", [blocked])
    assert cost == 100 + routing.PHOTO_PENALTY_M and reasons == ["street photo shows construction"]
    assert routing.segment_cost(segment, "wheelchair", [unsure]) == (100.0, [])
    assert routing.segment_cost(segment, "suitcase", [blocked]) == (100.0, [])


def test_tag_rules():
    assert routing.segment_cost(seg("x", 1, 2, [A, B], wheelchair=["yes", "no"]), "wheelchair")[0] is None
    assert routing.segment_cost(seg("x", 1, 2, [A, B], incline="15%"), "wheelchair")[0] is None
    cost, reasons = routing.segment_cost(seg("x", 1, 2, [A, B], surface="sett"), "stroller")
    assert cost == 180 and reasons == ["rough surface"]


def test_haversine_is_metres():
    assert 70 < routing.haversine_m(A, B) < 75  # 0.001 degrees of longitude at 49.87 N
