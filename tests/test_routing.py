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


def test_steepest_incline_counts_whatever_the_order():
    for incline in (["0%", "15%"], ["15%", "0%"], "3%;-14%"):
        assert routing.segment_cost(seg("x", 1, 2, [A, B], incline=incline), "wheelchair")[0] is None


def test_wheelchair_no_beats_generic_ramp_yes():
    net = network(ramp="yes", **{"ramp:wheelchair": "no"})
    route = routing.walk(net, routing.build_graph(net, "wheelchair"), A, C)
    assert route.segment_ids == ["ab", "bd", "dc"]


def test_point_only_reachable_by_stairs_is_cut_off():
    # E sits at the top of a short staircase from B; the nearest usable node (B) is only 15 m away.
    E = (8.6412, 49.870)
    net = network()
    net.segments["be"] = seg("be", 2, 5, [B, E], highway="steps", length=15.0)
    net.node_coords[5] = E
    assert routing.walk(net, routing.build_graph(net, "wheelchair"), A, E) is None
    assert routing.walk(net, routing.build_graph(net, "suitcase"), A, E) is not None


def test_connector_hops_count_as_walking_cost():
    net = network()
    near_a = (8.6400, 49.8701)  # ~11 m north of A
    route = routing.walk(net, routing.build_graph(net, None), near_a, B)
    assert route.segment_ids == ["ab"] and route.cost > 100 + 10


def test_no_route_into_an_area_we_have_no_streets_for():
    net = network()
    frankfurt = (8.6826, 50.1144)  # ~28 km away: snapping there would invent a 28 km walk
    assert routing.walk(net, routing.build_graph(net, None), A, frankfurt) is None
    assert not routing.covered(net, frankfurt) and routing.covered(net, A)


def test_avoided_does_not_claim_what_the_route_still_crosses():
    net = network()
    net.segments["bd"] = seg("bd", 2, 4, [B, D], surface="cobblestone")
    net.segments["bc"] = seg("bc", 2, 3, [B, C], highway="steps", length=20.0, surface="cobblestone")
    accessible = routing.walk(net, routing.build_graph(net, "wheelchair"), A, C)
    direct = routing.walk(net, routing.build_graph(net, None), A, C)
    assert "rough surface" in accessible.notes
    assert routing.avoided(net, accessible, direct) == ["stairs"]


def test_station_pin_above_stairs_snaps_to_nearest_step_free_spot():
    # Same staircase as above, but E is a station's map pin, not a front door.
    E = (8.6412, 49.870)
    net = network()
    net.segments["be"] = seg("be", 2, 5, [B, E], highway="steps", length=15.0)
    net.node_coords[5] = E
    route = routing.walk(net, routing.build_graph(net, "wheelchair"), A, E, exact_end=False)
    assert route is not None and route.segment_ids == ["ab"]
    assert route.coords[-1] == B and E not in route.coords  # the drawn walk stops before the stairs
    assert route.length_m == 100


def test_bus_stop_tagged_wheelchair_no_is_not_a_wall():
    # A stop_position on Luisenplatz: wheelchair=no means the bus isn't low-floor, not that the square is.
    net = network()
    net.barrier_nodes = {4: {"wheelchair": "no"}}
    route = routing.walk(net, routing.build_graph(net, "wheelchair"), A, C)
    assert route.segment_ids == ["ab", "bd", "dc"]
    net.barrier_nodes = {4: {"barrier": "gate", "wheelchair": "no"}}  # a gate that is too narrow is
    assert routing.walk(net, routing.build_graph(net, "wheelchair"), A, C) is None


def test_odd_node_a_few_metres_closer_does_not_cut_off_a_point():
    # The closest node (5) only has stairs, but a usable node (B) is barely farther: start there.
    net = network()
    near_b = (8.6410, 49.87035)  # ~39 m north of B
    net.node_coords[5] = (8.64105, 49.87001)  # ~4 m from B and ~1 m closer to near_b
    net.segments["b5"] = seg("b5", 2, 5, [B, net.node_coords[5]], highway="steps", length=4.0)
    route = routing.walk(net, routing.build_graph(net, "wheelchair"), near_b, A)
    assert route is not None and route.segment_ids == ["ab"]


def test_door_at_top_of_a_short_staircase_is_still_cut_off():
    E = (8.64107, 49.870)  # ~5 m east of B, up three steps
    net = network()
    net.segments["be"] = seg("be", 2, 5, [B, E], highway="steps", length=5.0)
    net.node_coords[5] = E
    assert routing.walk(net, routing.build_graph(net, "wheelchair"), A, E) is None
