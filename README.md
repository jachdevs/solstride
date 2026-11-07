# SolStride

**Loops that follow the light, or dodge it.**

SolStride is a running and walking route prototype that builds loops near your starting point from distance, sun or shade preference, terrain, and a plain-language description of the scenery you want. It combines geographic shadow estimates with street-level image matching to choose between candidate routes.

## Features

- Start from browser geolocation, a pin dropped on the map, or coordinates in the URL.
- Choose 1, 3 or 5 miles, or enter a custom distance.
- Prefer sun or shade, and set a Flat–Hilly slider.
- See current weather, feels-like temperature, UV, sun direction and altitude, and time to sunset. The weather sets the suggested sun preference.
- Describe a route vibe, such as "A tree-lined residential street," or pick a scenery preset.
- Use pretrained CLIP to rerank candidate loops by sampled street photos.
- See the chosen route on a map with sun/shade colors, numbered photo markers, distance, elevation gain, estimated duration and an elevation profile.
- Mobile version (`/mobile.html`) with run tracking: timer, distance, pace, progress and a post-run summary.

## Run locally

Python 3.11 is a reasonable starting environment. Dependencies are in `backend/requirements.txt`.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend
python -m uvicorn main:app --port 8765
```

Open <http://localhost:8765>, or <http://localhost:8765/mobile.html> for the mobile version.

The backend serves the frontend, so there is no frontend build step. Geolocation works on localhost; elsewhere it generally needs HTTPS. If location permission is denied, click the map to choose a start.

For a separately hosted frontend, add `?api=http://localhost:8765`. The backend currently allows all CORS origins.

### Optional credentials

To get real street photos, create `.env` in the repository root (see `.env.example`):

```dotenv
MAPILLARY_CLIENT_TOKEN=your_mapillary_client_token
# GOOGLE_MAPS_API_KEY=optional_fallback
```

Keep `.env` out of version control. Without a token, the photo section is hidden.

CLIP runs locally using `openai/clip-vit-base-patch32`. Its processor and weights download from Hugging Face on first use, and the server preloads the model at startup. Inference runs on CPU by default. Photos, weather, map tiles and the first model download need network access.

### Coverage

Routes are instant inside the prebuilt Pittsburgh area: Oakland, Shadyside and Squirrel Hill, a 3.5 km radius around CMU. Its data is in `backend/data/pittsburgh-oakland.pkl.gz`. Starts outside it get a "not covered" message. To add an area, add it to `PRESETS` in `backend/area.py` and run `build_preset.py --pbf <Geofabrik .osm.pbf>`. This takes about 2 minutes and runs offline except for elevation.

Live, on-demand areas anywhere (downloaded from Overpass, slow and flaky): set `SOLSTRIDE_ALLOW_LIVE=1`. `warm_cache.py --lat --lng --miles 1 3 5` pre-warms a live area.

## How routing works

### 1. Generate geographic candidates

The backend snaps the starting location to a node in a projected, undirected walk graph. It tries triangular loops (start → A → B → start) at eight bearings, rescaling each triangle up to three times to approach the requested distance.

NetworkX shortest-path searches combine edge length, sun preference, absolute grade and a penalty for reusing edges. Candidate loops are then scored on distance error, sun preference, elevation gain and overlap. This is a heuristic search; it does not guarantee the globally best loop.

### 2. Estimate sun and shade

`sun.py` calculates the current sun position. Buildings, trees, tree rows and wooded areas from OpenStreetMap supply simplified shadow geometry:

- Building height uses the mapped height if present, otherwise floor count × 3.2 m + 1 m, otherwise a 9 m default.
- Trees and tree rows use simplified crowns and heights. Wooded polygons are treated as shaded.
- A building's shadow is the convex hull of its footprint and the footprint shifted away from the sun by `height / tan(solar altitude)`, capped at 250 m.
- Only casters within 40 m of walkable edges are kept. Shadow intersections determine the shaded fraction of each edge.
- Results are cached in ten-minute buckets, and preset areas are refreshed in the background every 5 minutes. Elevation is interpolated from a grid, not surveyed at each sidewalk.

### 3. Match street photos to a scenery description

When a description is supplied:

1. Take the three best geographic candidates and sample five points on each, at about 10%, 30%, 50%, 70% and 90% of the loop's distance.
2. Fetch the nearest Mapillary photo for each point in parallel, falling back to Google Street View if a key is set. Points with no real photo are dropped; there are no placeholders. Lookups are cached by location.
3. Use CLIP, with embeddings cached per photo and per prompt, to compare each photo with the description and four benchmark descriptions: tree-lined residential street, park path, busy multilane road, and visible sidewalk.
4. Convert target cosine similarity to a clipped display score using `100 × (similarity − 0.15) / 0.20`.
5. Rerank using:

   `combined_cost = 0.3 × geographic_cost + 3.5 × (1 − average_match / 100)`

Lower cost wins, so scenery strongly influences the choice, while distance, sunlight, terrain and overlap still count.

Without a description, the geographic winner is returned immediately, and the page then fetches its five photos from `POST /photos`, so photos never delay the route.

### 4. Displaying the result

The backend returns route segments, statistics, an elevation profile, prominent streets, and either scored photos or photo sample points. The frontend draws the route on the map and fills in the panels.

## API

| Endpoint | Behavior |
| --- | --- |
| `GET /coverage` | Returns the available preset areas and whether live mode is enabled. |
| `GET /environment?lat=...&lng=...` | Returns weather, sun position, sunset, a suggested sun preference, notes and whether the start is covered. |
| `GET /route` | Requires `lat`, `lng` and `distance_mi` (above 0.2, at most 10). Optional: `sun=min` or `max`, `elevation=0..1`, `seed`, `description`. Returns 422 `out_of_coverage` outside the preset areas. |
| `POST /photos` | Accepts `{points, description?}` and returns `{vibe}` with real photos scored by CLIP, or `null` if none are found. |

A successful route includes `segments`, `stats`, `profile`, `streets`, `sun`, `vibe` (when a description was given), `photo_points` (otherwise), `degraded`, `candidates` and `elapsed_ms`. Photo records include coordinates, an image URL, a source, similarity scores and benchmark results.

## Project structure

```text
nova/
├── frontend/
│   ├── index.html       # Desktop page structure and controls
│   ├── styles.css       # Desktop UI styling
│   ├── app.js           # Leaflet/MapLibre map, photos, elevation profile
│   ├── mobile.html      # Mobile web version (Setup → Preview → Run → Summary)
│   ├── mobile.css
│   └── mobile.js        # Run tracking and "Simulate run"
├── mobile/              # Expo (React Native) version of the mobile flow
└── backend/
    ├── main.py          # FastAPI endpoints, frontend serving, preset and CLIP warm-up
    ├── area.py          # Presets, live map downloads, caching, elevation, shadows
    ├── router.py        # Loop generation and scoring
    ├── vibe.py          # Photo sampling and CLIP reranking
    ├── street_view.py   # Mapillary/Google photo lookup (parallel, cached)
    ├── clip_scorer.py   # CLIP loading and image–text similarity scores
    ├── environment.py   # Weather and sun preference suggestions
    ├── sun.py           # Solar-position calculations
    ├── build_preset.py  # Offline preset generation from an OSM extract
    ├── warm_cache.py    # Live-area cache preparation
    └── data/            # Bundled geographic preset
```

## Mobile web version (quickest to demo)

Open <http://localhost:8765/mobile.html>. On a laptop it shows in an iPhone-style frame with a status bar and location indicator. The flow is Setup → Preview → Run → Summary, with no scrolling. On the Run screen, **🛰 Simulate run** moves a fake GPS position along the loop at 20× speed, so you can demo the timer, pace and progress without moving. **Hold to finish** ends the run. On a real phone, open the same page over HTTPS (for example the Cloudflare tunnel below) to use real GPS, and keep the screen on while running.

## Mobile app (mobile/, Expo)

The same flow as a React Native app, using the same backend.

```bash
cloudflared tunnel --url http://127.0.0.1:8765        # prints https://<random>.trycloudflare.com
echo "EXPO_PUBLIC_API_BASE=https://<random>.trycloudflare.com" > mobile/.env
cd mobile && npm install && npx expo login && npx expo start   # scan the QR with the iPhone camera (Expo Go)
```

Expo Go needs the same Expo account signed in on the phone and in the CLI. The phone and laptop must be on the same Wi-Fi for Metro; if the venue blocks that, use `npx expo start --tunnel`. GPS with the screen locked needs a development build (`eas build --profile development`).

## Prototype limitations

- **Scenery scores are heuristic similarity scores, not probabilities or safety ratings.** Five samples can't establish the condition of an entire route.
- **Scoring failures have a fallback.** If CLIP scoring fails, the photos get neutral 50% scores.
- **Image location is approximate.** Mapillary returns the closest photo in a roughly 130 m search box. It isn't a verified, direction-matched photo of the exact route edge, and capture dates and contributor links aren't shown.
- **The 10 s route budget (`SOLSTRIDE_ROUTE_TIMEOUT`) covers routing and shade.** With a description, the first photo lookups in a new area add a few seconds; repeats are cached.
- **Durations use fixed paces:** 10 minutes per mile running and 20 minutes per mile walking.
- **Public deployment needs more work.** CORS is unrestricted, the desktop page inserts street names into HTML without escaping, and photo attribution and provenance aren't yet shown in the UI. Check provider requirements before distributing imagery.

## Data and model sources

- [OpenStreetMap](https://www.openstreetmap.org/copyright): walk network, building and vegetation features. Keep the attribution and follow the data and tile-service policies.
- [OpenFreeMap](https://openfreemap.org/): vector basemap tiles (OpenMapTiles schema).
- [Geofabrik](https://download.geofabrik.de/): OpenStreetMap extract used to build the Pittsburgh preset.
- [Open-Meteo](https://open-meteo.com/): weather and elevation inputs.
- [Mapillary](https://www.mapillary.com/): preferred street-level imagery provider. Keep contributor attribution and follow image-license requirements.
- [CLIP ViT-B/32](https://huggingface.co/openai/clip-vit-base-patch32): pretrained image–text matching. No project-specific model training is implemented.
