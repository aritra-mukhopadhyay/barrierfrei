"""Plan a step-free trip: walk to a station, ride, walk to the destination.

Run: python -m barrierfrei.plan --from luisenplatz --to zeil --profile wheelchair
     python -m barrierfrei.plan ... --out-of-service 10561326   (simulate a broken elevator)

The code decides the route: it checks every station's elevators against live DB data and only
accepts a trip whose elevators work. The language model (with --explain) only explains the result.
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from barrierfrei import routing, stations, store, transit
from barrierfrei.config import get_settings, require
from barrierfrei.http import make_session

log = logging.getLogger("barrierfrei.plan")

PLACES = {
    "luisenplatz": ("Luisenplatz, Darmstadt", 8.6512, 49.8728),
    "darmstadt-hbf": ("Darmstadt Hbf", 8.6297, 49.8725),
    "zeil": ("Zeil, Frankfurt", 8.6826, 50.1144),
    "hauptwache": ("Hauptwache, Frankfurt", 8.6787, 50.1140),
}
MAX_STATION_WALK_M = 2500  # how far we look for a station to start from
MAX_DESTINATION_WALK_M = 1200  # and how far from the destination we may get off
UNVERIFIED_PENALTY_MIN = 10  # prefer stations whose elevators we can check live
BOARDING_BUFFER_MIN = 5  # time for lifts and finding the platform


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

    @property
    def verified(self) -> bool:
        return self.status == "ok"


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
    problems: list[str] = field(default_factory=list)  # e.g. "train leaves before you reach the station"

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
    def verified(self) -> bool:
        """Every lift and walk on the trip was checked against live or map data."""
        return all(c.verified for c in self.checks) and all(t.status == "ok" for t in self.transfer_walks)

    @property
    def minutes(self) -> float:
        walks = [self.walk_start, self.walk_end, *(t.route for t in self.transfer_walks)]
        walk = sum(w.cost for w in walks if w) / 60  # ~1 m/s
        unverified = UNVERIFIED_PENALTY_MIN * (
            sum(c.status == "unverified" for c in self.checks)
            + sum(t.status == "unverified" for t in self.transfer_walks)
        )
        return walk + self.journey.minutes + unverified


def place(text: str) -> tuple[str, float, float]:
    if text.lower() in PLACES:
        return PLACES[text.lower()]
    lat, lon = (float(v) for v in text.split(","))
    return text, lon, lat


MAX_ELEVATOR_OFFSET_M = 500  # FaSta has a few bad coordinates; ignore positions this far from the station


def check_station(name: str, platform: str | None, lookup, transfer: bool = False) -> StationCheck:
    stop = transit.find_stop(name)
    if stop is None or stop.fasta is None:
        return StationCheck(name, platform, "unverified", "no live elevator data for this stop")
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
        status = "broken"
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
    )


def entrance(check: StationCheck) -> tuple[float, float] | None:
    """Where to walk to or from: the street-level lift if we know it, else the station itself."""
    lift = check.use[-1] if check.use else None
    # A platform lift picked without knowing the platform is a guess: walk to the station instead.
    known = lift is not None and (not lift.tracks or check.platform is not None)
    if check.street_entrance and known and lift.lon is not None:
        return (lift.lon, lift.lat)
    stop = transit.find_stop(check.name)
    return (stop.lon, stop.lat) if stop else None


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
                route = routing.walk(network, graph, ends[0], ends[1])
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
) -> list[Option]:
    now = now or datetime.now().astimezone()
    graph = routing.build_graph(network, profile)
    plain = routing.build_graph(network, None)
    starts = [s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), origin) <= MAX_STATION_WALK_M]
    ends = [
        s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), destination) <= MAX_DESTINATION_WALK_M
    ]

    options = []
    for start in starts:
        # Ask for trains we can actually catch: after the walk to this station, plus a buffer for the lift.
        approach = routing.walk(network, graph, origin, (start.lon, start.lat))
        earliest = now + timedelta(minutes=(approach.cost / 60 if approach else 0) + BOARDING_BUFFER_MIN)
        for end in ends:
            for journey in provider.journeys(start, end, departure=earliest):
                if not journey.rides:
                    continue
                checks, transfer_walks = check_journey(journey, station_lookup, network, graph)
                option = Option(journey, checks, transfer_walks)
                board, alight = entrance(checks[0]), entrance(checks[-1])
                if board and alight:
                    option.walk_start = routing.walk(network, graph, origin, board)
                    option.walk_end = routing.walk(network, graph, alight, destination)
                    for a, b, accessible in (
                        (origin, board, option.walk_start),
                        (alight, destination, option.walk_end),
                    ):
                        direct = routing.walk(network, plain, a, b)
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
                    arrive = now + timedelta(minutes=option.walk_start.cost / 60 + BOARDING_BUFFER_MIN)
                    if departure < arrive:
                        option.problems.append("the train leaves before you can reach the platform")
                options.append(option)
    return rank(options)


def rank(options: list[Option]) -> list[Option]:
    """Usable first; among those, a slower trip we could fully verify beats a faster one we couldn't."""
    return sorted(options, key=lambda o: (not o.usable, not o.verified, o.minutes))


def lift_text(check: StationCheck, lift: stations.Elevator) -> str:
    if lift.tracks and check.platform is None:
        platform_lifts = [e for e in check.elevators if e.tracks]
        working = sum(e.working for e in platform_lifts)
        return f"the lift to your platform ({working} of {len(platform_lifts)} platform lifts working)"
    return lift.description or f"lift {lift.equipment}"


def summary(options: list[Option], profile: str) -> dict:
    """A plain JSON description of the plan: input for the map and for the model's explanation."""

    def walk(w: routing.WalkRoute | None):
        return None if w is None else {"metres": round(w.length_m), "notes": w.notes}

    return {
        "profile": profile,
        "options": [
            {
                "usable": o.usable,
                "fully_verified": o.verified,
                "minutes": round(o.minutes),
                "timetable": o.journey.source,
                "rides": [
                    {
                        "line": leg.line,
                        "from": leg.origin,
                        "to": leg.destination,
                        "departure": leg.departure.isoformat() if leg.departure else None,
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
                "walk_to_station": walk(o.walk_start),
                "walk_from_station": walk(o.walk_end),
                "transfer_walks": [
                    {"from": t.origin, "to": t.destination, "status": t.status, "route": walk(t.route)}
                    for t in o.transfer_walks
                ],
                "problems": o.problems,
                "avoided_on_shortest_path": o.avoided,
            }
            for o in options
        ],
    }


EXPLAIN_PROMPT = """You help people with wheelchairs, strollers or heavy suitcases travel step-free.
Below is a trip plan computed and verified by our routing code (profile: {profile}). Explain the
recommended option (the first usable one) to the traveller in 4-6 short sentences: the walk, which
elevators to use, the train, and what was avoided. If other options were rejected, say why in one
sentence. Do not invent anything that is not in the plan.

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--from", dest="origin", default="luisenplatz", help=f"one of {sorted(PLACES)} or 'lat,lon'"
    )
    parser.add_argument("--to", dest="destination", default="zeil")
    parser.add_argument("--profile", choices=routing.PROFILES, default="wheelchair")
    parser.add_argument("--transit", choices=["auto", "live", "static"], default="auto")
    parser.add_argument(
        "--out-of-service",
        type=int,
        nargs="*",
        default=[],
        help="elevator equipment numbers to treat as broken",
    )
    parser.add_argument("--explain", action="store_true", help="ask Muse Spark to explain the plan")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    session = make_session()
    if args.transit == "static":
        provider = transit.Static()
    else:
        provider = transit.DbRest(session) if args.transit == "live" else transit.Auto()
    client_id = require(settings.db_client_id, "DB_CLIENT_ID")
    api_key = require(settings.db_api_key, "DB_API_KEY")
    outages = set(args.out_of_service)

    def lookup(number: int) -> stations.Station:
        station = stations.fetch_station(session, client_id, api_key, number, settings.data_dir / "fasta")
        return stations.with_outages(station, outages) if outages else station

    origin_name, *origin = place(args.origin)
    dest_name, *destination = place(args.destination)
    network = routing.load_network(store.connect(settings.db_path))
    options = plan(tuple(origin), tuple(destination), args.profile, network, provider, lookup)

    result = summary(options, args.profile)
    result |= {"from": origin_name, "to": dest_name}
    out = settings.data_dir / "plans"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    from barrierfrei import mapview

    html = mapview.render(result, options, tuple(origin), tuple(destination))
    (out / "plan.html").write_text(html, encoding="utf-8")

    if not options:
        log.error("No journeys found between %s and %s", origin_name, dest_name)
    for i, option in enumerate(result["options"], 1):
        rides = ", ".join(f"{r['line']} {r['from']} -> {r['to']}" for r in option["rides"])
        flag = ("OK " if option["fully_verified"] else "OK?") if option["usable"] else "NO "
        log.info("%s option %d: %d min, %s", flag, i, option["minutes"], rides)
        for s in option["stations"]:
            log.info("      %s %s: %s %s", s["role"], s["name"], s["status"], s["reason"])
        for problem in option["problems"]:
            log.info("      problem: %s", problem)
    if result["options"] and result["options"][0]["avoided_on_shortest_path"]:
        log.info("Avoided on the walk: %s", ", ".join(result["options"][0]["avoided_on_shortest_path"]))
    log.info("Map: %s", Path(out / "plan.html").resolve())
    if args.explain and options:
        print("\n" + explain(result))


if __name__ == "__main__":
    main()
