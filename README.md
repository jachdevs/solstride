# nova

Sun- and shade-aware running/walking loops that start where you are.

Open the app → it finds your location → shows current conditions (temperature, UV, sun angle, time to sunset) → pick a distance, Min/Max sun (pre-selected from the weather), and Flat↔Hilly → get a loop that starts and ends at your spot, colored by sun and shade.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env            # add MAPILLARY_CLIENT_TOKEN (optional, enables street photos)
cd backend
../.venv/bin/uvicorn main:app --port 8765
```

Open <http://localhost:8765>. The backend serves the frontend. Geolocation works on `localhost`; elsewhere it needs HTTPS. If permission is denied, tap the map to drop a start pin. To start at a fixed spot, use `?lat=40.4442&lng=-79.9430` (CMU).

To run the frontend from a separate dev server, open it with `?api=http://localhost:8765`. CORS is open.

## Coverage: Pittsburgh (Oakland, Shadyside, Squirrel Hill)

Map data is prebuilt and committed in `backend/data/pittsburgh-oakland.pkl.gz` (7 MB). It covers a 3.5 km radius around CMU (40.4433, -79.9436), which fits 1, 3 and 5 mi loops. Any start inside the dashed circle on the map routes in well under a second, with no map downloads at request time. The server loads the area at startup and refreshes shadows every 5 minutes. Starts outside the circle get a clear "not covered" message.

To rebuild it, or add another area, add an entry to `PRESETS` in `backend/area.py`, then run:

```bash
curl -L -o pennsylvania.osm.pbf https://download.geofabrik.de/north-america/us/pennsylvania-latest.osm.pbf   # 350 MB
cd backend && ../.venv/bin/pip install osmium
../.venv/bin/python build_preset.py --pbf ../pennsylvania.osm.pbf
```

This takes about 2 minutes, offline except for a few elevation requests.

Live, on-demand areas anywhere (downloaded from Overpass, slow and flaky): set `NOVA_ALLOW_LIVE=1`. `warm_cache.py --lat --lng --miles 1 3 5` pre-warms a live area.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /coverage` | Prebuilt areas `{areas:[{name,label,lat,lng,radius}]}` |
| `GET /environment?lat&lng` | Weather (Open-Meteo), UV, cloud cover, sun altitude/azimuth, sunset, `suggested_sun` + reason, notes (e.g. "Cloudy now, so sun mode has little effect.") |
| `GET /route?lat&lng&distance_mi&sun=min\|max&elevation=0..1[&seed]` | Loop as colored `segments`, `stats`, `profile`, `streets`, `degraded`. Outside coverage: **422 `out_of_coverage`**. Live mode only: **202 `{status:"warming"}`** while a new area downloads; the client retries. |

## How it works (backend/)

- **`build_preset.py`**: Crops an OSM `.pbf` extract to a preset area with pyosmium, then builds the graph, shade casters and elevation, and saves `backend/data/<name>.pkl.gz`.
- **`area.py`**: Presets first. For each live area (rounded lat/lng + radius bucket) three downloads run in parallel: the walk graph (`osmnx.graph_from_point`), shade casters (buildings with `height`/`building:levels`, trees, tree rows and woods), and a 14×14 elevation grid from Open-Meteo, interpolated onto nodes. Shadows are computed live for the current sun position, only for casters within 40 m of a walkable edge. Each shadow is the convex hull of the footprint and the footprint shifted by `height / tan(sun altitude)` away from the sun. Each edge gets a sun fraction. Results are cached per 10 minutes of sun movement. Overpass queries are tiled and fall back across mirrors.
- **`router.py`**: Builds triangle loops (start → A → B → start) at 8 bearings. Edge cost = length × sun preference × grade preference × reuse penalty, which avoids out-and-backs. The triangle is rescaled until the length is within 8% of the target, and candidates are scored on length error, sun, hills and overlap.
- **`main.py`**: 10 s budget (`NOVA_ROUTE_TIMEOUT`). If shade or elevation aren't ready in time, it returns a route without them, flags it in `degraded`, and keeps downloading in the background for the next request.
- **`sun.py`**: NOAA solar position. **`environment.py`**: weather and sun-mode suggestion.

## Frontend (frontend/)

Plain HTML/JS with Leaflet and no build step. Geolocation with a drop-a-pin fallback, an environment card with a sun compass, distance chips, a pre-filled sun toggle, a terrain slider, a "Tracing shadows…" loading state, a route colored shade (blue) → sun (amber), stats, an elevation profile with a sun/shade ribbon and street photos.
