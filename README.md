# Nova

**Loops that follow the light, or dodge it.**

Nova is a running and walking route prototype that generates loops near your starting point using distance, sun/shade preference, terrain, and a description of your preferred scenery with natural language. It combines geographic shadow estimates with street-level image matching to help choose between candidate routes.

## Features

- Start from browser geolocation, a pin dropped on the map, or coordinates supplied in the URL.
- Choose 1, 3, or 5 miles, or enter a custom distance.
- Prefer sun or shade and adjust a Flat–Hilly slider.
- See current weather, feels-like temperature, UV, sun direction and altitude, and time to sunset. Weather informs the suggested sun preference.
- Describe a route vibe, such as “A tree-lined residential street,” or select a scenery preset.
- Use pretrained CLIP to rerank candidate loops using sampled street photos.
- Outputs the optimal running route on a map interface with sun/shade route colors, numbered photo markers, distance, elevation gain, estimated duration, and an elevation profile.

## Run locally

Python 3.11 is a reasonable starting environment. Refer to `backend/requirements.txt` for dependencies.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend
python -m uvicorn main:app --port 8765
```

Open <http://localhost:8765>. 

The backend serves the frontend; there is no frontend build step. Geolocation works on localhost; deployment elsewhere generally requires HTTPS. If location permission is denied, click the map to choose a start.

For a separately hosted frontend, supply `?api=http://localhost:8765`. The current backend allows all CORS origins.

### Optional credentials

Create `.env` in the repository root to access real street imagery or generated summaries. Set preferred source of real street photos using:

```dotenv
MAPILLARY_CLIENT_TOKEN=your_mapillary_client_token
```

Keep `.env` out of version control. 

CLIP runs locally using `openai/clip-vit-base-patch32`. Its processor and weights download from Hugging Face on first use. The code does not explicitly select GPU or Apple MPS acceleration, so expect CPU inference by default. Photos, weather, map tiles, and the initial model download require network access.

## How routing works

### 1. Generate geographic candidates

The backend snaps the starting location to a node in a projected, undirected walk graph. It tries triangular loops—start → A → B → start—at eight bearings, rescaling each triangle up to three times to approach the requested distance.

NetworkX shortest-path searches combine edge length, sun preference, absolute grade, and a penalty for reusing edges. Candidate loops are then scored on distance error, sun preference, elevation gain, and overlap. This is heuristic candidate search, not a guarantee of the globally optimal loop.

### 2. Estimate sun and shade

`sun.py` calculates the current sun position. Buildings, trees, tree rows, and wooded areas from OpenStreetMap supply simplified shadow geometry:

- Building height uses a mapped height, otherwise floor count × 3.2 m + 1 m, otherwise a 9 m default.
- Trees and tree rows use simplified crowns and heights; wooded polygons are treated as shaded.
- A building's shadow is the convex hull of its footprint and the footprint shifted away from the sun by `height / tan(solar altitude)`, capped at 250 m.
- Only casters within 40 m of walkable edges are retained. Shadow intersections determine the shaded fraction of each edge.
- Results are cached in ten-minute buckets. Elevation is interpolated from a grid rather than surveyed at each sidewalk.

### 3. Match street photos to a scenery description

When a nonempty description is supplied:

1. Sample five locations at approximately 10%, 30%, 50%, 70%, and 90% of each candidate loop's distance.
2. Request imagery from Mapillary, then generate an illustrated placeholder if neither succeeds.
3. Use CLIP to compare each image with the user's description and four benchmark descriptions: tree-lined residential street, park path, busy multilane road, and visible sidewalk.
4. Convert target cosine similarity to a clipped display score using `100 × (similarity − 0.15) / 0.20`.
5. Average scores from real photos when available, then rerank using:

   `combined_cost = 0.3 × geographic_cost + 3.5 × (1 − average_match / 100)`

Lower cost wins. Scenery matching therefore strongly influences selection, while distance, sunlight, terrain, and overlap still contribute.

Without a description, the geographic winner is selected first. The backend still attempts to fetch and score five photos for its preview, without reranking the candidates.

### 4. Displaying Result Path

The backend returns route segments, statistics, an elevation profile, prominent streets, and optional photo scores. The frontend renders the result, then plots the resulting path onto the map.

## API

| Endpoint | Behavior |
| --- | --- |
| `GET /coverage` | Returns available preset areas and whether live mode is enabled. |
| `GET /environment?lat=...&lng=...` | Returns weather, sun position, sunset, a suggested sun preference, notes, and start coverage status. |
| `GET /route` | Requires `lat`, `lng`, and `distance_mi` (greater than 0.2 and at most 10). Optional: `sun=min` or `max`, `elevation=0..1`, `seed`, and `description`. |
| `POST /describe` | Accepts `stats`, `streets`, `sun_mode`, `elevation_pref`, and `environment`; returns `{text: ...}` or `{text: null}` when unavailable. |

A successful route includes `segments`, `stats`, `profile`, `streets`, `sun`, `vibe`, `degraded`, `candidates`, and `elapsed_ms`. Photo records include `is_real`, sample coordinates, a base64 image, similarity scores, and benchmark results.

## Project structure
```text
nova/
├── frontend/
│   ├── index.html       # Page structure and controls
│   ├── styles.css       # UI styling
│   └── app.js           # Leaflet map, photos, elevation profile, and summaries
└── backend/
    ├── main.py          # FastAPI endpoints, frontend serving, and preset warm-up
    ├── area.py          # Map downloads, caching, elevation, and shadows
    ├── router.py        # Loop generation, scoring, and scenery reranking
    ├── street_view.py   # Mapillary/Google imagery and placeholder fallbacks
    ├── clip_scorer.py   # CLIP loading and image–text similarity scores
    ├── environment.py   # Weather and sun preference suggestions
    ├── sun.py           # Solar-position calculations
    ├── describe.py      # Optional OpenRouter route summaries
    ├── build_preset.py  # Geographic preset generation
    ├── warm_cache.py    # Live-area cache preparation
    └── data/            # Bundled geographic preset
```
## Prototype limitations

- **Scenery scores are heuristic similarity scores, not probabilities or safety ratings.** Five samples cannot establish the condition of an entire route.
- **Scoring failures have a fallback.** Errors inside CLIP's scoring block return 50% scores; initial model-loading failures occur outside that block and can fail a description-based request.
- **Image location is approximate.** Mapillary returns a small bounding-box search result, not a verified nearest, direction-matched photo of the exact route edge. The response does not preserve capture dates or contributor/image links.
- **The time budget is partial.** `NOVA_ROUTE_TIMEOUT` defaults to 18 seconds, but synchronous photo downloads and CLIP evaluation are not bounded by that deadline. First-use model downloads and evaluation of multiple candidates can take much longer.
- **Durations use fixed paces:** 10 minutes per mile running and 20 minutes per mile walking.
- **Public deployment needs additional work.** CORS is unrestricted, and descriptions and street names are inserted into HTML without escaping. Photo attribution and provenance are not yet surfaced in the UI; check provider requirements before distributing imagery.

## Data and model sources

- [OpenStreetMap](https://www.openstreetmap.org/copyright): walk network, building and vegetation features, and frontend map tiles. Preserve attribution and follow the relevant data and tile-service policies.
- [Open-Meteo](https://open-meteo.com/): weather and elevation inputs.
- [Mapillary](https://www.mapillary.com/): preferred street-level imagery provider; preserve contributor attribution and applicable image-license requirements.
- [CLIP ViT-B/32](https://huggingface.co/openai/clip-vit-base-patch32): pretrained image–text matching; no project-specific model training is implemented.
