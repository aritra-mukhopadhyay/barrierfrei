"""Public transport between the two walking areas.

Two providers behind one interface:
- DbRest: live journeys from the public db.transport.rest API (DB's HAFAS, includes S-Bahn, U-Bahn, bus).
- Static: a hand-checked fallback so the demo still works when that free API is down.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

import requests

from barrierfrei.http import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

DB_REST_URL = "https://v6.db.transport.rest"


@dataclass(frozen=True)
class Entrance:
    """A step-free street entrance (OSM node tagged wheelchair=yes, on our walking network)."""

    name: str
    lon: float
    lat: float
    osm_node: int


@dataclass(frozen=True)
class Stop:
    name: str
    lon: float
    lat: float
    eva: str | None = None  # DB's stop id (EVA number), used by timetable APIs
    fasta: int | None = None  # DB station number, used by the FaSta elevator API; None = not a DB station
    note: str = ""  # what our live data does not cover here
    street_verified: bool = True  # False when the street-to-concourse lifts are not in DB's data
    entrances: tuple[Entrance, ...] = ()  # where a step-free walk should start or end


# Stations near our two walking areas. FaSta numbers come from the FaSta API itself; stations without
# one (U-Bahn, tram, bus stops run by local operators) have no live elevator data we can check.
# Entrances are OSM nodes tagged wheelchair=yes (train_station_entrance, or a lift with a street
# level), picked from the Geofabrik Hessen extract on 2026-10-31 and checked to lie on our network.
STOPS = [
    Stop(
        "Darmstadt Hbf",
        8.6297,
        49.8725,
        "8000068",
        1126,
        entrances=(
            Entrance("main entrance (Haupteingang)", 8.631109, 49.872584, 449623591),
            Entrance("entrance Am Fürstenbahnhof", 8.630957, 49.872254, 741625561),
        ),
    ),
    Stop(
        "Frankfurt (Main) Hbf",
        8.6631,
        50.1069,
        "8000105",
        1866,
        entrances=(
            Entrance("north exit (Ausgang Nord)", 8.662313, 50.107988, 441774639),
            Entrance("south exit (Ausgang Süd)", 8.664210, 50.105929, 441774641),
        ),
    ),
    Stop(
        "Frankfurt (Main) Taunusanlage",
        8.6683,
        50.1133,
        None,
        1857,
        # The street lift is DB's lift 10472990 itself, so it is checked live.
        entrances=(Entrance("street lift", 8.668330, 50.113266, 4130839623),),
    ),
    Stop(
        "Frankfurt (Main) Hauptwache",
        8.6787,
        50.1140,
        None,
        1864,
        "DB's lift links the S-Bahn platform to the concourse; the street lifts are run by VGF "
        "and not in DB's live data.",
        street_verified=False,
        entrances=(Entrance("VGF street lift (exit 2)", 8.678079, 50.113935, 312306834),),
    ),
    Stop(
        "Frankfurt (Main) Konstablerwache",
        8.6866,
        50.1148,
        None,
        1865,
        "DB lists only escalators here; the lifts are run by VGF and not in DB's live data.",
        street_verified=False,
        entrances=(Entrance("VGF street lift on the Zeil (exit 2)", 8.687509, 50.114650, 463195376),),
    ),
]


def normalise(name: str) -> str:
    """'Frankfurt(Main)Hauptwache' and 'Frankfurt (Main) Hauptwache' are the same stop."""
    name = name.lower().replace("hauptbahnhof", "hbf").replace("(tief)", "")
    return re.sub(r"[^a-z0-9]", "", name)


def find_stop(name: str) -> Stop | None:
    key = normalise(name)
    return next((s for s in STOPS if normalise(s.name) == key), None)


@dataclass(frozen=True)
class Leg:
    mode: str  # "walk" or a transit product such as "suburban", "subway", "bus"
    line: str | None
    origin: str
    destination: str
    departure: datetime | None
    arrival: datetime | None
    origin_platform: str | None
    destination_platform: str | None
    minutes: float
    coords: tuple[tuple[float, float], tuple[float, float]] | None = None  # (lon, lat) start and end


@dataclass(frozen=True)
class Journey:
    legs: list[Leg]
    source: str  # which provider produced it, shown to the user

    @property
    def minutes(self) -> float:
        """Door-to-door time of the ride part, including waits between live connections."""
        rides = self.rides
        if rides and rides[0].departure and rides[-1].arrival:
            return (rides[-1].arrival - rides[0].departure).total_seconds() / 60
        return sum(leg.minutes for leg in self.legs)

    @property
    def rides(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.mode != "walk"]


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def parse_db_rest(data: dict) -> list[Journey]:
    journeys = []
    for raw in data.get("journeys", []):
        legs = []
        for leg in raw.get("legs", []):
            dep, arr = (
                _time(leg.get("departure") or leg.get("plannedDeparture")),
                _time(leg.get("arrival") or leg.get("plannedArrival")),
            )
            minutes = (arr - dep).total_seconds() / 60 if dep and arr else 0.0
            origin, destination = leg.get("origin", {}), leg.get("destination", {})
            locs = [x.get("location") or {} for x in (origin, destination)]
            coords = None
            if all("longitude" in loc for loc in locs):
                coords = tuple((loc["longitude"], loc["latitude"]) for loc in locs)
            line = leg.get("line") or {}
            legs.append(
                Leg(
                    mode="walk" if leg.get("walking") else line.get("product", "transit"),
                    line=line.get("name"),
                    origin=origin.get("name", ""),
                    destination=destination.get("name", ""),
                    departure=dep,
                    arrival=arr,
                    origin_platform=leg.get("departurePlatform"),
                    destination_platform=leg.get("arrivalPlatform"),
                    minutes=minutes,
                    coords=coords,
                )
            )
        if legs:
            journeys.append(Journey(legs, "db.transport.rest (live timetable)"))
    return journeys


class DbRest:
    def __init__(self, session: requests.Session, timeout: float = DEFAULT_TIMEOUT):
        self.session, self.timeout = session, timeout

    def journeys(
        self, origin: Stop, destination: Stop, results: int = 3, departure: datetime | None = None
    ) -> list[Journey]:
        if not origin.eva:
            return []
        # The destination may lack an EVA number; db.transport.rest accepts an address instead.
        params = {"from": origin.eva, "results": results, "stopovers": "false"}
        if departure:  # leave time to walk to the station
            params["departure"] = departure.isoformat()
        if destination.eva:
            params["to"] = destination.eva
        else:
            params |= {
                "to.name": destination.name,
                "to.latitude": destination.lat,
                "to.longitude": destination.lon,
                "to.address": destination.name,
            }
        resp = self.session.get(f"{DB_REST_URL}/journeys", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return parse_db_rest(resp.json())


# Typical journeys (not live) for when no timetable API is reachable. S3/S4 run from Darmstadt Hbf
# through Frankfurt's S-Bahn tunnel about every 15 minutes; eastbound S-Bahn leaves Hbf (tief) from
# track 101. Each leg: (line, from, from track, to, to track, minutes).
STATIC_JOURNEYS = {
    ("Darmstadt Hbf", "Frankfurt (Main) Hauptwache"): [
        [("S3/S4", "Darmstadt Hbf", None, "Frankfurt (Main) Hauptwache", None, 37)],
        [
            ("RE / RB", "Darmstadt Hbf", None, "Frankfurt (Main) Hbf", None, 18),
            ("S-Bahn", "Frankfurt (Main) Hbf", "101", "Frankfurt (Main) Hauptwache", None, 4),
        ],
    ],
    ("Darmstadt Hbf", "Frankfurt (Main) Konstablerwache"): [
        [("S3/S4", "Darmstadt Hbf", None, "Frankfurt (Main) Konstablerwache", None, 35)],
    ],
    ("Darmstadt Hbf", "Frankfurt (Main) Taunusanlage"): [
        [("S3/S4", "Darmstadt Hbf", None, "Frankfurt (Main) Taunusanlage", None, 39)],
    ],
}
TRANSFER_MINUTES = 6


class Static:
    def journeys(
        self, origin: Stop, destination: Stop, results: int = 3, departure: datetime | None = None
    ) -> list[Journey]:
        found = []
        for plan in STATIC_JOURNEYS.get((origin.name, destination.name), [])[:results]:
            legs = []
            for i, (line, frm, frm_track, to, to_track, minutes) in enumerate(plan):
                a, b = find_stop(frm), find_stop(to)
                coords = ((a.lon, a.lat), (b.lon, b.lat)) if a and b else None
                if i:  # changing trains takes time, especially via lifts
                    legs.append(Leg("walk", None, frm, frm, None, None, None, None, TRANSFER_MINUTES))
                legs.append(Leg("suburban", line, frm, to, None, None, frm_track, to_track, minutes, coords))
            found.append(Journey(legs, "typical timetable (live timetable unavailable)"))
        return found


class Auto:
    """Live timetable when reachable, static fallback otherwise, so a demo never dead-ends."""

    def __init__(self, session: requests.Session | None = None):
        # A free public API may be down for hours: fail fast (no retries, short timeout) and,
        # after the first failure, stop asking for the rest of this run (a circuit breaker).
        self.live, self.static = DbRest(session or requests.Session(), timeout=8), Static()
        self.live_down = False

    def journeys(
        self, origin: Stop, destination: Stop, results: int = 3, departure: datetime | None = None
    ) -> list[Journey]:
        if not self.live_down:
            try:
                found = self.live.journeys(origin, destination, results, departure)
                if found:
                    return found
            except (requests.RequestException, ValueError) as err:
                self.live_down = True
                log.warning("Live timetable unavailable (%s); using the typical timetable", err)
        return self.static.journeys(origin, destination, results, departure)
