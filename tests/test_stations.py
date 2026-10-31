from barrierfrei import stations

# Real FaSta answers (trimmed) fetched on 2026-10-31.
FRANKFURT_HBF = {
    "stationnumber": 1866,
    "name": "Frankfurt (Main) Hbf",
    "facilities": [
        {
            "equipmentnumber": 10020388,
            "type": "ELEVATOR",
            "state": "ACTIVE",
            "description": "*HA282 Lastenaufzug*",
        },
        {
            "equipmentnumber": 10020626,
            "type": "ELEVATOR",
            "state": "ACTIVE",
            "description": "Eingangshalle<->DB Lounge 1.Kl",
        },
        {
            "equipmentnumber": 10561326,
            "type": "ELEVATOR",
            "state": "ACTIVE",
            "description": "Querbahnsteig <-> Gl.101/102",
            "geocoordX": 8.6628995,
            "geocoordY": 50.1072933,
        },
        {
            "equipmentnumber": 10561327,
            "type": "ELEVATOR",
            "state": "ACTIVE",
            "description": "Querbahnsteig <-> Gl.103/104",
        },
        {
            "equipmentnumber": 10955285,
            "type": "ELEVATOR",
            "state": "ACTIVE",
            "description": "Bahnhofsvorplatz <-> B-Ebene",
        },
        {
            "equipmentnumber": 10313106,
            "type": "ESCALATOR",
            "state": "INACTIVE",
            "description": "zu Gleis 101/102 (S-Bahn)",
        },
    ],
}
DARMSTADT = {
    "stationnumber": 1126,
    "name": "Darmstadt Hbf",
    "facilities": [
        {"equipmentnumber": 10543453, "type": "ELEVATOR", "state": "ACTIVE", "description": "zu Gleis 3/4"},
        {"equipmentnumber": 10543458, "type": "ELEVATOR", "state": "ACTIVE", "description": "zu Gleis 1"},
    ],
}


def test_parse_keeps_only_passenger_elevators():
    station = stations.parse_station(FRANKFURT_HBF, 0)
    assert [e.equipment for e in station.elevators] == [10561326, 10561327, 10955285]
    assert station.elevators[0].tracks == {"101", "102"}
    assert station.elevators[0].lon == 8.6628995


def test_tracks_from_descriptions():
    station = stations.parse_station(DARMSTADT, 0)
    assert [e.tracks for e in station.elevators] == [{"3", "4"}, {"1"}]


def test_access_needs_platform_and_street_elevator():
    station = stations.parse_station(FRANKFURT_HBF, 0)
    result = stations.access(station, "101")
    assert result.ok and [e.equipment for e in result.use] == [10561326, 10955285]


def test_transfer_needs_only_platform_elevator():
    station = stations.with_outages(stations.parse_station(FRANKFURT_HBF, 0), {10955285})
    assert not stations.access(station, "101").ok
    assert stations.access(station, "101", transfer=True).ok


def test_broken_platform_elevator_blocks_that_track_only():
    station = stations.with_outages(stations.parse_station(FRANKFURT_HBF, 0), {10561326})
    blocked = stations.access(station, "102")
    assert not blocked.ok and "track 102" in blocked.reason
    assert [e.equipment for e in blocked.broken] == [10561326]
    assert stations.access(station, "103").ok


def test_unknown_track_accepts_any_working_elevator():
    station = stations.with_outages(stations.parse_station(DARMSTADT, 0), {10543453})
    result = stations.access(station)
    assert result.ok and result.use[0].equipment == 10543458
    everything_broken = stations.with_outages(station, {10543458})
    assert not stations.access(everything_broken).ok


def test_unknown_state_counts_as_not_working():
    data = {
        "stationnumber": 1,
        "facilities": [{"equipmentnumber": 1, "type": "ELEVATOR", "state": "UNKNOWN"}],
    }
    assert not stations.access(stations.parse_station(data, 0)).ok


def test_fetch_uses_fresh_cache_and_falls_back_to_stale(tmp_path):
    import json
    import os

    import requests

    class Down:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    (tmp_path / "1126.json").write_text(json.dumps(DARMSTADT))
    assert not stations.fetch_station(Down(), "id", "key", 1126, tmp_path).stale  # fresh cache, no call
    old = 1_000_000_000
    os.utime(tmp_path / "1126.json", (old, old))
    station = stations.fetch_station(Down(), "id", "key", 1126, tmp_path)
    assert station.stale and len(station.elevators) == 2


def test_unknown_platform_still_needs_the_street_lift():
    station = stations.with_outages(stations.parse_station(FRANKFURT_HBF, 0), {10955285})
    assert not stations.access(station).ok
    assert stations.access(station, transfer=True).ok
