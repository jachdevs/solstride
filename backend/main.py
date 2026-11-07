"""Nova API: /environment and /route. Also serves the frontend at /."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Literal

from fastapi import Body, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pyproj import Transformer

import area as areas
from describe import describe_route
from environment import get_environment
from router import M_PER_MI, generate_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("nova")

ROUTE_TIMEOUT_S = float(os.environ.get("NOVA_ROUTE_TIMEOUT", "10"))
ROUTING_RESERVE_S = 2.5  # time kept back for routing + serialising
M_TO_FT = 3.28084

app = FastAPI(title="Nova")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/environment")
def environment(lat: float = Query(ge=-90, le=90), lng: float = Query(ge=-180, le=180)):
    return get_environment(lat, lng)


@app.post("/describe")
def describe(payload: dict = Body(...)):
    """payload: {stats, streets, sun_mode, environment} -> {text}. Uses Claude Fable 5.1 via OpenRouter."""
    allowed = {k: payload.get(k) for k in ("stats", "streets", "sun_mode", "elevation_pref", "environment")}
    return {"text": describe_route(allowed)}


@app.get("/route")
def route(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    distance_mi: float = Query(gt=0.2, le=10),
    sun: Literal["min", "max"] = "min",
    elevation: float = Query(0.5, ge=0, le=1),
    seed: int | None = None,
):
    t0 = time.time()
    deadline = t0 + ROUTE_TIMEOUT_S
    radius = areas.radius_for_distance(distance_mi * M_PER_MI)
    area = areas.get_area(lat, lng, radius)

    def remaining():
        return max(0.05, deadline - ROUTING_RESERVE_S - time.time())

    # 1. The street graph is required. If it isn't ready, keep building in the
    #    background and tell the client to retry shortly.
    try:
        G = area.graph.result(timeout=remaining())
    except FutureTimeout:
        return JSONResponse(
            status_code=202,
            content={"status": "warming", "retry_after_s": 3,
                     "message": "Mapping the streets around you…"},
        )
    except Exception as e:
        log.exception("graph build failed")
        return JSONResponse(status_code=502, content={"status": "error", "message": f"Couldn't load map data: {e}"})

    # 2. Shade + elevation are best-effort within the deadline.
    for fut in (area.casters, area.elevation):
        try:
            fut.result(timeout=remaining())
        except Exception:
            pass

    degraded = []
    elev_ok = areas.apply_elevation(area)
    if not elev_ok:
        degraded.append("elevation")
    shade = areas.compute_sun_fraction(area)
    if shade is None:
        degraded.append("shade")
        frac, (alt, az) = None, areas.sun_position(area.lat, area.lng)
    else:
        frac, alt, az = shade

    # 3. Generate the loop with whatever time is left.
    crs = G.graph["crs"]
    to_proj = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    try:
        path, summ, tried = generate_loop(
            G, lat, lng, to_proj, distance_mi, sun, elevation, frac,
            deadline=max(deadline - 0.7, time.time() + 0.5), seed=seed,
        )
    except RuntimeError as e:
        return JSONResponse(status_code=422, content={"status": "error", "message": str(e)})

    return _serialise(G, path, summ, frac, to_ll, alt, az, elev_ok, degraded, tried, t0, lat, lng)


def _serialise(G, path, summ, frac, to_ll, alt, az, elev_ok, degraded, tried, t0, lat, lng):
    from router import _edge_sun

    segments = []
    profile = []
    street_len: dict[str, float] = {}
    cum = 0.0
    first = path[0]
    profile.append({"d_mi": 0.0, "elev_ft": round(G.nodes[first].get("elev", 0) * M_TO_FT, 1) if elev_ok else None})

    for u, v in zip(path[:-1], path[1:]):
        d = G[u][v]
        coords = list(d["geom"].coords)
        if d["a"] != u:
            coords.reverse()
        xs, ys = zip(*coords)
        lons, lats = to_ll.transform(xs, ys)
        pts = [[round(la, 6), round(lo, 6)] for la, lo in zip(lats, lons)]
        s = round(_edge_sun(frac, u, v), 2) if frac is not None else None
        # Merge with the previous segment when the sun bucket matches.
        if segments and s is not None and segments[-1]["sun"] is not None and abs(segments[-1]["sun"] - s) < 0.1:
            segments[-1]["coords"].extend(pts[1:])
        else:
            segments.append({"coords": pts, "sun": s})
        if d.get("name"):
            street_len[d["name"]] = street_len.get(d["name"], 0) + d["length"]
        cum += d["length"]
        profile.append({
            "d_mi": round(cum / M_PER_MI, 3),
            "elev_ft": round(G.nodes[v].get("elev", 0) * M_TO_FT, 1) if elev_ok else None,
            "sun": s,
        })

    dist_mi = summ["length_m"] / M_PER_MI
    sun_pct = round(summ["sun_frac"] * 100) if frac is not None else None
    return {
        "status": "ok",
        "start": {"lat": lat, "lng": lng},
        "segments": segments,
        "profile": profile,
        "stats": {
            "distance_mi": round(dist_mi, 2),
            "sun_pct": sun_pct,
            "shade_pct": None if sun_pct is None else 100 - sun_pct,
            "gain_ft": round(summ["gain_m"] * M_TO_FT) if elev_ok else None,
            "run_min": round(dist_mi * 10),
            "walk_min": round(dist_mi * 20),
            "overlap_pct": round(summ["overlap"] * 100),
        },
        "streets": [
            {"name": n, "mi": round(m / M_PER_MI, 2)}
            for n, m in sorted(street_len.items(), key=lambda kv: -kv[1])[:6]
        ],
        "sun": {"altitude": round(alt, 1), "azimuth": round(az, 1)},
        "degraded": degraded,
        "candidates": tried,
        "elapsed_ms": round((time.time() - t0) * 1000),
    }


FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
