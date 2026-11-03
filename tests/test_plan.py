from barrierfrei import mapview, plan, stations, transit
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
    page = mapview.render(result, options, ORIGIN, ZEIL)
    assert (
        "Best we can offer" in page and "leaflet" in page and "10351032" in page
    )  # test stations are all unverifiable
    # CARTO's basemaps now require an API key; OSM's own tiles load (checked in Chrome and Edge).
    assert "tile.openstreetmap.org" in page and "cartocdn.com" not in page


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
    assert not too_soon[0].usable and too_soon[0].problems
    later = plan.plan(
        ORIGIN, ZEIL, "wheelchair", network(), Timed(now + timedelta(minutes=30)), lookup_with(), now
    )
    assert later[0].usable


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
