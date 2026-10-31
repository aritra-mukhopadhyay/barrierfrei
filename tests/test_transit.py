from barrierfrei import transit


def test_names_from_different_sources_match():
    assert transit.find_stop("Frankfurt(Main)Hauptwache").fasta == 1864
    assert transit.find_stop("Frankfurt (Main) Hauptbahnhof").fasta == 1866
    assert transit.find_stop("Frankfurt(Main)Hbf (tief)").fasta == 1866
    assert transit.find_stop("Somewhere else") is None


def test_static_journeys_include_transfer():
    darmstadt, hauptwache = (
        transit.find_stop("Darmstadt Hbf"),
        transit.find_stop("Frankfurt (Main) Hauptwache"),
    )
    direct, via_hbf = transit.Static().journeys(darmstadt, hauptwache)
    assert len(direct.rides) == 1
    assert [r.destination for r in via_hbf.rides] == ["Frankfurt (Main) Hbf", "Frankfurt (Main) Hauptwache"]
    assert via_hbf.rides[1].origin_platform == "101"
    assert via_hbf.minutes == 18 + transit.TRANSFER_MINUTES + 4


def test_parse_db_rest_journey():
    data = {
        "journeys": [
            {
                "legs": [
                    {
                        "origin": {
                            "name": "Darmstadt Hbf",
                            "location": {"longitude": 8.63, "latitude": 49.87},
                        },
                        "destination": {
                            "name": "Frankfurt(Main)Hbf",
                            "location": {"longitude": 8.66, "latitude": 50.1},
                        },
                        "departure": "2026-10-31T12:04:00+01:00",
                        "arrival": "2026-10-31T12:21:00+01:00",
                        "departurePlatform": "1",
                        "arrivalPlatform": "18",
                        "line": {"name": "RE 60", "product": "regional"},
                    },
                    {"walking": True, "origin": {"name": "a"}, "destination": {"name": "b"}},
                ]
            }
        ]
    }
    (journey,) = transit.parse_db_rest(data)
    ride = journey.rides[0]
    assert (ride.line, ride.minutes, ride.origin_platform, ride.destination_platform) == (
        "RE 60",
        17,
        "1",
        "18",
    )
    assert journey.legs[1].mode == "walk"


def test_auto_falls_back_when_live_api_fails():
    import requests

    class Down:
        def get(self, *args, **kwargs):
            raise requests.HTTPError("503")

    auto = transit.Auto(Down())
    darmstadt, hauptwache = (
        transit.find_stop("Darmstadt Hbf"),
        transit.find_stop("Frankfurt (Main) Hauptwache"),
    )
    assert auto.journeys(darmstadt, hauptwache)[0].source.startswith("typical")


def test_live_journey_minutes_include_waiting():
    from datetime import datetime

    def leg(dep, arr):
        t = lambda m: datetime(2026, 10, 31, 12, m)  # noqa: E731
        return transit.Leg("suburban", "S1", "a", "b", t(dep), t(arr), None, None, arr - dep)

    journey = transit.Journey([leg(0, 10), leg(30, 40)], "test")
    assert journey.minutes == 40


def test_auto_stops_asking_a_dead_api():
    import requests

    calls = []

    class Down:
        def get(self, *args, **kwargs):
            calls.append(1)
            raise requests.ConnectionError("down")

    auto = transit.Auto(Down())
    darmstadt = transit.find_stop("Darmstadt Hbf")
    for name in ("Frankfurt (Main) Hauptwache", "Frankfurt (Main) Konstablerwache"):
        assert auto.journeys(darmstadt, transit.find_stop(name))
    assert len(calls) == 1  # the circuit breaker kept the second lookup instant
