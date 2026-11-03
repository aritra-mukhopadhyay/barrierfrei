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


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _TimetablesFake:
    """A Timetables API backed by inline XML: station lookup, hour boards and fchg."""

    def __init__(self, origin_board, dest_board, fchg_origin=None, fchg_dest=None):
        self.origin_board, self.dest_board = origin_board, dest_board
        self.fchg_origin = fchg_origin or '<timetable station="Darmstadt Hbf"/>'
        self.fchg_dest = fchg_dest or '<timetable station="Frankfurt(M)Taunusanlage"/>'
        self.station_calls = 0

    def get(self, url, headers=None, timeout=None):
        if "/station/" in url:
            self.station_calls += 1
            return _Resp('<stations><station name="Frankfurt(M)Taunusanlage" eva="8002048"/></stations>')
        if "/fchg/8000068" in url:
            return _Resp(self.fchg_origin)
        if "/fchg/" in url:
            return _Resp(self.fchg_dest)
        if "/plan/8000068/" in url:
            return _Resp(self.origin_board if url.endswith("/12") else '<timetable station="x"/>')
        if "/plan/" in url:
            board = self.dest_board if url.endswith("/12") else None
            return _Resp(board or '<timetable station="Frankfurt(M)Taunusanlage"/>')
        raise AssertionError(f"unexpected url {url}")


def _taunusanlage_trip():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    darmstadt = transit.find_stop("Darmstadt Hbf")
    taunusanlage = transit.find_stop("Frankfurt (Main) Taunusanlage")
    departure = datetime(2026, 11, 3, 12, 0, tzinfo=berlin)
    origin_board = (
        '<timetable station="Darmstadt Hbf">'
        '<s id="123-2611031200-1"><tl c="S" n="35321"/>'
        '<dp pt="2611031200" pp="2" l="3" '
        'ppth="Darmstadt-Arheilgen|Frankfurt(M)Taunusanlage|Frankfurt(M)Hauptwache"/>'
        "</s></timetable>"
    )
    dest_board = (
        '<timetable station="Frankfurt(M)Taunusanlage">'
        '<s id="123-2611031200-1"><tl c="S" n="35321"/>'
        '<ar pt="2611031230" pp="1" l="3" ppth="Darmstadt Hbf|Darmstadt-Arheilgen"/>'
        "</s></timetable>"
    )
    return darmstadt, taunusanlage, departure, origin_board, dest_board


def test_timetables_direct_train():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    darmstadt, taunusanlage, departure, origin_board, dest_board = _taunusanlage_trip()
    fchg_origin = (
        '<timetable station="Darmstadt Hbf">'
        '<s id="123-2611031200-1"><dp ct="2611031203" cp="5"/></s></timetable>'
    )
    provider = transit.DbTimetables(_TimetablesFake(origin_board, dest_board, fchg_origin), "id", "key")
    (journey,) = provider.journeys(darmstadt, taunusanlage, departure=departure)
    (leg,) = journey.legs
    assert leg.departure == datetime(2026, 11, 3, 12, 3, tzinfo=berlin)
    assert leg.arrival == datetime(2026, 11, 3, 12, 30, tzinfo=berlin)
    assert (leg.origin_platform, leg.destination_platform) == ("5", "1")
    assert (leg.line, leg.mode, leg.minutes) == ("S3", "suburban", 27)
    assert (leg.origin, leg.destination) == ("Darmstadt Hbf", "Frankfurt (Main) Taunusanlage")
    assert leg.departure.tzinfo is not None and leg.arrival.tzinfo is not None
    assert journey.source == "DB Timetables (live)"
    assert leg.coords == ((darmstadt.lon, darmstadt.lat), (taunusanlage.lon, taunusanlage.lat))


def test_timetables_skips_trains_that_do_not_stop_there():
    darmstadt, taunusanlage, departure, _, dest_board = _taunusanlage_trip()
    origin_board = (
        '<timetable station="Darmstadt Hbf">'
        '<s id="123-2611031200-1"><tl c="S" n="35321"/>'
        '<dp pt="2611031200" pp="2" l="3" ppth="Darmstadt-Arheilgen|Frankfurt(M)Hauptwache"/>'
        "</s></timetable>"
    )
    provider = transit.DbTimetables(_TimetablesFake(origin_board, dest_board), "id", "key")
    assert provider.journeys(darmstadt, taunusanlage, departure=departure) == []


def test_timetables_skips_cancelled_and_too_early():
    darmstadt, taunusanlage, departure, _, _ = _taunusanlage_trip()
    origin_board = (
        '<timetable station="Darmstadt Hbf">'
        '<s id="1-2611031200-1"><tl c="S" n="1"/>'
        '<dp pt="2611031200" pp="2" l="3" ppth="Frankfurt(M)Taunusanlage" cs="c"/></s>'
        '<s id="2-2611031150-1"><tl c="S" n="2"/>'
        '<dp pt="2611031150" pp="2" l="3" ppth="Frankfurt(M)Taunusanlage"/></s>'
        "</timetable>"
    )
    dest_board = (
        '<timetable station="Frankfurt(M)Taunusanlage">'
        '<s id="1-2611031200-1"><tl c="S" n="1"/><ar pt="2611031230" pp="1"/></s>'
        '<s id="2-2611031150-1"><tl c="S" n="2"/><ar pt="2611031220" pp="1"/></s>'
        "</timetable>"
    )
    provider = transit.DbTimetables(_TimetablesFake(origin_board, dest_board), "id", "key")
    assert provider.journeys(darmstadt, taunusanlage, departure=departure) == []


def test_timetables_resolves_eva_by_name_once():
    darmstadt, taunusanlage, departure, origin_board, dest_board = _taunusanlage_trip()
    fake = _TimetablesFake(origin_board, dest_board)
    provider = transit.DbTimetables(fake, "id", "key")
    provider.journeys(darmstadt, taunusanlage, departure=departure)
    provider.journeys(darmstadt, taunusanlage, departure=departure)
    assert fake.station_calls == 1


def test_auto_tries_next_live_provider():
    import requests

    class Fail:
        def __init__(self):
            self.calls = 0

        def journeys(self, origin, destination, results=3, departure=None):
            self.calls += 1
            raise requests.ConnectionError("down")

    class Ok:
        def __init__(self, journey):
            self.calls, self.journey = 0, journey

        def journeys(self, origin, destination, results=3, departure=None):
            self.calls += 1
            return [self.journey]

    journey = transit.Journey([transit.Leg("suburban", "S3", "a", "b", None, None, None, None, 1)], "second")
    fail, ok = Fail(), Ok(journey)
    auto = transit.Auto(live=[fail, ok])
    darmstadt, hauptwache = (
        transit.find_stop("Darmstadt Hbf"),
        transit.find_stop("Frankfurt (Main) Hauptwache"),
    )
    assert auto.journeys(darmstadt, hauptwache) == [journey]
    assert auto.journeys(darmstadt, hauptwache) == [journey]
    assert fail.calls == 1
    assert ok.calls == 2


def test_timetables_uses_changed_arrival():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    darmstadt, taunusanlage, departure, origin_board, dest_board = _taunusanlage_trip()
    fchg_dest = (
        '<timetable station="Frankfurt(M)Taunusanlage">'
        '<s id="123-2611031200-1"><ar ct="2611031234" cp="3"/></s></timetable>'
    )
    fake = _TimetablesFake(origin_board, dest_board, fchg_dest=fchg_dest)
    provider = transit.DbTimetables(fake, "id", "key")
    (journey,) = provider.journeys(darmstadt, taunusanlage, departure=departure)
    (leg,) = journey.legs
    assert leg.arrival == datetime(2026, 11, 3, 12, 34, tzinfo=berlin)
    assert leg.destination_platform == "3"


def test_timetables_matches_by_train_number():
    darmstadt, taunusanlage, departure, origin_board, _ = _taunusanlage_trip()
    dest_board = (
        '<timetable station="Frankfurt(M)Taunusanlage">'
        '<s id="999-2611031230-2"><tl c="S" n="35321"/>'
        '<ar pt="2611031230" pp="1"/></s></timetable>'
    )
    provider = transit.DbTimetables(_TimetablesFake(origin_board, dest_board), "id", "key")
    (journey,) = provider.journeys(darmstadt, taunusanlage, departure=departure)
    assert journey.legs[0].line == "S3"


def test_timetables_missing_hour_is_empty():
    import requests

    darmstadt, taunusanlage, departure, origin_board, dest_board = _taunusanlage_trip()

    class Missing:
        def raise_for_status(self):
            err = requests.HTTPError("404")
            err.response = type("Resp", (), {"status_code": 404})()
            raise err

    class MissingHour(_TimetablesFake):
        def get(self, url, headers=None, timeout=None):
            if "/plan/" in url and not url.endswith("/12"):
                return Missing()
            return super().get(url, headers, timeout)

    provider = transit.DbTimetables(MissingHour(origin_board, dest_board), "id", "key")
    (journey,) = provider.journeys(darmstadt, taunusanlage, departure=departure)
    assert journey.legs[0].line == "S3"
