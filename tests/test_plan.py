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
            }
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
    assert "Recommended" in page and "leaflet" in page and "10351032" in page


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
    assert check.status == "ok" and check.elevator.lon is None
    assert plan.entrance(check) == (
        transit.find_stop("Frankfurt (Main) Hbf").lon,
        transit.find_stop("Frankfurt (Main) Hbf").lat,
    )
