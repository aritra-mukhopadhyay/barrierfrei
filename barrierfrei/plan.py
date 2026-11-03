"""Plan a step-free trip: walk to a station, ride, walk to the destination.

Run: python -m barrierfrei.plan --from luisenplatz --to zeil --profile wheelchair
     python -m barrierfrei.plan ... --out-of-service 10561326   (simulate a broken elevator)
     python -m barrierfrei.serve   (re-open the last trip map)

The code decides the route: it checks every station's elevators against live DB data and only
accepts a trip whose elevators work. The language model (with --explain) only explains the result.
"""

import argparse
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests

from barrierfrei import routing, serve, stations, store, transit
from barrierfrei.config import AREAS, get_settings, require
from barrierfrei.http import make_session

log = logging.getLogger("barrierfrei.plan")

PLACES = {
    "luisenplatz": ("Luisenplatz, Darmstadt", 8.6512, 49.8728),
    "darmstadt-hbf": ("Darmstadt Hbf", 8.6297, 49.8725),
    "zeil": ("Zeil, Frankfurt", 8.6826, 50.1144),
    "hauptwache": ("Hauptwache, Frankfurt", 8.6787, 50.1140),
    "tu-darmstadt": ("TU Darmstadt, Karolinenplatz", 8.6566, 49.8749),
    "darmstadtium": ("darmstadtium congress centre, Darmstadt", 8.6587, 49.8745),
    "staatstheater": ("Staatstheater Darmstadt", 8.6505, 49.8680),
    "herrngarten": ("Herrngarten, Darmstadt", 8.6545, 49.8780),
    "frankfurt-hbf": ("Frankfurt (Main) Hbf", 8.6631, 50.1069),
    "roemer": ("Römer, Frankfurt", 8.6821, 50.1105),
    "alte-oper": ("Alte Oper, Frankfurt", 8.6720, 50.1159),
    "staedel": ("Städel Museum, Frankfurt", 8.6739, 50.1031),
    "paulskirche": ("Paulskirche, Frankfurt", 8.6809, 50.1111),
}
MAX_STATION_WALK_M = 2500  # how far we look for a station to start from
MAX_DESTINATION_WALK_M = 1200  # and how far from the destination we may get off
MAX_OFF_MAP_M = 300  # a point farther than this from any mapped path is outside our map
UNVERIFIED_PENALTY_MIN = 10  # prefer stations whose elevators we can check live
BOARDING_BUFFER_MIN = 5  # time for lifts and finding the platform
# Typical speeds on level ground; rough surfaces make the route costlier to choose, not this speed.
WALK_SPEED_M_PER_MIN = {"wheelchair": 60.0, "stroller": 70.0, "suitcase": 70.0}


def walk_minutes(route: routing.WalkRoute | None) -> float:
    if route is None:
        return 0.0
    return route.length_m / WALK_SPEED_M_PER_MIN.get(route.profile or "", 75.0)


@dataclass
class StationCheck:
    name: str
    platform: str | None
    status: str  # "ok", "broken" or "unverified"
    reason: str = ""
    use: list[stations.Elevator] = field(default_factory=list)  # every lift on the way, platform first
    elevators: list[stations.Elevator] = field(default_factory=list)  # all elevators, for the map
    street_entrance: bool = False  # is use[-1] the street-level lift we can walk to?
    role: str = ""  # board, alight, change (arrive) or change (depart)
    stale: bool = False  # lift status came from the cache because FaSta was unreachable

    @property
    def verified(self) -> bool:
        return self.status == "ok" and not self.stale


@dataclass
class TransferWalk:
    origin: str
    destination: str
    route: routing.WalkRoute | None
    status: str  # "ok", "broken" (no step-free path in our map) or "unverified" (outside our map)


@dataclass
class Option:
    journey: transit.Journey
    checks: list[StationCheck]
    transfer_walks: list[TransferWalk] = field(default_factory=list)
    walk_start: routing.WalkRoute | None = None
    walk_end: routing.WalkRoute | None = None
    avoided: list[str] = field(default_factory=list)
    direct_walk: list[list[tuple[float, float]]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # e.g. "no step-free walk found to the station"
    board_via: str = ""  # which station door the walks use
    alight_via: str = ""
    later: list[datetime] = field(default_factory=list)  # first-ride departures of same trips merged here

    @property
    def usable(self) -> bool:
        return (
            all(c.status != "broken" for c in self.checks)
            and all(t.status != "broken" for t in self.transfer_walks)
            and self.walk_start is not None
            and self.walk_end is not None
            and not self.problems
        )

    @property
    def blockers(self) -> list[str]:
        """Every reason this trip is not step-free right now, in words for the traveller."""
        found = [f"{c.name}: {c.reason or 'no working lift'}" for c in self.checks if c.status == "broken"]
        found += [
            f"no step-free walk from {t.origin} to {t.destination}"
            for t in self.transfer_walks
            if t.status == "broken"
        ]
        return found + [p for p in self.problems if p not in found]

    @property
    def verified(self) -> bool:
        """Every lift and walk on the trip was checked against live or map data."""
        return all(c.verified for c in self.checks) and all(t.status == "ok" for t in self.transfer_walks)

    @property
    def walks(self) -> list[routing.WalkRoute]:
        """The walks to and from the stations; walks between trains are already in the journey."""
        return [w for w in (self.walk_start, self.walk_end) if w]

    @property
    def leave_at(self) -> datetime | None:
        """When to leave: the first departure minus the walk to the station and the lift buffer."""
        rides = self.journey.rides
        if not rides or rides[0].departure is None or self.walk_start is None:
            return None
        return rides[0].departure - timedelta(minutes=walk_minutes(self.walk_start) + BOARDING_BUFFER_MIN)

    @property
    def arrive_at(self) -> datetime | None:
        """When you reach the destination: the last arrival plus the walk from the station."""
        rides = self.journey.rides
        if not rides or rides[-1].arrival is None or self.walk_end is None:
            return None
        return rides[-1].arrival + timedelta(minutes=walk_minutes(self.walk_end))

    @property
    def minutes(self) -> float:
        """Door-to-door time we show: real walking time, the rides, and time for the first lift."""
        return sum(walk_minutes(w) for w in self.walks) + BOARDING_BUFFER_MIN + self.journey.minutes

    @property
    def score(self) -> float:
        """What we minimise when choosing: like minutes, but a cobbled or unverifiable trip costs more.

        Kept apart from minutes so penalties steer the choice without lying about the clock.
        """
        walk = sum(w.cost for w in self.walks) / 60
        # A transfer walk's time is in the journey; only its extra cost (cobbles, detours) counts here.
        transfers = sum(
            max(0.0, t.route.cost / 60 - walk_minutes(t.route)) for t in self.transfer_walks if t.route
        )
        unverified = UNVERIFIED_PENALTY_MIN * (
            sum(c.status == "unverified" for c in self.checks)
            + sum(t.status == "unverified" for t in self.transfer_walks)
        )
        return walk + transfers + BOARDING_BUFFER_MIN + self.journey.minutes + unverified

    @property
    def ranked_arrival(self) -> datetime | None:
        """The arrival time, pushed later by the same penalties `score` adds (rough walks, unverified
        stations), so penalties keep steering the choice without changing the times we show.
        """
        arrival = self.arrive_at
        if arrival is None:
            return None
        return arrival + timedelta(minutes=self.score - self.minutes)


def place(text: str) -> tuple[str, float, float]:
    if text.lower() in PLACES:
        return PLACES[text.lower()]
    try:
        lat, lon = (float(v) for v in text.split(","))
    except ValueError:
        raise SystemExit(f"unknown place '{text}': use one of {sorted(PLACES)} or 'lat,lon'") from None
    return text, lon, lat


def check_on_map(network: routing.Network, name: str, point: tuple[float, float]) -> None:
    """Stop when a point lies beyond the streets we have ingested, before any API call.

    Without this, a far-away point would silently snap to the nearest mapped street.
    """
    dist = routing.distance_to_network_m(network, point)
    if dist <= MAX_OFF_MAP_M:
        return
    lon, lat = point
    hits = [key for key, (w, s, e, n) in AREAS.items() if w <= lon <= e and s <= lat <= n]
    hits.sort(key=lambda k: (not k.endswith("-centre"), k))
    if hits:
        area = f"--area {hits[0]}"
    else:
        area = f"--area <name> (one of: {', '.join(sorted(AREAS))})"
    log.error(
        "%s is outside our map (nearest mapped path is %.0f m away). "
        "Ingest that area first: python -m barrierfrei.ingest %s",
        name,
        dist,
        area,
    )
    raise SystemExit(2)


MAX_ELEVATOR_OFFSET_M = 500  # FaSta has a few bad coordinates; ignore positions this far from the station


def check_station(name: str, platform: str | None, lookup, transfer: bool = False) -> StationCheck:
    stop = transit.find_stop(name)
    if stop is None or stop.fasta is None:
        return StationCheck(name, platform, "unverified", "no live elevator data for this stop")
    if platform in stop.level_tracks:
        return StationCheck(
            name, platform, "ok", "level access from the platform to the exits, no lift needed"
        )
    station = lookup(stop.fasta)
    station.elevators = [
        e
        if e.lon is None or routing.haversine_m((e.lon, e.lat), (stop.lon, stop.lat)) <= MAX_ELEVATOR_OFFSET_M
        else stations.Elevator(e.equipment, e.state, e.description, None, None, e.explanation)
        for e in station.elevators
    ]
    if not station.elevators:  # not "broken": someone else may run the lifts, we just can't check them
        return StationCheck(name, platform, "unverified", stop.note or "no elevators in DB's live data")
    result = stations.access(station, platform, transfer)
    if not result.ok:
        status = "unverified" if result.no_data else "broken"
    elif transfer or stop.street_verified:
        status = "ok"
    else:  # the platform lift works, but the way up to the street is someone else's lift
        status = "unverified"
    reason = "; ".join(x for x in (result.reason, "" if transfer else stop.note) if x)
    return StationCheck(
        name,
        platform,
        status,
        reason,
        result.use,
        station.elevators,
        street_entrance=not transfer and stop.street_verified,
        stale=station.stale,
    )


def entrance_lift(check: StationCheck) -> stations.Elevator | None:
    """The street-level lift to walk to, if we know which one it is."""
    lift = check.use[-1] if check.use else None
    # A platform lift picked without knowing the platform is a guess: walk to the station instead.
    known = lift is not None and (not lift.tracks or check.platform is not None)
    return lift if check.street_entrance and known and lift.lon is not None else None


Door = tuple[tuple[float, float], bool, str]  # (point, exact, how we describe it)


def doors(check: StationCheck) -> list[Door]:
    """Where a walk to or from this station may end, best first.

    The live-checked street lift if we know it; else the station's mapped step-free entrances;
    else its map pin, which only marks the area (not exact).
    """
    if lift := entrance_lift(check):
        return [((lift.lon, lift.lat), True, lift.description or "the street lift")]
    stop = transit.find_stop(check.name)
    if stop is None:
        return []
    if stop.entrances:
        return [((e.lon, e.lat), True, e.name) for e in stop.entrances]
    return [((stop.lon, stop.lat), False, "the station")]


def entrance(check: StationCheck) -> tuple[float, float] | None:
    found = doors(check)
    return found[0][0] if found else None


def best_walk(network, graph, point, candidates: list[Door], to_station: bool):
    """The cheapest step-free walk between a point and any of a station's doors."""
    best = None
    for door, exact, label in candidates:
        if to_station:
            route = routing.walk(network, graph, point, door, exact_end=exact)
        else:
            route = routing.walk(network, graph, door, point, exact_start=exact)
        if route and (best is None or route.cost < best[0].cost):
            best = (route, door, exact, label)
    return best


def check_journey(
    journey: transit.Journey, lookup, network, graph
) -> tuple[list[StationCheck], list[TransferWalk]]:
    rides = journey.rides
    checks = [check_station(rides[0].origin, rides[0].origin_platform, lookup)]
    checks[0].role = "board"
    walks = []
    for before, after in zip(rides, rides[1:], strict=False):
        same_station = transit.normalise(before.destination) == transit.normalise(after.origin)
        # Changing trains inside one station stays below street level; walking between two stations doesn't.
        checks.append(
            check_station(before.destination, before.destination_platform, lookup, transfer=same_station)
        )
        checks.append(check_station(after.origin, after.origin_platform, lookup, transfer=same_station))
        checks[-2].role, checks[-1].role = "change (arrive)", "change (depart)"
        if not same_station:
            a, b = (transit.find_stop(n) for n in (before.destination, after.origin))
            ends = [(s.lon, s.lat) for s in (a, b) if s]
            if len(ends) < 2:
                leg = next(
                    (x for x in journey.legs if x.mode == "walk" and x.origin == before.destination), None
                )
                ends = list(leg.coords) if leg and leg.coords else []
            if len(ends) == 2 and all(routing.covered(network, p) for p in ends):
                route = routing.walk(network, graph, ends[0], ends[1], exact_start=False, exact_end=False)
                walks.append(
                    TransferWalk(before.destination, after.origin, route, "ok" if route else "broken")
                )
            else:
                walks.append(TransferWalk(before.destination, after.origin, None, "unverified"))
    checks.append(check_station(rides[-1].destination, rides[-1].destination_platform, lookup))
    checks[-1].role = "alight"
    return checks, walks


def plan(
    origin: tuple[float, float],
    destination: tuple[float, float],
    profile: routing.Profile,
    network: routing.Network,
    provider,
    station_lookup,
    now: datetime | None = None,
    tickets: str = "regional",
) -> list[Option]:
    if tickets not in ("regional", "any"):
        raise ValueError(f"unknown tickets {tickets!r}: use 'regional' or 'any'")
    now = now or datetime.now().astimezone()
    graph = routing.build_graph(network, profile)
    plain = routing.build_graph(network, None)
    starts = [s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), origin) <= MAX_STATION_WALK_M]
    ends = [
        s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), destination) <= MAX_DESTINATION_WALK_M
    ]

    options = []
    for start in starts:
        # Ask for trains we can actually catch. The walk may end at an entrance door or a street
        # lift, and we only learn which from the journey, so ask from the quickest way in: this
        # keeps the cutoff conservative and never hides a catchable train. A train the chosen
        # door still misses is skipped below; the pin is only the last resort.
        try:
            lifts = station_lookup(start.fasta).elevators if start.fasta else []
        except Exception:  # without live data the doors alone still give a cutoff
            lifts = []
        doors_in = [((e.lon, e.lat), True, e.name) for e in start.entrances]
        doors_in += [
            ((lift.lon, lift.lat), True, lift.description or "the street lift")
            for lift in lifts
            if lift.lon is not None and lift.lat is not None
        ]
        ways_in = doors_in or [((start.lon, start.lat), False, "")]
        quickest = [w for door in ways_in if (w := best_walk(network, graph, origin, [door], True))]
        approach = min((w[0] for w in quickest), key=walk_minutes, default=None)
        earliest = now + timedelta(minutes=walk_minutes(approach) + BOARDING_BUFFER_MIN)
        for end in ends:
            for journey in provider.journeys(start, end, departure=earliest):
                if not journey.rides:
                    continue
                if tickets == "regional" and journey.long_distance:
                    continue
                checks, transfer_walks = check_journey(journey, station_lookup, network, graph)
                option = Option(journey, checks, transfer_walks)
                board_doors, alight_doors = doors(checks[0]), doors(checks[-1])
                board = board_doors[0][0] if board_doors else None
                alight = alight_doors[0][0] if alight_doors else None
                if board and alight:
                    # A door must itself be step-free; a station pin only marks the area.
                    board_exact = alight_exact = True
                    if start_walk := best_walk(network, graph, origin, board_doors, True):
                        option.walk_start, board, board_exact, option.board_via = start_walk
                    if end_walk := best_walk(network, graph, destination, alight_doors, False):
                        option.walk_end, alight, alight_exact, option.alight_via = end_walk
                    for a, b, exact_a, exact_b, accessible in (
                        (origin, board, True, board_exact, option.walk_start),
                        (alight, destination, alight_exact, True, option.walk_end),
                    ):
                        direct = routing.walk(network, plain, a, b, exact_a, exact_b)
                        if direct and accessible:
                            option.avoided += [
                                x
                                for x in routing.avoided(network, accessible, direct)
                                if x not in option.avoided
                            ]
                            option.direct_walk.append(direct.coords)
                for label, point in (
                    ("start", origin),
                    ("station", board),
                    ("station", alight),
                    ("destination", destination),
                ):
                    if point and not routing.covered(network, point):
                        msg = f"no street data near the {label}; run ingest for that area"
                        if msg not in option.problems:
                            option.problems.append(msg)
                if board and alight and not option.problems:
                    for label, route in (
                        ("to the station", option.walk_start),
                        ("from the station", option.walk_end),
                    ):
                        if route is None:
                            option.problems.append(f"no step-free walk found {label}")
                departure = journey.rides[0].departure
                if departure and option.walk_start:
                    arrive = now + timedelta(minutes=walk_minutes(option.walk_start) + BOARDING_BUFFER_MIN)
                    if departure < arrive:
                        continue  # a train you cannot catch is not an option
                options.append(option)
    return collapse(rank(options))


def rank(options: list[Option]) -> list[Option]:
    """Usable first, then fully verified, then earliest (penalised) arrival, then score; trips without
    times (the typical timetable) keep today's order by score.
    """
    return sorted(
        options,
        key=lambda o: (
            not o.usable,
            not o.verified,
            o.ranked_arrival is None,
            o.ranked_arrival.timestamp() if o.ranked_arrival else 0.0,
            o.score,
        ),
    )


def collapse(options: list[Option]) -> list[Option]:
    """Merge departures of the same trip into one card, keeping the earliest departure.

    A different platform can mean a different lift, so it stays a separate card; so does a trip
    whose doors, usability or verification differ. Groups keep the position of their best-ranked
    member, and the kept card lists the other departures in `later`. For the same line, platforms
    and doors, the earliest departure also arrives first, so the kept card and the group's position
    agree.
    """

    def key(option: Option):
        rides = tuple(
            (r.line, r.origin, r.destination, r.origin_platform, r.destination_platform)
            for r in option.journey.rides
        )
        return (rides, option.board_via, option.alight_via, option.usable, option.verified)

    def departs(option: Option):
        rides = option.journey.rides
        return rides[0].departure if rides else None

    groups: dict[tuple, list[Option]] = {}
    for option in options:
        groups.setdefault(key(option), []).append(option)
    merged = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        kept = min(group, key=lambda o: ((d := departs(o)) is not None, d))
        kept.later = sorted(d for o in group if o is not kept and (d := departs(o)) is not None)
        merged.append(kept)
    return merged


def lift_text(check: StationCheck, lift: stations.Elevator) -> str:
    if lift.tracks and check.platform is None:
        platform_lifts = [e for e in check.elevators if e.tracks]
        working = sum(e.working for e in platform_lifts)
        return f"the lift to your platform ({working} of {len(platform_lifts)} platform lifts working)"
    return lift.description or f"lift {lift.equipment}"


def summary(options: list[Option], profile: str, tickets: str = "regional") -> dict:
    """A plain JSON description of the plan: input for the map and for the model's explanation."""

    def walk(w: routing.WalkRoute | None):
        if w is None:
            return None
        return {"metres": round(w.length_m), "minutes": round(walk_minutes(w)), "notes": w.notes}

    def truly_avoided(o: Option):
        """Entries of `avoided` the trip no longer crosses on either walk."""
        still = set()
        if o.walk_start and o.walk_start.notes:
            still.update(o.walk_start.notes)
        if o.walk_end and o.walk_end.notes:
            still.update(o.walk_end.notes)
        return [x for x in o.avoided if x not in still]

    return {
        "profile": profile,
        "tickets": tickets,
        "options": [
            {
                "usable": o.usable,
                "fully_verified": o.verified,
                "minutes": round(o.minutes),
                "long_distance": o.journey.long_distance,
                "leave_at": o.leave_at.strftime("%H:%M") if o.leave_at else None,
                "arrive_at": o.arrive_at.strftime("%H:%M") if o.arrive_at else None,
                "later_departures": [d.strftime("%H:%M") for d in o.later],
                "timetable": o.journey.source,
                "rides": [
                    {
                        "line": leg.line,
                        "from": leg.origin,
                        "to": leg.destination,
                        "departure": leg.departure.isoformat() if leg.departure else None,
                        "arrival": leg.arrival.isoformat() if leg.arrival else None,
                        "minutes": round(leg.minutes),
                    }
                    for leg in o.journey.rides
                ],
                "stations": [
                    {
                        "name": c.name,
                        "platform": c.platform,
                        "status": c.status,
                        "reason": c.reason,
                        "role": c.role,
                        "elevators_to_use": [lift_text(c, e) for e in c.use],
                    }
                    for c in o.checks
                ],
                "walk_to_station": walk(o.walk_start) and walk(o.walk_start) | {"via": o.board_via},
                "walk_from_station": walk(o.walk_end) and walk(o.walk_end) | {"via": o.alight_via},
                "transfer_walks": [
                    {"from": t.origin, "to": t.destination, "status": t.status, "route": walk(t.route)}
                    for t in o.transfer_walks
                ],
                "problems": o.blockers,
                "avoided_on_shortest_path": truly_avoided(o),
            }
            for o in options
        ],
    }


EXPLAIN_PROMPT = """You help people with wheelchairs, strollers or heavy suitcases travel step-free.
Below is a trip plan computed and verified by our routing code (profile: {profile}).
Explain the recommended option (the first usable one) in 4-6 short sentences: the walk to the station, which lifts to use, the train with its departure and the time it reaches the station, the walk at the end, and the arrival at the destination.
`leave_at` is when to leave the start and `arrive_at` is the arrival at the destination after the last walk; a ride's own `arrival` is when the train reaches that station. Never mix them up.
Only say a trip avoids something if it is listed in `avoided_on_shortest_path`; surfaces in a walk's `notes` are still on the route.
Other options are still offered, not skipped or rejected, unless their `usable` is false. If an alternative arrives earlier but is not `fully_verified`, say in one sentence that it is faster but some of its lifts can't be checked live. If an option is not usable, say why from its `problems`.
Do not invent anything that is not in the plan. Use the 24-hour times exactly as given.

{plan}"""


def explain(plan_json: dict) -> str:
    from barrierfrei.perceive import make_client

    settings = get_settings()
    client = make_client(settings)
    resp = client.chat.completions.create(
        model=settings.spark_model,
        messages=[
            {
                "role": "user",
                "content": EXPLAIN_PROMPT.format(
                    profile=plan_json["profile"], plan=json.dumps(plan_json, indent=1)
                ),
            }
        ],
    )
    return resp.choices[0].message.content or ""


def make_lookup(
    session, client_id: str, api_key: str, cache_dir: Path, outages: set[int]
) -> Callable[[int], stations.Station]:
    """Live FaSta elevator lookup with simulated outages applied; shared by plan and the demo site."""

    def lookup(number: int) -> stations.Station:
        station = stations.fetch_station(session, client_id, api_key, number, cache_dir)
        return stations.with_outages(station, outages) if outages else station

    return lookup


def run_trip(
    origin: str,
    destination: str,
    profile: str,
    network,
    provider,
    lookup,
    tickets: str = "regional",
) -> tuple[dict, str, list[Option]]:
    """One end-to-end trip: geocode, verify on the map, plan, summarise and render the map page."""
    from barrierfrei import mapview

    origin_name, *origin_point = place(origin)
    dest_name, *dest_point = place(destination)
    check_on_map(network, origin_name, tuple(origin_point))
    check_on_map(network, dest_name, tuple(dest_point))
    options = plan(
        tuple(origin_point), tuple(dest_point), profile, network, provider, lookup, tickets=tickets
    )
    result = summary(options, profile, tickets=tickets)
    result |= {"from": origin_name, "to": dest_name}
    html = mapview.render(result, options, tuple(origin_point), tuple(dest_point))
    return result, html, options


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--from", dest="origin", default="luisenplatz", help=f"one of {sorted(PLACES)} or 'lat,lon'"
    )
    parser.add_argument("--to", dest="destination", default="zeil")
    parser.add_argument("--profile", choices=routing.PROFILES, default="wheelchair")
    parser.add_argument("--transit", choices=["auto", "live", "rest", "static"], default="auto")
    parser.add_argument(
        "--tickets",
        choices=["regional", "any"],
        default="regional",
        help="regional: trains covered by the Deutschlandticket (S-Bahn, RE, RB); any: also ICE, IC and EC",
    )
    parser.add_argument(
        "--out-of-service",
        type=int,
        nargs="*",
        default=[],
        help="elevator equipment numbers to treat as broken",
    )
    parser.add_argument("--explain", action="store_true", help="ask Muse Spark to explain the plan")
    parser.add_argument("--no-serve", action="store_true", help="only write the files, do not open the map")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    session = make_session()
    client_id = require(settings.db_client_id, "DB_CLIENT_ID")
    api_key = require(settings.db_api_key, "DB_API_KEY")
    # Live timetables fail fast with no retries, so the fallback stays instant; FaSta keeps the session.
    fast = requests.Session()
    if args.transit == "static":
        provider = transit.Static()
    elif args.transit == "live":
        provider = transit.DbTimetables(fast, client_id, api_key)
    elif args.transit == "rest":
        provider = transit.DbRest(fast, timeout=8)
    else:
        provider = transit.Auto(
            live=[
                transit.DbTimetables(fast, client_id, api_key),
                transit.DbRest(fast, timeout=8),
            ]
        )
    outages = set(args.out_of_service)
    lookup = make_lookup(session, client_id, api_key, settings.data_dir / "fasta", outages)

    network = routing.load_network(store.connect(settings.db_path))
    result, html, options = run_trip(
        args.origin, args.destination, args.profile, network, provider, lookup, tickets=args.tickets
    )
    origin_name, dest_name = result["from"], result["to"]
    out = settings.data_dir / "plans"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "plan.html").write_text(html, encoding="utf-8")

    if not options:
        log.error("No journeys found between %s and %s", origin_name, dest_name)
        if args.tickets == "regional":
            log.info("No regional trains found; try --tickets any to include ICE, IC and EC.")
    for i, option in enumerate(result["options"], 1):
        rides = ", ".join(f"{r['line']} {r['from']} -> {r['to']}" for r in option["rides"])
        flag = ("OK " if option["fully_verified"] else "OK?") if option["usable"] else "NO "
        dep = option["rides"][0]["departure"]
        extra = f", dep {dep[11:16]}" if dep else ""
        if option["leave_at"]:
            extra += f", leave {option['leave_at']}"
        log.info("%s option %d: %d min%s, %s", flag, i, option["minutes"], extra, rides)
        for s in option["stations"]:
            log.info("      %s %s: %s %s", s["role"], s["name"], s["status"], s["reason"])
        for problem in option["problems"]:
            log.info("      problem: %s", problem)
    if result["options"] and result["options"][0]["avoided_on_shortest_path"]:
        log.info("Avoided on the walk: %s", ", ".join(result["options"][0]["avoided_on_shortest_path"]))
    log.info("Files: %s", out.resolve())
    if args.explain and options:
        print("\n" + explain(result))
    if not args.no_serve and result["options"]:
        serve.serve(out)


if __name__ == "__main__":
    main()
