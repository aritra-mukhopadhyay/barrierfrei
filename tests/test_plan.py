import logging

import pytest

from barrierfrei import mapview, plan, stations, transit
from barrierfrei.config import AREAS
from barrierfrei.routing import Network, Segment

ORIGIN = (8.6400, 49.8726)  # in Darmstadt, ~760 m east of the Hbf
DARMSTADT_LIFT = (8.6300911, 49.8725678)
MID = (8.6350, 49.8726)
HAUPTWACHE_LIFT = (8.67818, 50.114046)
ZEIL = (8.6826, 50.1144)

FASTA = {
    1126: {
        "stationnumber": 1126,
        "facilities": [
            {
                "equipmentnumber": 10543453,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "zu Gleis 3/4",
                "geocoordX": DARMSTADT_LIFT[0],
                "geocoordY": DARMSTADT_LIFT[1],
            }
        ],
    },
    1866: {
        "stationnumber": 1866,
        "facilities": [
            {
                "equipmentnumber": 10561326,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "Querbahnsteig <-> Gl.101/102",
                "geocoordX": 8.6628995,
                "geocoordY": 50.1072933,
            },
            {
                "equipmentnumber": 10955285,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "Bahnhofsvorplatz <-> B-Ebene",
                "geocoordX": 8.662759,
                "geocoordY": 50.108021,
            },
        ],
    },
    1864: {
        "stationnumber": 1864,
        "facilities": [
            {
                "equipmentnumber": 10351032,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "zu Gleis 2/3 (S-Bahn)",
                "geocoordX": HAUPTWACHE_LIFT[0],
                "geocoordY": HAUPTWACHE_LIFT[1],
            }
        ],
    },
    1865: {"stationnumber": 1865, "facilities": []},
    1857: {"stationnumber": 1857, "facilities": []},
}


def network() -> Network:
    segments = [
        Segment("o-m", 1, 2, 370, "footway", {}, [ORIGIN, MID]),
        Segment("m-d", 2, 3, 360, "footway", {}, [MID, DARMSTADT_LIFT]),
        Segment("h-z", 10, 11, 330, "pedestrian", {}, [HAUPTWACHE_LIFT, ZEIL]),
    ]
    coords = {1: ORIGIN, 2: MID, 3: DARMSTADT_LIFT, 10: HAUPTWACHE_LIFT, 11: ZEIL}
    return Network({s.segment_id: s for s in segments}, coords)


def lookup_with(outages=frozenset()):
    def lookup(number):
        return stations.with_outages(stations.parse_station(FASTA[number], 0), set(outages))

    return lookup


def test_plan_picks_fastest_trip_with_working_elevators():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with())
    best = options[0]
    assert best.usable
    assert [r.destination for r in best.journey.rides][-1] == "Frankfurt (Main) Hauptwache"
    assert [c.status for c in best.checks][0] == "ok"
    assert best.walk_start.segment_ids == ["o-m", "m-d"]
    assert best.walk_end.segment_ids == ["h-z"]
    # Konstablerwache's lifts are not in DB's data: never "broken", only "unverified".
    konst = [o for o in options if o.checks[-1].name.endswith("Konstablerwache")]
    assert konst and konst[0].checks[-1].status == "unverified"


def test_broken_elevator_rejects_every_trip_through_it():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with({10351032}))
    through_hauptwache = [o for o in options if o.checks[-1].name.endswith("Hauptwache")]
    assert through_hauptwache and not any(o.usable for o in through_hauptwache)
    assert all(o.checks[-1].status == "broken" for o in through_hauptwache)


def test_summary_and_map_render():
    options = plan.plan(ORIGIN, ZEIL, "stroller", network(), transit.Static(), lookup_with())
    result = plan.summary(options, "stroller") | {"from": "A", "to": "B"}
    assert result["options"][0]["usable"]
    assert {"leave_at", "arrive_at", "later_departures"} <= result["options"][0].keys()
    page = mapview.render(result, options, ORIGIN, ZEIL)
    assert (
        "Best we can offer" in page and "leaflet" in page and "10351032" in page
    )  # test stations are all unverifiable
    # CARTO's basemaps now require an API key; OSM's own tiles load (checked in Chrome and Edge).
    assert "tile.openstreetmap.org" in page and "cartocdn.com" not in page
    assert 'location.protocol === "file:"' in page


def test_place_parsing():
    assert plan.place("zeil")[0] == "Zeil, Frankfurt"
    assert plan.place("50.1,8.6") == ("50.1,8.6", 8.6, 50.1)


def test_far_off_elevator_coordinates_are_ignored():
    data = {
        "stationnumber": 1866,
        "facilities": [
            {
                "equipmentnumber": 1,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "x",
                "geocoordX": 8.62106,
                "geocoordY": 50.106461,
            }
        ],
    }  # real FaSta entry ~3 km west of the Hbf
    check = plan.check_station("Frankfurt (Main) Hbf", None, lambda n: stations.parse_station(data, 0))
    assert check.status == "ok" and check.use[0].lon is None
    north = transit.find_stop("Frankfurt (Main) Hbf").entrances[0]
    assert plan.entrance(check) == (north.lon, north.lat)  # a mapped door, not the bad lift position


def _via_hbf(options):
    return next(o for o in options if len(o.journey.rides) == 2)


def test_same_station_transfer_needs_only_the_platform_lift():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with({10955285}))
    via_hbf = _via_hbf(options)
    assert via_hbf.usable
    assert [c.status for c in via_hbf.checks[1:3]] == ["ok", "ok"]
    assert [e.equipment for e in via_hbf.checks[2].use] == [10561326]


def test_hauptwache_platform_lift_does_not_verify_the_street_lifts():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with())
    arrival = options[0].checks[-1]
    assert arrival.name.endswith("Hauptwache") and arrival.status == "unverified"
    assert "VGF" in arrival.reason
    lift = transit.find_stop(arrival.name).entrances[0]  # VGF's street lift: we walk there, unverified
    assert plan.entrance(arrival) == (lift.lon, lift.lat) and "VGF" in lift.name


def test_full_lift_chain_is_reported():
    check = plan.check_station("Frankfurt (Main) Hbf", "101", lookup_with())
    assert [e.equipment for e in check.use] == [10561326, 10955285]


class Timed:
    """A provider returning one live-looking journey that departs at a fixed time."""

    def __init__(self, departs, legs=None):
        self.departs, self.legs = departs, legs

    def journeys(self, origin, destination, results=3, departure=None):
        from datetime import timedelta

        if (origin.name, destination.name) != ("Darmstadt Hbf", "Frankfurt (Main) Hauptwache"):
            return []
        legs = self.legs or [
            transit.Leg(
                "suburban",
                "S3",
                origin.name,
                destination.name,
                self.departs,
                self.departs + timedelta(minutes=37),
                None,
                None,
                37,
            )
        ]
        return [transit.Journey(legs, "test")]


def test_train_leaving_before_you_arrive_is_rejected():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0).astimezone()
    too_soon = plan.plan(
        ORIGIN, ZEIL, "wheelchair", network(), Timed(now + timedelta(minutes=2)), lookup_with(), now
    )
    assert too_soon == []
    later = plan.plan(
        ORIGIN, ZEIL, "wheelchair", network(), Timed(now + timedelta(minutes=30)), lookup_with(), now
    )
    assert later[0].usable


def test_missed_train_is_not_offered():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0).astimezone()
    options = plan.plan(
        ORIGIN, ZEIL, "wheelchair", network(), Timed(now + timedelta(minutes=2)), lookup_with(), now
    )
    assert options == []


def test_walk_between_two_stations_is_checked():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0).astimezone()
    t = lambda m: now + timedelta(minutes=m)  # noqa: E731
    legs = [
        transit.Leg("regional", "RE", "Darmstadt Hbf", "Somewhere Nord", t(30), t(45), None, None, 15),
        transit.Leg("walk", None, "Somewhere Nord", "Somewhere Süd", t(45), t(50), None, None, 5),
        transit.Leg(
            "subway", "U4", "Somewhere Süd", "Frankfurt (Main) Hauptwache", t(55), t(60), None, None, 5
        ),
    ]
    (option,) = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), Timed(t(30), legs), lookup_with(), now)
    assert [w.status for w in option.transfer_walks] == ["unverified"]  # outside the mapped streets


def test_unknown_platform_names_no_specific_lift():
    check = plan.check_station("Darmstadt Hbf", None, lookup_with())
    assert plan.lift_text(check, check.use[0]) == "the lift to your platform (1 of 1 platform lifts working)"
    # Don't walk to a platform lift that may be the wrong one: use the station's step-free doors.
    assert [d[2] for d in plan.doors(check)] == [
        "main entrance (Haupteingang)",
        "entrance Am Fürstenbahnhof",
    ]
    assert all(exact for _, exact, _ in plan.doors(check))


def test_station_roles_are_labelled():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with())
    via_hbf = _via_hbf(options)
    assert [c.role for c in via_hbf.checks] == ["board", "change (arrive)", "change (depart)", "alight"]


def test_verified_trip_ranks_above_faster_unverified_one():
    def option(minutes, status):
        leg = transit.Leg("suburban", "S3", "a", "b", None, None, None, None, minutes)
        walk = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        return plan.Option(
            transit.Journey([leg], "test"),
            [plan.StationCheck("a", None, "ok"), plan.StationCheck("b", None, status)],
            walk_start=walk,
            walk_end=walk,
        )

    fast_unverified, slow_verified = option(20, "unverified"), option(40, "ok")
    assert plan.rank([fast_unverified, slow_verified]) == [slow_verified, fast_unverified]


def test_destination_outside_the_mapped_area_is_explained():
    far = (8.70, 50.20)  # ~10 km north of anything in the test network
    options = plan.plan(ORIGIN, far, "wheelchair", network(), transit.Static(), lookup_with())
    assert options == []  # no station near it either
    net = network()
    del net.segments["h-z"]
    for n in (10, 11):
        del net.node_coords[n]
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", net, transit.Static(), lookup_with())
    assert options and not any(o.usable for o in options)
    assert "no street data near the destination; run ingest for that area" in options[0].problems


def test_known_lift_entrance_must_be_reachable_itself():
    check = plan.check_station("Frankfurt (Main) Hbf", "101", lookup_with())
    lift = plan.entrance_lift(check)
    assert lift is not None and lift.equipment == 10955285  # exact: stairs-only lift door is cut off
    assert plan.entrance_lift(plan.check_station("Darmstadt Hbf", None, lookup_with())) is None  # pin


def test_shown_minutes_use_walking_speed_not_route_penalties():
    leg = transit.Leg("suburban", "S3", "a", "b", None, None, None, None, 30)
    cobbled = plan.routing.WalkRoute("wheelchair", 600, 1080, [], [], ["rough surface"])  # cost x1.8
    option = plan.Option(
        transit.Journey([leg], "test"),
        [plan.StationCheck("a", None, "ok"), plan.StationCheck("b", None, "unverified")],
        walk_start=cobbled,
        walk_end=cobbled,
    )
    # 1200 m at 60 m/min, the first lift, the ride; no surface or "unverified" penalty on the clock.
    assert option.minutes == 20 + plan.BOARDING_BUFFER_MIN + 30
    assert option.score == 36 + plan.BOARDING_BUFFER_MIN + 30 + plan.UNVERIFIED_PENALTY_MIN


def test_panel_explains_a_slower_recommendation():
    verified = {"usable": True, "fully_verified": True, "minutes": 70}
    faster = {"usable": True, "fully_verified": False, "minutes": 60}
    assert "10 min slower" in mapview._why_best(verified, [verified, faster])
    assert "live data" in mapview._why_best(verified, [verified, faster])
    assert mapview._why_best(faster, [verified, faster]) == ""


def test_transfer_walks_are_counted_once_and_stale_status_is_not_verified():
    rides = [
        transit.Leg("suburban", "S1", "a", "b", None, None, None, None, 10),
        transit.Leg("walk", None, "b", "c", None, None, None, None, 10),
        transit.Leg("subway", "U4", "c", "d", None, None, None, None, 10),
    ]
    transfer = plan.routing.WalkRoute("wheelchair", 600, 600, [], [], [])
    option = plan.Option(
        transit.Journey(rides, "test"),
        [plan.StationCheck("a", None, "ok"), plan.StationCheck("d", None, "ok", stale=True)],
        [plan.TransferWalk("b", "c", transfer, "ok")],
    )
    assert option.minutes == 30 + plan.BOARDING_BUFFER_MIN  # the walk leg is already in the journey
    assert not option.verified  # a cached lift status is not a confirmation


def test_walk_ends_at_the_cheapest_station_door_and_says_which():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with())
    best = options[0]
    # In this toy network both doors hang off one node; Fürstenbahnhof is a couple of metres nearer.
    assert best.board_via == "entrance Am Fürstenbahnhof"
    summary = plan.summary(options, "wheelchair")["options"][0]
    assert summary["walk_to_station"]["via"] == best.board_via


def test_station_without_mapped_doors_falls_back_to_its_pin():
    check = plan.StationCheck("Nowhere", None, "unverified")
    assert plan.doors(check) == []
    (door,) = plan.doors(plan.StationCheck("Frankfurt (Main) Konstablerwache", None, "unverified"))
    assert door[1] is True and "Zeil" in door[2]


def test_broken_lift_is_named_in_the_problems():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with({10561326}))
    via_hbf = _via_hbf(options)
    assert not via_hbf.usable
    assert any(b.startswith("Frankfurt (Main) Hbf: every elevator") for b in via_hbf.blockers)
    summary = plan.summary([via_hbf], "wheelchair")["options"][0]
    assert summary["problems"] == via_hbf.blockers


def test_panel_says_when_no_trip_is_fully_confirmed():
    options = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), transit.Static(), lookup_with())
    result = plan.summary(options, "wheelchair") | {"from": "a", "to": "b"}
    assert not any(o["fully_verified"] for o in result["options"] if o["usable"])
    page = mapview.render(result, options, ORIGIN, ZEIL)
    assert "Best we can offer" in page and "No trip right now is fully confirmed" in page


def _timed_option(departs, destination="Frankfurt (Main) Hauptwache", status="ok"):
    from datetime import timedelta

    arrival = departs + timedelta(minutes=35) if departs else None
    leg = transit.Leg("suburban", "S6", "Darmstadt Hbf", destination, departs, arrival, "2", None, 35)
    walk = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
    checks = [plan.StationCheck("Darmstadt Hbf", "2", "ok"), plan.StationCheck(destination, None, status)]
    return plan.Option(
        transit.Journey([leg], "test"),
        checks,
        walk_start=walk,
        walk_end=walk,
        board_via="entrance Am Fürstenbahnhof",
        alight_via="VGF street lift (exit 2)",
    )


def test_collapse_keeps_earliest_and_lists_later():
    from datetime import datetime

    t = lambda h, m: datetime(2026, 11, 3, h, m)  # noqa: E731
    other = _timed_option(t(12, 40), "Frankfurt (Main) Konstablerwache")
    ranked = [_timed_option(t(13, 35)), other, _timed_option(t(12, 35)), _timed_option(t(13, 5))]
    (kept, still_other) = plan.collapse(ranked)
    assert kept.journey.rides[0].departure == t(12, 35)
    assert kept.later == [t(13, 5), t(13, 35)]
    assert still_other is other and other.later == []


def test_collapse_keeps_verified_and_unverified_apart():
    from datetime import datetime

    departs = datetime(2026, 11, 3, 12, 35)
    options = plan.collapse([_timed_option(departs), _timed_option(departs, status="unverified")])
    assert len(options) == 2
    assert all(o.later == [] for o in options)


def test_leave_and_arrive_times():
    from datetime import datetime, timedelta

    departs = datetime(2026, 11, 3, 12, 35)
    arrives = departs + timedelta(minutes=35)
    start = plan.routing.WalkRoute("wheelchair", 600, 600, [], [], [])  # 10 min at 60 m/min
    end = plan.routing.WalkRoute("wheelchair", 300, 300, [], [], [])  # 5 min

    def option(dep, arr):
        leg = transit.Leg(
            "suburban", "S6", "Darmstadt Hbf", "Frankfurt (Main) Hauptwache", dep, arr, None, None, 35
        )
        return plan.Option(transit.Journey([leg], "test"), [], walk_start=start, walk_end=end)

    both = option(departs, arrives)
    assert both.leave_at == departs - timedelta(minutes=10 + plan.BOARDING_BUFFER_MIN)
    assert both.arrive_at == arrives + timedelta(minutes=5)
    no_dep = option(None, arrives)
    assert no_dep.leave_at is None
    assert no_dep.arrive_at == arrives + timedelta(minutes=5)
    no_arr = option(departs, None)
    assert no_arr.arrive_at is None
    assert no_arr.leave_at == departs - timedelta(minutes=10 + plan.BOARDING_BUFFER_MIN)


def test_option_html_shows_leave_time_and_later():
    option = {
        "usable": True,
        "fully_verified": True,
        "minutes": 97,
        "timetable": "test",
        "rides": [
            {
                "line": "S6",
                "from": "Darmstadt Hbf",
                "to": "Frankfurt (Main) Hauptwache",
                "minutes": 35,
                "departure": "2026-11-03T12:35:00+01:00",
            }
        ],
        "stations": [],
        "walk_to_station": None,
        "walk_from_station": None,
        "transfer_walks": [],
        "problems": [],
        "avoided_on_shortest_path": [],
        "leave_at": "12:02",
        "arrive_at": "13:39",
        "later_departures": ["13:05"],
    }
    page = mapview._option_html(option, True)
    assert "Leave at 12:02" in page
    assert "arrive about 13:39" in page
    assert "Same trip later: 13:05" in page


def test_earliest_uses_entrance_doors_not_pin():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0).astimezone()
    net = network()
    graph = plan.routing.build_graph(net, "wheelchair")
    darmstadt = transit.find_stop("Darmstadt Hbf")
    pin = plan.best_walk(net, graph, ORIGIN, [((darmstadt.lon, darmstadt.lat), False, "")], True)[0]
    doors = [
        plan.best_walk(net, graph, ORIGIN, [((e.lon, e.lat), True, e.name)], True)[0]
        for e in darmstadt.entrances
    ]
    gap = min(plan.walk_minutes(d) for d in doors) - plan.walk_minutes(pin)
    assert gap > 0  # the pin is closer than any door in this fixture
    early = now + timedelta(minutes=plan.walk_minutes(pin) + plan.BOARDING_BUFFER_MIN + gap / 2)
    late = now + timedelta(minutes=plan.walk_minutes(pin) + plan.BOARDING_BUFFER_MIN + gap + 5)

    class Since:
        """A provider offering fixed departures at or after the requested time, like the live ones."""

        def journeys(self, origin, destination, results=3, departure=None):
            if (origin.name, destination.name) != ("Darmstadt Hbf", "Frankfurt (Main) Hauptwache"):
                return []
            found = []
            for departs in (early, late):
                if departure is not None and departs < departure:
                    continue
                leg = transit.Leg(
                    "suburban",
                    "S3",
                    origin.name,
                    destination.name,
                    departs,
                    departs + timedelta(minutes=37),
                    None,
                    None,
                    37,
                )
                found.append(transit.Journey([leg], "test"))
            return found[:results]

    options = plan.plan(ORIGIN, ZEIL, "wheelchair", net, Since(), lookup_with(), now)
    assert len(options) == 1
    assert options[0].journey.rides[0].departure == late
    assert not any("train leaves before" in p for p in options[0].problems)


def test_place_names_and_errors():
    boxes = [AREAS["darmstadt-centre"], AREAS["frankfurt-centre"]]
    for label, lon, lat in plan.PLACES.values():
        assert any(w <= lon <= e and s <= lat <= n for w, s, e, n in boxes), label
    assert plan.place("49.87,8.65") == ("49.87,8.65", 8.65, 49.87)
    with pytest.raises(SystemExit, match="luisenplatz"):
        plan.place("nowhere")


def test_new_areas_contain_old_ones():
    for old, new in (("darmstadt", "darmstadt-centre"), ("frankfurt", "frankfurt-centre")):
        west, south, east, north = AREAS[old]
        big_west, big_south, big_east, big_north = AREAS[new]
        assert big_west <= west and big_south <= south and big_east >= east and big_north >= north


def test_places_reach_a_station():
    for label, lon, lat in plan.PLACES.values():
        assert any(
            plan.routing.haversine_m((lon, lat), (s.lon, s.lat)) <= plan.MAX_STATION_WALK_M
            for s in transit.STOPS
        ), label
    west, south, east, north = AREAS["frankfurt-centre"]
    for label, lon, lat in plan.PLACES.values():
        if west <= lon <= east and south <= lat <= north:
            assert any(
                plan.routing.haversine_m((lon, lat), (s.lon, s.lat)) <= plan.MAX_DESTINATION_WALK_M
                for s in transit.STOPS
            ), label


def test_check_on_map():
    net = network()
    plan.check_on_map(net, "origin", ORIGIN)  # on a mapped node
    with pytest.raises(SystemExit) as exc:
        plan.check_on_map(net, "far", (ORIGIN[0], ORIGIN[1] - 0.009))  # ~1 km away
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        plan.check_on_map(Network({}, {}), "empty", ORIGIN)
    assert exc.value.code == 2
    plan.check_on_map(net, "near", (ORIGIN[0], ORIGIN[1] - 0.00225))  # ~250 m: inside
    with pytest.raises(SystemExit) as exc:
        plan.check_on_map(net, "over", (ORIGIN[0], ORIGIN[1] - 0.00315))  # ~350 m: outside
    assert exc.value.code == 2


def test_check_on_map_suggests_containing_area(caplog):
    point = (8.6668, 49.8770)  # inside darmstadt-centre, far from the test network
    with caplog.at_level(logging.INFO, logger="barrierfrei.plan"), pytest.raises(SystemExit):
        plan.check_on_map(network(), "Mathildenhöhe", point)
    assert "--area darmstadt-centre" in caplog.text


def test_frankfurt_hbf_main_hall_tracks_need_no_lift():
    def lookup(number):
        raise AssertionError("FaSta must not be called for level tracks")

    check = plan.check_station("Frankfurt (Main) Hbf", "12", lookup)
    assert check.status == "ok"
    assert "level access" in check.reason
    assert check.use == []
    deep = plan.check_station("Frankfurt (Main) Hbf", "101", lookup_with())
    assert [e.equipment for e in deep.use] == [10561326, 10955285]


def test_missing_lift_data_is_unverified_not_broken():
    data = {
        "stationnumber": 1126,
        "facilities": [
            {
                "equipmentnumber": 10543460,
                "type": "ELEVATOR",
                "state": "ACTIVE",
                "description": "zu Gleis 5",
                "geocoordX": DARMSTADT_LIFT[0],
                "geocoordY": DARMSTADT_LIFT[1],
            }
        ],
    }

    def lookup(number, outages=frozenset()):
        assert number == 1126
        return stations.with_outages(stations.parse_station(data, 0), set(outages))

    check = plan.check_station("Darmstadt Hbf", "7", lookup)
    assert check.status == "unverified"
    assert "no elevator listed for track 7" in check.reason
    broken = plan.check_station("Darmstadt Hbf", "5", lambda n: lookup(n, {10543460}))
    assert broken.status == "broken"


def test_regional_tickets_skip_long_distance_trains():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0).astimezone()

    class Both:
        def journeys(self, origin, destination, results=3, departure=None):
            if (origin.name, destination.name) != ("Darmstadt Hbf", "Frankfurt (Main) Hauptwache"):
                return []
            departs = now + timedelta(minutes=30)
            ice = transit.Leg(
                "national",
                "ICE 1092",
                origin.name,
                destination.name,
                departs,
                departs + timedelta(minutes=20),
                None,
                None,
                20,
            )
            sbahn = transit.Leg(
                "suburban",
                "S6",
                origin.name,
                destination.name,
                departs,
                departs + timedelta(minutes=37),
                None,
                None,
                37,
            )
            return [transit.Journey([ice], "test"), transit.Journey([sbahn], "test")]

    regional = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), Both(), lookup_with(), now)
    assert len(regional) == 1
    assert regional[0].journey.rides[0].line == "S6"
    both = plan.plan(ORIGIN, ZEIL, "wheelchair", network(), Both(), lookup_with(), now, tickets="any")
    assert len(both) == 2
    result = plan.summary(both, "wheelchair", tickets="any")
    assert result["tickets"] == "any"
    ice_summary = next(o for o in result["options"] if o["rides"][0]["line"] == "ICE 1092")
    assert ice_summary["long_distance"] is True
    with pytest.raises(ValueError):
        plan.plan(ORIGIN, ZEIL, "wheelchair", network(), Both(), lookup_with(), now, tickets="foo")


def test_option_html_marks_long_distance():
    def option(**overrides):
        base = {
            "usable": True,
            "fully_verified": True,
            "minutes": 97,
            "timetable": "test",
            "rides": [
                {
                    "line": "ICE 1092",
                    "from": "Darmstadt Hbf",
                    "to": "Frankfurt (Main) Hbf",
                    "minutes": 20,
                    "departure": "2026-11-03T12:35:00+01:00",
                }
            ],
            "stations": [],
            "walk_to_station": None,
            "walk_from_station": None,
            "transfer_walks": [],
            "problems": [],
            "avoided_on_shortest_path": [],
            "leave_at": "12:02",
            "arrive_at": "13:39",
            "later_departures": [],
        }
        return base | overrides

    assert "Needs a long-distance ticket" in mapview._option_html(option(long_distance=True), True)
    assert "Needs a long-distance ticket" not in mapview._option_html(option(), True)


def _berlin(h, m):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(2026, 11, 3, h, m, tzinfo=ZoneInfo("Europe/Berlin"))


def _ranked_option(
    line,
    dest,
    dep,
    arr,
    ride_min,
    start_m,
    end_m,
    statuses,
    end_cost=None,
    board_via="",
    alight_via="",
):
    mode = "suburban" if line.startswith("S") else "regional"
    leg = transit.Leg(mode, line, "Darmstadt Hbf", dest, dep, arr, None, None, ride_min)
    walk_start = plan.routing.WalkRoute("wheelchair", start_m, start_m, [], [], [])
    walk_end = plan.routing.WalkRoute(
        "wheelchair", end_m, end_cost if end_cost is not None else end_m, [], [], []
    )
    checks = [
        plan.StationCheck("Darmstadt Hbf", None, statuses[0]),
        plan.StationCheck(dest, None, statuses[1]),
    ]
    return plan.Option(
        transit.Journey([leg], "test"),
        checks,
        walk_start=walk_start,
        walk_end=walk_end,
        board_via=board_via,
        alight_via=alight_via,
    )


def test_rank_prefers_earlier_arrival_among_verified():
    s6 = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(17, 35),
        _berlin(18, 12),
        37,
        2199,
        600,
        ["ok", "ok"],
    )
    re60 = _ranked_option(
        "RE60",
        "Frankfurt (Main) Hbf",
        _berlin(18, 7),
        _berlin(18, 25),
        18,
        2222,
        1560,
        ["ok", "ok"],
    )
    assert s6.arrive_at == _berlin(18, 22)
    assert re60.arrive_at == _berlin(18, 51)
    assert re60.score < s6.score
    assert plan.rank([re60, s6]) == [s6, re60]


def test_rank_prefers_earlier_arrival_regional_pair():
    re60 = _ranked_option(
        "RE60",
        "Frankfurt (Main) Hbf",
        _berlin(18, 7),
        _berlin(18, 25),
        18,
        2767,
        1081,
        ["ok", "ok"],
    )
    rb68 = _ranked_option(
        "RB68",
        "Frankfurt (Main) Hbf",
        _berlin(18, 30),
        _berlin(18, 47),
        17,
        2767,
        1081,
        ["ok", "ok"],
    )
    assert rb68.score < re60.score
    assert re60.arrive_at < rb68.arrive_at
    assert plan.rank([rb68, re60]) == [re60, rb68]


def test_rank_keeps_verified_before_earlier_unverified():
    verified = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(17, 35),
        _berlin(18, 12),
        37,
        2199,
        600,
        ["ok", "ok"],
    )
    unverified = _ranked_option(
        "S6",
        "Frankfurt (Main) Hauptwache",
        _berlin(17, 34),
        _berlin(18, 11),
        37,
        2199,
        600,
        ["ok", "unverified"],
    )
    assert unverified.arrive_at < verified.arrive_at
    assert plan.rank([unverified, verified]) == [verified, unverified]


def test_rank_penalties_shift_arrival():
    def unverified_option(dep, arr, statuses):
        leg = transit.Leg(
            "suburban", "S6", "Darmstadt Hbf", "Frankfurt (Main) Hauptwache", dep, arr, None, None, 37
        )
        checks = [
            plan.StationCheck("Darmstadt Hbf", None, statuses[0]),
            plan.StationCheck("Frankfurt (Main) Hauptwache", None, statuses[1]),
        ]
        start = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        end = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        return plan.Option(transit.Journey([leg], "test"), checks, walk_start=start, walk_end=end)

    one = unverified_option(_berlin(17, 48), _berlin(18, 25), ["ok", "unverified"])
    two = unverified_option(_berlin(17, 43), _berlin(18, 20), ["unverified", "unverified"])
    assert one.arrive_at == _berlin(18, 25)
    assert two.arrive_at == _berlin(18, 20)
    assert plan.rank([two, one]) == [one, two]

    rough = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(17, 35),
        _berlin(18, 12),
        37,
        2199,
        600,
        ["ok", "ok"],
        end_cost=600 + 900,
    )
    smooth = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(17, 43),
        _berlin(18, 20),
        37,
        2199,
        600,
        ["ok", "ok"],
    )
    assert rough.arrive_at == _berlin(18, 22)
    assert smooth.arrive_at == _berlin(18, 30)
    assert plan.rank([rough, smooth]) == [smooth, rough]


def test_rank_without_times_uses_score():
    def untimed(ride_min):
        leg = transit.Leg("suburban", "S3", "a", "b", None, None, None, None, ride_min)
        start = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        end = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        checks = [plan.StationCheck("a", None, "ok"), plan.StationCheck("b", None, "ok")]
        return plan.Option(transit.Journey([leg], "test"), checks, walk_start=start, walk_end=end)

    fast, slow = untimed(20), untimed(40)
    assert plan.rank([slow, fast]) == [fast, slow]

    leg = transit.Leg("suburban", "S6", "a", "b", _berlin(17, 35), _berlin(18, 15), None, None, 40)
    start = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
    end = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
    checks = [plan.StationCheck("a", None, "ok"), plan.StationCheck("b", None, "ok")]
    timed = plan.Option(transit.Journey([leg], "test"), checks, walk_start=start, walk_end=end)
    assert fast.score < timed.score
    assert plan.rank([fast, timed]) == [timed, fast]


def test_rank_unusable_last():
    def option(dep, arr, status):
        leg = transit.Leg("suburban", "S6", "a", "b", dep, arr, None, None, 37)
        start = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        end = plan.routing.WalkRoute("wheelchair", 0, 0, [], [], [])
        checks = [plan.StationCheck("a", None, "ok"), plan.StationCheck("b", None, status)]
        return plan.Option(transit.Journey([leg], "test"), checks, walk_start=start, walk_end=end)

    usable = option(_berlin(18, 23), _berlin(19, 0), "ok")
    broken = option(_berlin(17, 23), _berlin(18, 0), "broken")
    assert plan.rank([broken, usable]) == [usable, broken]


def test_collapse_order_matches_rank():
    early = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(17, 35),
        _berlin(18, 12),
        37,
        2199,
        600,
        ["ok", "ok"],
        board_via="door",
        alight_via="door",
    )
    late = _ranked_option(
        "S6",
        "Frankfurt (Main) Taunusanlage",
        _berlin(18, 5),
        _berlin(18, 42),
        37,
        2199,
        600,
        ["ok", "ok"],
        board_via="door",
        alight_via="door",
    )
    re60 = _ranked_option(
        "RE60",
        "Frankfurt (Main) Hbf",
        _berlin(18, 7),
        _berlin(18, 25),
        18,
        2222,
        1560,
        ["ok", "ok"],
    )
    result = plan.collapse(plan.rank([re60, late, early]))
    assert len(result) == 2
    assert result[0].journey.rides[0].departure == _berlin(17, 35)
    assert result[0].later == [_berlin(18, 5)]
    assert plan.rank(result) == result


def test_why_best_compares_arrivals():
    first = {"usable": True, "fully_verified": True, "minutes": 89, "arrive_at": "18:22"}
    slower = {"usable": True, "fully_verified": True, "minutes": 86, "arrive_at": "18:51"}
    assert mapview._why_best(first, [first, slower]) == ""

    late = {"usable": True, "fully_verified": True, "minutes": 89, "arrive_at": "18:30"}
    early = {"usable": True, "fully_verified": False, "minutes": 86, "arrive_at": "18:21"}
    note = mapview._why_best(late, [late, early])
    assert "arrives 9 min later than the earliest option" in note

    no_time = {"usable": True, "fully_verified": True, "minutes": 70, "arrive_at": None}
    faster = {"usable": True, "fully_verified": False, "minutes": 60, "arrive_at": None}
    assert "min slower than the fastest option" in mapview._why_best(no_time, [no_time, faster])


def test_run_trip_returns_result_and_map():
    origin, dest = f"{ORIGIN[1]},{ORIGIN[0]}", f"{ZEIL[1]},{ZEIL[0]}"
    result, page, options = plan.run_trip(
        origin, dest, "wheelchair", network(), transit.Static(), lookup_with()
    )
    assert result["from"] == origin and result["to"] == dest
    assert options and result["options"][0]["usable"]
    assert "leaflet" in page and "tile.openstreetmap.org" in page
