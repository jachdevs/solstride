# nova

Sun- and shade-aware running/walking loops that start where you are.

Open the app → it finds your location → shows current conditions (temperature, UV, sun angle, time to sunset) → pick a distance, Min/Max sun (pre-selected from the weather), and Flat↔Hilly → get a loop that starts and ends at your spot, colored by sun and shade.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env            # add your OPENROUTER_API_KEY (optional, enables route summaries)
cd backend
../.venv/bin/uvicorn main:app --port 8765
```

Open <http://localhost:8765>. The backend serves the frontend. Geolocation works on `localhost`; elsewhere it needs HTTPS. If permission is denied, tap the map to drop a start pin. You can also pass a start point in the URL: `?lat=37.7793&lng=-122.4193`.

To run the frontend from a separate dev server, open it with `?api=http://localhost:8765`. CORS is open.

## Before a demo: warm the cache

The first request for a new area downloads streets, buildings and trees from OpenStreetMap (Overpass), which can take 30 s to 2+ min. Warm the exact demo spot ahead of time:

```bash
cd backend
../.venv/bin/python warm_cache.py --lat <LAT> --lng <LNG> --miles 1 3 5
```

Areas are pickled to `backend/.cache/` (gitignored), so they survive restarts. The cache key is lat/lng rounded to 3 decimals (~100 m), so warm the point you'll actually start from. Keep a recorded video as a backup.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /environment?lat&lng` | Weather (Open-Meteo), UV, cloud cover, sun altitude/azimuth, sunset, `suggested_sun` + reason, notes (e.g. "Cloudy now, so sun mode has little effect.") |
| `GET /route?lat&lng&distance_mi&sun=min\|max&elevation=0..1[&seed]` | Loop as colored `segments`, `stats`, `profile`, `streets`, `degraded`. Returns **202 `{status:"warming"}`** while a new area downloads; the client retries. |
| `POST /describe` | 2-sentence route summary from Claude Fable 5.1 via OpenRouter. Called after the map renders, so it never slows `/route`. |

## How it works (backend/)

- **`area.py`**: For each area (rounded lat/lng + radius bucket) three downloads run in parallel: the walk graph (`osmnx.graph_from_point`), shade casters (buildings with `height`/`building:levels`, trees, tree rows and woods), and a 14×14 elevation grid from Open-Meteo, interpolated onto nodes. Shadows are computed live for the current sun position, only for casters within 40 m of a walkable edge. Each shadow is the convex hull of the footprint and the footprint shifted by `height / tan(sun altitude)` away from the sun. Each edge gets a sun fraction. Results are cached per 10 minutes of sun movement. Overpass queries are tiled and fall back across mirrors.
- **`router.py`**: Builds triangle loops (start → A → B → start) at 8 bearings. Edge cost = length × sun preference × grade preference × reuse penalty, which avoids out-and-backs. The triangle is rescaled until the length is within 8% of the target, and candidates are scored on length error, sun, hills and overlap.
- **`main.py`**: 10 s budget (`NOVA_ROUTE_TIMEOUT`). If shade or elevation aren't ready in time, it returns a route without them, flags it in `degraded`, and keeps downloading in the background for the next request.
- **`sun.py`**: NOAA solar position. **`environment.py`**: weather and sun-mode suggestion. **`describe.py`**: OpenRouter call.

## Frontend (frontend/)

Plain HTML/JS with Leaflet and no build step. Geolocation with a drop-a-pin fallback, an environment card with a sun compass, distance chips, a pre-filled sun toggle, a terrain slider, a "Tracing shadows…" loading state, a route colored shade (blue) → sun (amber), stats, an elevation profile with a sun/shade ribbon, and the AI summary.
