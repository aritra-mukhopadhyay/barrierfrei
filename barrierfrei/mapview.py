"""Render a trip plan as one self-contained HTML page with a Leaflet map."""

import html
import json

ELEVATOR_COLOURS = {"ACTIVE": "#1a7f37", "INACTIVE": "#cf222e"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Barrierfrei trip</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin: 0; font: 15px/1.4 system-ui, sans-serif; display: flex; height: 100vh; background: #fff; color: #222; }}
  #panel {{ width: 380px; overflow-y: auto; padding: 16px; box-sizing: border-box; border-right: 1px solid #ddd; }}
  #maps {{ flex: 1; display: grid; grid-template-rows: 1fr 1fr; }}
  .map {{ min-height: 200px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .opt {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px; margin: 10px 0; }}
  .opt.best {{ border-color: #1a7f37; border-width: 2px; }}
  .opt.bad {{ opacity: .65; }}
  .ok {{ color: #1a7f37; }} .broken {{ color: #cf222e; }} .unverified {{ color: #9a6700; }}
  small {{ color: #555; }}
  @media (max-width: 800px) {{ body {{ flex-direction: column; }} #panel {{ width: auto; height: 45vh; }} }}
</style></head>
<body>
<div id="panel">
  <h1>{title}</h1>
  <small>Profile: <b>{profile}</b>. Green line: step-free walk. Grey dashes: plain shortest walk.</small>
  {options}
</div>
<div id="maps"><div id="start" class="map"></div><div id="end" class="map"></div></div>
<script>
const data = {data};
function draw(id, part) {{
  const map = L.map(id);
  // OSM's tiles (CARTO's answered "API key required" on 2026-11-03). If a viewer shows "Access blocked" for a
  // file:// page (no Referer), so plan.py serves it on 127.0.0.1; re-open with python -m barrierfrei.serve.
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
    {{maxZoom: 19, attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"}}).addTo(map);
  if (location.protocol === "file:") {{
    const notice = L.control({{position: "topright"}});
    notice.onAdd = () => {{
      const div = L.DomUtil.create("div");
      div.style.background = "white";
      div.style.padding = "8px";
      div.textContent = "The map background needs the page served: run python -m barrierfrei.serve";
      return div;
    }};
    notice.addTo(map);
  }}
  const ll = c => [c[1], c[0]];
  const bounds = [];
  (part.direct || []).forEach(line => L.polyline(line.map(ll), {{color: "#888", dashArray: "6 6", weight: 3}}).addTo(map));
  if (part.walk) {{ const p = L.polyline(part.walk.map(ll), {{color: "#1a7f37", weight: 6}}).addTo(map); bounds.push(...p.getLatLngs()); }}
  (part.elevators || []).forEach(e => {{
    if (e.lon == null) return;
    L.circleMarker([e.lat, e.lon], {{radius: e.used ? 9 : 6, color: e.colour, fillColor: e.colour, fillOpacity: .9}})
      .bindPopup(`<b>Elevator ${{e.equipment}}</b><br>${{e.description}}<br>${{e.state}}${{e.used ? "<br><b>use this one</b>" : ""}}`).addTo(map);
  }});
  part.markers.forEach(m => {{ L.marker(ll(m.at)).bindPopup(m.label).addTo(map); bounds.push(L.latLng(ll(m.at))); }});
  map.invalidateSize();  // the container may only now have its final size
  map.fitBounds(L.latLngBounds(bounds).pad(0.2));
}}
window.addEventListener("load", () => {{ draw("start", data.start); draw("end", data.end); }});
</script>
</body></html>
"""


def _elevators(check):
    if check is None:
        return []
    used = {e.equipment for e in check.use}
    return [
        {
            "equipment": e.equipment,
            "description": html.escape(e.description),
            "state": e.state + (f" ({html.escape(e.explanation)})" if e.explanation else ""),
            "lon": e.lon,
            "lat": e.lat,
            "colour": ELEVATOR_COLOURS.get(e.state, "#9a6700"),
            "used": e.equipment in used,
        }
        for e in check.elevators
    ]


def _why_best(option: dict, others: list[dict]) -> str:
    """Say why we recommend a trip that isn't the fastest; people will wonder."""
    faster = [o["minutes"] for o in others if o["usable"] and o["minutes"] < option["minutes"]]
    if faster and option["fully_verified"]:
        return (
            f"<br><small class='ok'>{option['minutes'] - min(faster)} min slower than the fastest option, "
            "but DB's live data shows every lift on it working.</small>"
        )
    return ""


def _option_html(option: dict, best: bool, why: str = "") -> str:
    rides = "".join(
        f"<li>{html.escape(r['line'] or 'Train')}: {html.escape(r['from'])} &rarr; {html.escape(r['to'])}"
        f" ({r['minutes']} min{', dep ' + r['departure'][11:16] if r['departure'] else ''})</li>"
        for r in option["rides"]
    )
    checks = "".join(
        f"<li class='{s['status']}'>{html.escape(s['role'].capitalize() + ': ') if s['role'] else ''}{html.escape(s['name'])}"
        f"{' platform ' + html.escape(s['platform']) if s['platform'] else ''}: {s['status']}"
        f"{' (' + html.escape(s['reason']) + ')' if s['reason'] else ''}"
        f"{'<br><small>Use: ' + html.escape(' then '.join(s['elevators_to_use'])) + '</small>' if s['elevators_to_use'] else ''}</li>"
        for s in option["stations"]
    )
    walks = []
    for label, w in (
        ("Walk to station", option["walk_to_station"]),
        ("Walk from station", option["walk_from_station"]),
    ):
        if w:
            notes = f" <small>({html.escape(', '.join(w['notes']))})</small>" if w["notes"] else ""
            via = f" ({html.escape(w['via'])})" if w.get("via") else ""
            walks.append(f"<li>{label}{via}: {w['metres']} m, about {w['minutes']} min{notes}</li>")
    for t in option["transfer_walks"]:
        walks.insert(
            1,
            f"<li class='{t['status']}'>Walk {html.escape(t['from'])} &rarr; {html.escape(t['to'])}: "
            + (f"{t['route']['metres']} m, about {t['route']['minutes']} min" if t["route"] else t["status"])
            + "</li>",
        )
    problems = "".join(f"<li class='broken'>{html.escape(p)}</li>" for p in option["problems"])
    avoided = option["avoided_on_shortest_path"]
    avoided_html = f"<p><small>Avoided: {html.escape(', '.join(avoided))}</small></p>" if avoided else ""
    cls = "opt best" if best else ("opt" if option["usable"] else "opt bad")
    leave = option.get("leave_at")
    arrive = option.get("arrive_at")
    leave_html = ""
    if leave:
        leave_html = f"<p><b>Leave at {html.escape(leave)}</b>"
        if arrive:
            leave_html += f", arrive about {html.escape(arrive)}"
        leave_html += "</p>"
    later = option.get("later_departures") or []
    later_html = f"<p><small>Same trip later: {html.escape(', '.join(later))}</small></p>" if later else ""
    long_html = (
        "<p><small>Needs a long-distance ticket (ICE/IC/EC are not covered by the "
        "Deutschlandticket).</small></p>"
        if option.get("long_distance")
        else ""
    )
    head = "Recommended" if best else ("Alternative" if option["usable"] else "Not step-free right now")
    if best and not option["fully_verified"]:
        head = "Best we can offer"
        why += (
            "<br><small class='unverified'>No trip right now is fully confirmed (every lift in live "
            "data and every walk on our map); check the parts marked unverified before you go.</small>"
        )
    elif option["usable"] and not option["fully_verified"]:
        head += " <small class='unverified'>(some parts can't be checked)</small>"
    return (
        f"<div class='{cls}'><b>{head}</b> &middot; about {option['minutes']} min{why}{leave_html}{long_html}"
        f"{'<br>' if not leave_html else ''}<small>{html.escape(option['timetable'])}</small>"
        f"<ul>{''.join(walks[:1])}{rides}{''.join(walks[1:])}</ul>{later_html}<b>Elevators</b><ul>{checks}</ul><ul>{problems}</ul>{avoided_html}</div>"
    )


def render(result: dict, options: list, origin, destination) -> str:
    """result is plan.summary(); options are the same plans with geometry."""
    best = next((o for o in options if o.usable), options[0] if options else None)
    start = {"markers": [{"at": origin, "label": html.escape(result["from"])}], "elevators": [], "direct": []}
    end = {
        "markers": [{"at": destination, "label": html.escape(result["to"])}],
        "elevators": [],
        "direct": [],
    }
    if best:
        first, last = best.checks[0], best.checks[-1]
        start |= {
            "walk": best.walk_start.coords if best.walk_start else None,
            "elevators": _elevators(first),
            "direct": best.direct_walk[:1],
        }
        end |= {
            "walk": best.walk_end.coords if best.walk_end else None,
            "elevators": _elevators(last),
            "direct": best.direct_walk[1:],
        }
    best_index = options.index(best) if best else -1
    shown = result["options"]
    panel = "".join(
        _option_html(o, i == best_index and o["usable"], _why_best(o, shown) if i == best_index else "")
        for i, o in enumerate(shown)
    )
    if not result["options"]:
        panel = "<p>No journeys found.</p>"
    return PAGE.format(
        title=f"{html.escape(result['from'])} &rarr; {html.escape(result['to'])}",
        profile=html.escape(result["profile"]),
        options=panel,
        data=json.dumps({"start": start, "end": end}),
    )
