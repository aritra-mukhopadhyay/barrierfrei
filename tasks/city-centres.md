# Cover both city centres and name more places to plan from and to

## Context
The walking network covers two small boxes today (`AREAS` in `barrierfrei/config.py`): Darmstadt Hbf
to Luisenplatz, and Frankfurt Hbf to the Zeil. The demo region is the S-Bahn corridor between
the two city centres: anywhere in central Darmstadt to anywhere in central Frankfurt. That needs
bigger areas, more named places, and a clear error when a point lies outside the map (today a
far-away point silently snaps to the nearest street we have).

## Change
1. `barrierfrei/config.py` `AREAS`: keep the two existing entries unchanged (tests and old commands
   use them) and add, with a comment saying what each covers:
   - `"darmstadt-centre": (8.620, 49.860, 8.672, 49.885)`: Hbf, Luisenplatz, the TU, Mathildenhöhe, Staatstheater.
   - `"frankfurt-centre": (8.650, 50.098, 8.700, 50.122)`: Hbf, Bahnhofsviertel, Römer, Zeil, Alte Oper, Museumsufer.
2. `barrierfrei/plan.py` `PLACES`: keep the existing four and add (name, lon, lat):
   - `"tu-darmstadt": ("TU Darmstadt, Karolinenplatz", 8.6566, 49.8749)`
   - `"mathildenhoehe": ("Mathildenhöhe, Darmstadt", 8.6668, 49.8770)`
   - `"staatstheater": ("Staatstheater Darmstadt", 8.6505, 49.8680)`
   - `"herrngarten": ("Herrngarten, Darmstadt", 8.6545, 49.8780)`
   - `"frankfurt-hbf": ("Frankfurt (Main) Hbf", 8.6631, 50.1069)`
   - `"roemer": ("Römer, Frankfurt", 8.6821, 50.1105)`
   - `"alte-oper": ("Alte Oper, Frankfurt", 8.6720, 50.1159)`
   - `"staedel": ("Städel Museum, Frankfurt", 8.6739, 50.1031)`
   - `"paulskirche": ("Paulskirche, Frankfurt", 8.6809, 50.1111)`
3. `barrierfrei/routing.py`: new function
   `distance_to_network_m(network: Network, point: tuple[float, float]) -> float`: the distance in
   metres from `(lon, lat)` to the closest node in `network.node_coords` (`haversine_m`), or
   `math.inf` when the network is empty.
4. `barrierfrei/plan.py`: constant `MAX_OFF_MAP_M = 300`. In `main()`, right after
   `network = routing.load_network(...)`, for the origin and the destination: when
   `routing.distance_to_network_m(network, point) > MAX_OFF_MAP_M`, log at ERROR
   `"<name> is outside our map (nearest mapped path is <N> m away). Ingest that area first: python -m barrierfrei.ingest --area <one of AREAS>"`
   and `raise SystemExit(2)`, before any API call is made. Also make `place()` raise
   `SystemExit` with the message `unknown place '<text>': use one of <sorted PLACES> or 'lat,lon'`
   instead of a bare `ValueError` when the text is neither a known name nor two numbers.
5. `README.md`: in the pipeline table, the ingest rows use `--area darmstadt-centre` and
   `--area frankfurt-centre`; the plan row lists the place names (or `lat,lon`). Add one sentence that
   the demo region is central Darmstadt to central Frankfurt.

## Tests
- `tests/test_routing.py::test_distance_to_network`: a tiny network built like the other routing
  tests: a point on a node gives about 0, a point 1 km away gives about 1000 (±5 %), an empty
  network gives `math.inf`.
- `tests/test_plan.py::test_place_names_and_errors`: every `PLACES` value lies inside one of
  `AREAS["darmstadt-centre"]` / `AREAS["frankfurt-centre"]`; `place("49.87,8.65")` returns
  `("49.87,8.65", 8.65, 49.87)`; `place("nowhere")` raises `SystemExit` whose message contains `luisenplatz`.
- `tests/test_plan.py::test_new_areas_contain_old_ones`: each new centre box contains its old box.

## Out of scope
- Do not change routing costs, ranking, transit, stations, mapview or ingest logic.
- No geocoding service and no new dependencies.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
