"""Build a static demo site of live trip plans for GitHub Pages.

Run: python -m barrierfrei.site
"""

import argparse
import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from barrierfrei import plan, routing, store, transit
from barrierfrei.config import get_settings, require
from barrierfrei.http import make_session

log = logging.getLogger("barrierfrei.site")

REPO_URL = "https://github.com/aritra-mukhopadhyay/barrierfrei"


@dataclass
class DemoTrip:
    slug: str
    title: str
    origin: str
    destination: str
    profile: str = "wheelchair"
    out_of_service: tuple[int, ...] = ()
    tickets: str = "regional"
    note: str = ""


DEMO_TRIPS = [
    DemoTrip(
        slug="luisenplatz-zeil",
        title="Luisenplatz, Darmstadt → Zeil, Frankfurt (wheelchair)",
        origin="luisenplatz",
        destination="zeil",
    ),
    DemoTrip(
        slug="luisenplatz-zeil-broken-lift",
        title="Luisenplatz → Zeil, with a broken lift at Taunusanlage",
        origin="luisenplatz",
        destination="zeil",
        out_of_service=(10472990,),
        note="Simulated: the lift from the concourse to platforms 1/2 at Frankfurt Taunusanlage is "
        "out of service.",
    ),
    DemoTrip(
        slug="tu-darmstadt-staedel",
        title="TU Darmstadt → Städel Museum",
        origin="tu-darmstadt",
        destination="staedel",
    ),
    DemoTrip(
        slug="staatstheater-alte-oper",
        title="Staatstheater Darmstadt → Alte Oper",
        origin="staatstheater",
        destination="alte-oper",
    ),
    DemoTrip(
        slug="darmstadtium-roemer",
        title="darmstadtium → Römer",
        origin="darmstadtium",
        destination="roemer",
    ),
    DemoTrip(
        slug="luisenplatz-zeil-stroller",
        title="Luisenplatz, Darmstadt → Zeil, Frankfurt (stroller)",
        origin="luisenplatz",
        destination="zeil",
        profile="stroller",
    ),
]


def build(out_dir: Path, trips=DEMO_TRIPS, *, network, provider, make_lookup_for, now=None) -> list[dict]:
    """Compute each trip live, write its page and JSON, then the index; a failing trip gets an error."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for trip in trips:
        try:
            lookup = make_lookup_for(trip.out_of_service)
            result, page, _options = plan.run_trip(
                trip.origin, trip.destination, trip.profile, network, provider, lookup, tickets=trip.tickets
            )
        except (SystemExit, Exception) as exc:  # one bad trip must not crash the site build
            if isinstance(exc, SystemExit) and isinstance(exc.code, str):
                details = exc.code
            else:
                details = str(exc) or repr(exc)
            log.warning("Trip %s failed: %s", trip.slug, details)
            entries.append(
                {
                    "slug": trip.slug,
                    "title": trip.title,
                    "note": trip.note,
                    "profile": trip.profile,
                    "error": "Could not compute this trip right now.",
                }
            )
            continue
        (out_dir / f"{trip.slug}.html").write_text(page, encoding="utf-8")
        (out_dir / f"{trip.slug}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        entry: dict = {"slug": trip.slug, "title": trip.title, "note": trip.note, "profile": trip.profile}
        if result.get("options"):
            first = result["options"][0]
            entry |= {
                "usable": first["usable"],
                "fully_verified": first["fully_verified"],
                "minutes": first["minutes"],
                "leave_at": first.get("leave_at"),
                "arrive_at": first.get("arrive_at"),
                "line": first["rides"][0]["line"] if first.get("rides") else None,
            }
        entries.append(entry)
    generated_at = now if now is not None else datetime.now(ZoneInfo("Europe/Berlin"))
    (out_dir / "index.html").write_text(index_html(entries, generated_at), encoding="utf-8")
    return entries


def _verdict(entry: dict) -> str:
    if entry.get("error"):
        return html.escape(str(entry["error"]))
    if "usable" not in entry or not entry["usable"]:
        return "No step-free trip right now"
    if entry.get("fully_verified"):
        return "Step-free, every lift checked"
    return "Step-free, some lifts can't be checked"


def _timing(entry: dict) -> str:
    parts = []
    leave, arrive = entry.get("leave_at"), entry.get("arrive_at")
    if leave and arrive:
        parts.append(f"Leave {html.escape(str(leave))}, arrive about {html.escape(str(arrive))}")
    elif leave:
        parts.append(f"Leave {html.escape(str(leave))}")
    elif arrive:
        parts.append(f"Arrive about {html.escape(str(arrive))}")
    if entry.get("line"):
        parts.append(html.escape(str(entry["line"])))
    return " · ".join(parts)


def index_html(entries: list[dict], generated_at: datetime) -> str:
    """Self-contained index page: no external scripts, readable on a phone."""
    when = html.escape(generated_at.strftime("%Y-%m-%d %H:%M"))
    cards = []
    for entry in entries:
        slug = html.escape(str(entry.get("slug", "")), quote=True)
        title = html.escape(str(entry.get("title", "")))
        note = html.escape(str(entry.get("note", "")))
        timing = _timing(entry)
        body = f'  <div class="card"><h2><a href="{slug}.html">{title}</a></h2>'
        if note:
            body += f"<p>{note}</p>"
        body += f"<p><strong>{_verdict(entry)}</strong></p>"
        if timing:
            body += f"<p>{timing}</p>"
        cards.append(body + "</div>")
    cards_html = "\n".join(cards)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Barrierefrei: step-free trips, checked live</title>
<style>
  body {{ background: #fff; color: #222; font: 16px/1.5 system-ui, sans-serif; margin: 0 auto;
    max-width: 700px; padding: 16px; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin: 12px 0; }}
  h1 {{ font-size: 24px; }} h2 {{ font-size: 18px; margin: 0 0 4px; }}
  a {{ color: #0969da; }}
  p {{ margin: 6px 0; }}
</style></head>
<body>
<h1>Barrierefrei: step-free trips, checked live</h1>
<p>Barrierefrei plans step-free walks from OpenStreetMap and street photos read by Meta's SAM 3.1
and Muse Spark, checks every station's lifts against DB's live lift status, and connects the walks
with live DB departures.</p>
<p>Computed live on {when} (Europe/Berlin) from DB's data; lifts and trains may have changed since.</p>
{cards_html}
<p><a href="{REPO_URL}">Barrierefrei on GitHub</a></p>
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=Path("site"), help="directory for the static site")
    parser.add_argument("--only", nargs="+", default=None, help="only build these trip slugs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    session = make_session()
    client_id = require(settings.db_client_id, "DB_CLIENT_ID")
    api_key = require(settings.db_api_key, "DB_API_KEY")
    fast = requests.Session()
    provider = transit.Auto(
        live=[
            transit.DbTimetables(fast, client_id, api_key),
            transit.DbRest(fast, timeout=8),
        ]
    )
    network = routing.load_network(store.connect(settings.db_path))
    cache_dir = settings.data_dir / "fasta"

    def make_lookup_for(outages):
        return plan.make_lookup(session, client_id, api_key, cache_dir, set(outages))

    trips = DEMO_TRIPS
    if args.only:
        wanted = set(args.only)
        trips = [t for t in DEMO_TRIPS if t.slug in wanted]
    entries = build(args.out, trips, network=network, provider=provider, make_lookup_for=make_lookup_for)
    log.info("Wrote %d trips to %s", len(entries), args.out.resolve())


if __name__ == "__main__":
    main()
