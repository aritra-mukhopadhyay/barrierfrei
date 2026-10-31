"""Live elevator status at DB stations (FaSta API), with a short-lived cache and stale fallback.

Elevator status is the one input that changes minute to minute, so we always try a live call and
only fall back to the last cached answer (clearly marked stale) when the API is down.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from barrierfrei.http import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

FASTA_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/fasta/v2/stations/{number}"
CACHE_TTL_S = 300  # elevator status is live data; 5 minutes old is still useful, an hour is not
# Lifts that are not for passengers: freight, tenants, the first-class lounge.
NOT_PUBLIC = re.compile(r"lasten|mieter|lounge", re.IGNORECASE)


@dataclass(frozen=True)
class Elevator:
    equipment: int
    state: str  # ACTIVE, INACTIVE or UNKNOWN
    description: str
    lon: float | None
    lat: float | None
    explanation: str = ""

    @property
    def working(self) -> bool:
        return self.state == "ACTIVE"

    @property
    def tracks(self) -> set[str]:
        """Platform tracks the description mentions, e.g. 'zu Gleis 1/2' -> {'1', '2'}."""
        found: set[str] = set()
        for match in re.finditer(r"(?:Gleis(?:e)?|Gl\.)\s*([\d\s/,+und-]+)", self.description):
            found |= set(re.findall(r"\d+", match.group(1)))
        return found


@dataclass
class Station:
    number: int
    name: str
    elevators: list[Elevator]
    fetched_at: float
    stale: bool = False


@dataclass
class Access:
    """Can this station be used step-free, and through which elevators?"""

    station: Station
    ok: bool
    use: list[Elevator] = field(default_factory=list)  # working elevators on the way
    broken: list[Elevator] = field(default_factory=list)  # relevant elevators that are out of service
    reason: str = ""


def parse_station(data: dict, fetched_at: float, stale: bool = False) -> Station:
    elevators = [
        Elevator(
            equipment=int(f["equipmentnumber"]),
            state=f.get("state", "UNKNOWN"),
            description=f.get("description") or "",
            lon=f.get("geocoordX"),
            lat=f.get("geocoordY"),
            explanation=f.get("stateExplanation") or "",
        )
        for f in data.get("facilities", [])
        if f.get("type") == "ELEVATOR" and not NOT_PUBLIC.search(f.get("description") or "")
    ]
    return Station(int(data["stationnumber"]), data.get("name", ""), elevators, fetched_at, stale)


def fetch_station(
    session: requests.Session, client_id: str, api_key: str, number: int, cache_dir: Path
) -> Station:
    cache = cache_dir / f"{number}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        return parse_station(json.loads(cache.read_text(encoding="utf-8")), cache.stat().st_mtime)
    try:
        resp = session.get(
            FASTA_URL.format(number=number),
            headers={"DB-Client-Id": client_id, "DB-Api-Key": api_key, "accept": "application/json"},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as err:
        if cache.exists():  # better a slightly old answer, clearly labelled, than none
            log.warning("FaSta unavailable for %s (%s); using cached status", number, err)
            return parse_station(
                json.loads(cache.read_text(encoding="utf-8")), cache.stat().st_mtime, stale=True
            )
        raise
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return parse_station(data, time.time())


def with_outages(station: Station, out_of_service: set[int]) -> Station:
    """Pretend some elevators are broken, to demo and test re-routing."""
    elevators = [
        Elevator(e.equipment, "INACTIVE", e.description, e.lon, e.lat, "simulated outage")
        if e.equipment in out_of_service
        else e
        for e in station.elevators
    ]
    return Station(station.number, station.name, elevators, station.fetched_at, station.stale)


def access(station: Station, track: str | None = None, transfer: bool = False) -> Access:
    """Step-free access between street and platform.

    With a known track we need a working elevator that serves it, plus one of the elevators whose
    description names no track (street to concourse), if the station has any. Without track
    information, any working elevator counts. When changing trains (transfer=True) we stay below
    street level, so only the platform elevator matters. This is a heuristic on free-text
    descriptions, so the result says why.
    """
    if not station.elevators:
        return Access(station, False, reason="no elevators listed")
    platform_lifts = [e for e in station.elevators if e.tracks]
    general = [e for e in station.elevators if not e.tracks]
    if platform_lifts:
        if track:
            groups = [(f"track {track}", [e for e in platform_lifts if track in e.tracks])]
        else:  # platform unknown: any working platform lift is our best guess
            groups = [("a platform", platform_lifts)]
        if general and not transfer:  # not knowing the platform doesn't remove the street-level lift
            groups.append(("street level", general))
    else:  # descriptions don't say which platform: any working elevator is our best guess
        groups = [("the station", station.elevators)]

    use: list[Elevator] = []
    broken: list[Elevator] = []
    missing: list[str] = []
    for label, group in groups:
        if not group:
            missing.append(f"no elevator listed for {label}")
            continue
        working = [e for e in group if e.working]
        broken += [e for e in group if not e.working]
        if working:
            use.append(working[0])
        else:
            missing.append(f"every elevator for {label} is out of service")
    if missing:
        return Access(station, False, use, broken, "; ".join(missing))
    return Access(station, True, use, broken, "stale status" if station.stale else "")
