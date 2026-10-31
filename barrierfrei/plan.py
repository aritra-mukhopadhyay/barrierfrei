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


@dataclass
class StationCheck:
    name: str
    platform: str | None
    status: str  # "ok", "broken" or "unverified"
    reason: str = ""
    elevator: stations.Elevator | None = None  # the street-level elevator we route to
    elevators: list[stations.Elevator] = field(default_factory=list)  # all elevators, for the map


@dataclass
class Option:
    journey: transit.Journey
    checks: list[StationCheck]
    walk_start: routing.WalkRoute | None = None
    walk_end: routing.WalkRoute | None = None
    avoided: list[str] = field(default_factory=list)
    direct_walk: list[list[tuple[float, float]]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return (
            all(c.status != "broken" for c in self.checks)
            and self.walk_start is not None
            and self.walk_end is not None
        )

    @property
    def minutes(self) -> float:
        walk = sum(w.cost for w in (self.walk_start, self.walk_end) if w) / 60  # ~1 m/s
        unverified = sum(UNVERIFIED_PENALTY_MIN for c in self.checks if c.status == "unverified")
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
    status = "ok" if result.ok else "broken"
    return StationCheck(
        name,
        platform,
        status,
        "; ".join(x for x in (result.reason, stop.note) if x),
        result.use[-1] if result.use else None,  # the last group is the street-level one
        station.elevators,
    )


def entrance(check: StationCheck) -> tuple[float, float] | None:
    if check.elevator and check.elevator.lon is not None:
        return (check.elevator.lon, check.elevator.lat)
    stop = transit.find_stop(check.name)
    return (stop.lon, stop.lat) if stop else None


def plan(
    origin: tuple[float, float],
    destination: tuple[float, float],
    profile: routing.Profile,
    network: routing.Network,
    provider,
    station_lookup,
) -> list[Option]:
    graph = routing.build_graph(network, profile)
    plain = routing.build_graph(network, None)
    starts = [s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), origin) <= MAX_STATION_WALK_M]
    ends = [
        s for s in transit.STOPS if routing.haversine_m((s.lon, s.lat), destination) <= MAX_DESTINATION_WALK_M
    ]

    options = []
    for start in starts:
        for end in ends:
            for journey in provider.journeys(start, end):
                rides = journey.rides
                if not rides:
                    continue
                checks = [check_station(rides[0].origin, rides[0].origin_platform, station_lookup)]
                for before, after in zip(rides, rides[1:], strict=False):  # transfers
                    checks.append(
                        check_station(before.destination, before.destination_platform, station_lookup)
                    )
                    checks.append(check_station(after.origin, after.origin_platform, station_lookup))
                checks.append(
                    check_station(rides[-1].destination, rides[-1].destination_platform, station_lookup)
                )
                option = Option(journey, checks)
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
                options.append(option)
    return sorted(options, key=lambda o: (not o.usable, o.minutes))


def summary(options: list[Option], profile: str) -> dict:
    """A plain JSON description of the plan: input for the map and for the model's explanation."""

    def walk(w: routing.WalkRoute | None):
        return None if w is None else {"metres": round(w.length_m), "notes": w.notes}

    return {
        "profile": profile,
        "options": [
            {
                "usable": o.usable,
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
                        "elevator": c.elevator.description if c.elevator else None,
                    }
                    for c in o.checks
                ],
                "walk_to_station": walk(o.walk_start),
                "walk_from_station": walk(o.walk_end),
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
        provider = transit.DbRest(session) if args.transit == "live" else transit.Auto(session)
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
    (out / "plan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    from barrierfrei import mapview

    html = mapview.render(result, options, tuple(origin), tuple(destination))
    (out / "plan.html").write_text(html, encoding="utf-8")

    if not options:
        log.error("No journeys found between %s and %s", origin_name, dest_name)
    for i, option in enumerate(result["options"], 1):
        rides = ", ".join(f"{r['line']} {r['from']} -> {r['to']}" for r in option["rides"])
        flag = "OK " if option["usable"] else "NO "
        log.info("%s option %d: %d min, %s", flag, i, option["minutes"], rides)
        for s in option["stations"]:
            log.info("      %s: %s %s", s["name"], s["status"], s["reason"])
    if result["options"] and result["options"][0]["avoided_on_shortest_path"]:
        log.info("Avoided on the walk: %s", ", ".join(result["options"][0]["avoided_on_shortest_path"]))
    log.info("Map: %s", Path(out / "plan.html").resolve())
    if args.explain and options:
        print("\n" + explain(result))


if __name__ == "__main__":
    main()
