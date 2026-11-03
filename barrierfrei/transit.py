"""Public transport between the two walking areas.

Three providers behind one interface:
- DbTimetables: live direct trains from DB's Timetables API (station boards, DB rail only).
- DbRest: live journeys from the public db.transport.rest API (DB's HAFAS, includes S-Bahn, U-Bahn, bus).
- Static: a hand-checked fallback so the demo still works when those APIs are down.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from barrierfrei.http import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

DB_REST_URL = "https://v6.db.transport.rest"
TIMETABLES_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"

# Our names for the Frankfurt tunnel stops have no EVA number; the IRIS name is the lookup pattern.
IRIS_NAMES = {
    "Frankfurt (Main) Taunusanlage": "Frankfurt(M)Taunusanlage",
    "Frankfurt (Main) Hauptwache": "Frankfurt(M)Hauptwache",
    "Frankfurt (Main) Konstablerwache": "Frankfurt(M)Konstablerwache",
}


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
    level_tracks: frozenset[str] = frozenset()  # tracks reached from the street without any lift


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
        # Terminus tracks in the main hall, level with the north and south exits.
        level_tracks=frozenset(str(n) for n in range(1, 25)),
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


def _trip_key(stop_id: str) -> str:
    """The trip part of an IRIS stop id (`<trip>-<YYMMDDHHmm>-<index>`; `<trip>` may start with `-`)."""
    parts = stop_id.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else stop_id


def _iris_time(value: str | None, tz: ZoneInfo) -> datetime | None:
    """Parse `YYMMDDHHmm` in German local time; None when missing or malformed (skip, don't guess)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=tz)
    except ValueError:
        return None


def _cancelled(*elements: ET.Element | None) -> bool:
    return any(el is not None and el.get("cs") == "c" for el in elements)


class DbTimetables:
    """Live direct trains from DB's Timetables API (IRIS station boards)."""

    def __init__(self, session: requests.Session, client_id: str, api_key: str, timeout: float = 8):
        self.session, self.client_id, self.api_key, self.timeout = session, client_id, api_key, timeout
        self._eva_cache: dict[str, str | None] = {}
        self._xml_cache: dict[str, ET.Element] = {}

    def _get_xml(self, url: str) -> ET.Element:
        if url in self._xml_cache:
            return self._xml_cache[url]
        resp = self.session.get(
            url,
            headers={"DB-Client-Id": self.client_id, "DB-Api-Key": self.api_key},
            timeout=self.timeout,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as err:
            status = getattr(getattr(err, "response", None), "status_code", None)
            if "/plan/" in url and status == 404:  # no planned trains for that hour
                root = ET.fromstring("<timetable/>")
                self._xml_cache[url] = root
                return root
            raise
        data = getattr(resp, "text", None)
        if data is None:
            data = resp.content
        try:
            root = ET.fromstring(data)
        except ET.ParseError as err:
            raise ValueError(f"unparseable timetable XML from {url}") from err
        self._xml_cache[url] = root
        return root

    def _eva(self, stop: Stop) -> str | None:
        if stop.eva:
            return stop.eva
        if stop.name in self._eva_cache:
            return self._eva_cache[stop.name]
        pattern = IRIS_NAMES.get(stop.name)
        if not pattern:
            self._eva_cache[stop.name] = None
            return None
        root = self._get_xml(f"{TIMETABLES_URL}/station/{pattern}")
        stations = root.findall("station")
        found = next((s for s in stations if s.get("name") == pattern), None)
        found = found if found is not None else (stations[0] if stations else None)
        eva = found.get("eva") if found is not None else None
        self._eva_cache[stop.name] = eva or None
        return self._eva_cache[stop.name]

    def _changes(self, eva: str) -> dict[str, ET.Element]:
        root = self._get_xml(f"{TIMETABLES_URL}/fchg/{eva}")
        return {s.get("id"): s for s in root.findall("s") if s.get("id")}

    def journeys(
        self, origin: Stop, destination: Stop, results: int = 3, departure: datetime | None = None
    ) -> list[Journey]:
        try:
            berlin = ZoneInfo("Europe/Berlin")
        except ZoneInfoNotFoundError as err:
            raise ValueError("Europe/Berlin timezone unavailable") from err
        if departure is None:
            departure = datetime.now(berlin)
        elif departure.tzinfo is None:  # naive values are Berlin time, not the host's offset
            departure = departure.replace(tzinfo=berlin)
        dep_berlin = departure.astimezone(berlin)

        origin_eva, dest_eva = self._eva(origin), self._eva(destination)
        if not origin_eva or not dest_eva:
            return []

        origin_changes, dest_changes = self._changes(origin_eva), self._changes(dest_eva)

        def board(eva: str, when: datetime) -> ET.Element:
            return self._get_xml(
                f"{TIMETABLES_URL}/plan/{eva}/{when.strftime('%y%m%d')}/{when.strftime('%H')}"
            )

        origin_stops: list[ET.Element] = []
        for offset in (-1, 0, 1):
            origin_stops += board(origin_eva, dep_berlin + timedelta(hours=offset)).findall("s")

        dest_station: str | None = None
        dest_stops: list[ET.Element] = []
        for offset in (-1, 0, 1, 2):
            root = board(dest_eva, dep_berlin + timedelta(hours=offset))
            if dest_station is None:
                dest_station = root.get("station")
            dest_stops += root.findall("s")
        if not dest_station:
            return []

        dest_by_trip: dict[str, list[tuple]] = {}
        dest_by_train: dict[tuple[str, str], list[tuple]] = {}
        for s in dest_stops:
            sid = s.get("id")
            ar = s.find("ar")
            if not sid or ar is None:
                continue
            change = dest_changes.get(sid)
            changed_ar = change.find("ar") if change is not None else None
            if _cancelled(s, ar, change, changed_ar):
                continue
            ct = changed_ar.get("ct") if changed_ar is not None else None
            arrival = _iris_time(ct or ar.get("pt"), berlin)
            if arrival is None:
                continue
            tl = s.find("tl")
            c = tl.get("c") if tl is not None else ""
            n = tl.get("n") if tl is not None else ""
            cp = changed_ar.get("cp") if changed_ar is not None else None
            entry = (arrival, cp or ar.get("pp"))
            dest_by_trip.setdefault(_trip_key(sid), []).append(entry)
            dest_by_train.setdefault((c or "", n or ""), []).append(entry)

        found: list[tuple[datetime, Journey]] = []
        for s in origin_stops:
            sid = s.get("id")
            dp = s.find("dp")
            if not sid or dp is None:
                continue
            change = origin_changes.get(sid)
            changed_dp = change.find("dp") if change is not None else None
            if _cancelled(s, dp, change, changed_dp):
                continue
            ct = changed_dp.get("ct") if changed_dp is not None else None
            departs = _iris_time(ct or dp.get("pt"), berlin)
            if departs is None or departs < departure:
                continue
            cpth = changed_dp.get("cpth") if changed_dp is not None else None
            path = (cpth or dp.get("ppth") or "").split("|")
            if dest_station not in path:
                continue
            tl = s.find("tl")
            c = tl.get("c") if tl is not None else ""
            n = tl.get("n") if tl is not None else ""
            matches = dest_by_trip.get(_trip_key(sid)) or dest_by_train.get((c or "", n or ""), [])
            arrivals = sorted(a for a, _ in matches if a >= departs)
            if not arrivals:
                continue
            arrival = arrivals[0]
            dest_platform = next(p for a, p in matches if a == arrival)
            mode = "suburban" if c == "S" else "regional" if c in ("RE", "RB") else "national"
            # Real boards give l="S6" (category included); some give only the number.
            label = dp.get("l")
            line = (label if not label.isdigit() else f"{c}{label}") if label else f"{c} {n}"
            cp = changed_dp.get("cp") if changed_dp is not None else None
            leg = Leg(
                mode=mode,
                line=line,
                origin=origin.name,
                destination=destination.name,
                departure=departs,
                arrival=arrival,
                origin_platform=cp or dp.get("pp"),
                destination_platform=dest_platform,
                minutes=(arrival - departs).total_seconds() / 60,
                coords=((origin.lon, origin.lat), (destination.lon, destination.lat)),
            )
            found.append((departs, Journey([leg], "DB Timetables (live)")))
        found.sort(key=lambda item: item[0])
        return [journey for _, journey in found[:results]]


# Typical journeys (not live) for when no timetable API is reachable. The S6 runs from Darmstadt Hbf
# at :05 and :35 and enters Frankfurt's S-Bahn tunnel from Frankfurt Süd, as seen in DB Timetables
# on 2026-11-03; eastbound S-Bahn leaves Hbf (tief) from track 101.
# Each leg: (line, from, from track, to, to track, minutes).
STATIC_JOURNEYS = {
    ("Darmstadt Hbf", "Frankfurt (Main) Hauptwache"): [
        [("S6", "Darmstadt Hbf", None, "Frankfurt (Main) Hauptwache", None, 35)],
        [
            ("RE / RB", "Darmstadt Hbf", None, "Frankfurt (Main) Hbf", None, 18),
            ("S-Bahn", "Frankfurt (Main) Hbf", "101", "Frankfurt (Main) Hauptwache", None, 4),
        ],
    ],
    ("Darmstadt Hbf", "Frankfurt (Main) Konstablerwache"): [
        [("S6", "Darmstadt Hbf", None, "Frankfurt (Main) Konstablerwache", None, 33)],
    ],
    ("Darmstadt Hbf", "Frankfurt (Main) Taunusanlage"): [
        [("S6", "Darmstadt Hbf", None, "Frankfurt (Main) Taunusanlage", None, 37)],
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
    """Live timetables in order when reachable, static fallback otherwise, so a demo never dead-ends."""

    def __init__(self, session: requests.Session | None = None, *, live: list | None = None):
        # A free public API may be down for hours: fail fast (no retries, short timeout) and,
        # after the first failure, stop asking that provider for the rest of this run.
        self.lives = live if live is not None else [DbRest(session or requests.Session(), timeout=8)]
        self.static = Static()
        self._down: set[int] = set()

    def journeys(
        self, origin: Stop, destination: Stop, results: int = 3, departure: datetime | None = None
    ) -> list[Journey]:
        for i, provider in enumerate(self.lives):
            if i in self._down:
                continue
            try:
                if found := provider.journeys(origin, destination, results, departure):
                    return found
            except (requests.RequestException, ValueError) as err:
                self._down.add(i)
                if any(j not in self._down for j in range(i + 1, len(self.lives))):
                    log.warning("Live timetable unavailable (%s); trying the next provider", err)
                else:
                    log.warning("Live timetable unavailable (%s); using the typical timetable", err)
        return self.static.journeys(origin, destination, results, departure)
