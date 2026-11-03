import json
from datetime import datetime
from zoneinfo import ZoneInfo

from barrierfrei import site, stations, transit
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


def _latlon(point):
    return f"{point[1]},{point[0]}"


def test_build_writes_pages_and_index(tmp_path):
    origin, dest = _latlon(ORIGIN), _latlon(ZEIL)
    trips = [
        site.DemoTrip(slug="a-trip", title="A trip", origin=origin, destination=dest),
        site.DemoTrip(slug="b-trip", title="B trip", origin=origin, destination=dest),
    ]
    entries = site.build(
        tmp_path,
        trips,
        network=network(),
        provider=transit.Static(),
        make_lookup_for=lambda outages: lookup_with(outages),
    )
    assert len(entries) == 2
    assert all("error" not in e for e in entries)
    for trip in trips:
        assert (tmp_path / f"{trip.slug}.html").exists()
        data = json.loads((tmp_path / f"{trip.slug}.json").read_text(encoding="utf-8"))
        assert data["from"] == origin and data["to"] == dest
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="a-trip.html"' in index and 'href="b-trip.html"' in index
    first = entries[0]
    assert {"usable", "fully_verified", "minutes", "leave_at", "arrive_at", "line"} <= first.keys()


def test_build_survives_a_failing_trip(tmp_path):
    origin, dest = _latlon(ORIGIN), _latlon(ZEIL)
    trips = [
        site.DemoTrip(slug="good", title="Good", origin=origin, destination=dest),
        site.DemoTrip(slug="bad", title="Bad", origin="0,0", destination=dest),
    ]
    entries = site.build(
        tmp_path,
        trips,
        network=network(),
        provider=transit.Static(),
        make_lookup_for=lambda outages: lookup_with(outages),
    )
    by_slug = {e["slug"]: e for e in entries}
    assert "error" in by_slug["bad"] and by_slug["bad"]["error"]
    assert "error" not in by_slug["good"]
    assert (tmp_path / "good.html").exists() and (tmp_path / "good.json").exists()
    assert not (tmp_path / "bad.html").exists()
    assert (tmp_path / "index.html").exists()


def test_index_escapes_and_has_no_secrets(tmp_path):
    entries = [
        {
            "slug": "x",
            "title": "<script>alert(1)</script>",
            "note": "",
            "profile": "wheelchair",
            "usable": True,
            "fully_verified": True,
            "minutes": 60,
            "leave_at": "12:00",
            "arrive_at": "13:00",
            "line": "S6",
        }
    ]
    page = site.index_html(entries, datetime(2026, 11, 3, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")))
    assert "<script>" not in page
    assert "&lt;script&gt;" in page

    fake_key = "sk-fake-12345-do-not-leak"

    def broken_lookup(number):
        raise RuntimeError(f"FaSta boom {fake_key}")

    origin, dest = _latlon(ORIGIN), _latlon(ZEIL)
    entries = site.build(
        tmp_path,
        [site.DemoTrip(slug="a", title="A", origin=origin, destination=dest)],
        network=network(),
        provider=transit.Static(),
        make_lookup_for=lambda outages: broken_lookup,
    )
    assert entries[0]["error"] == "Could not compute this trip right now."
    for path in tmp_path.glob("*.html"):
        assert fake_key not in path.read_text(encoding="utf-8")
    for path in tmp_path.glob("*.json"):
        assert fake_key not in path.read_text(encoding="utf-8")
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Could not compute this trip right now." in index


def test_broken_lift_trip_passes_outages(tmp_path):
    calls: list[tuple] = []

    def make_lookup_for(outages):
        calls.append(tuple(outages))
        return lookup_with(outages)

    site.build(
        tmp_path,
        site.DEMO_TRIPS,
        network=network(),
        provider=transit.Static(),
        make_lookup_for=make_lookup_for,
    )
    assert calls == [tuple(t.out_of_service) for t in site.DEMO_TRIPS]
    broken = next(t for t in site.DEMO_TRIPS if t.slug == "luisenplatz-zeil-broken-lift")
    assert broken.out_of_service == (10472990,)
    assert calls.count((10472990,)) == 1
    assert calls.count(()) == len(site.DEMO_TRIPS) - 1
