"""Street-level photos along a route: Mapillary API v4 first, Google Street View second.

Fast path design:
  * One shared HTTP client (connection reuse) and parallel lookups.
  * Small 256 px thumbnails are downloaded for CLIP (it works at 224 px anyway);
    the UI gets Mapillary's 1024 px URL to load directly, so no base64 in the JSON.
  * Lookups are cached by location (~10 m), so repeat routes in an area are instant.
  * No synthetic placeholders: a point with no real photo is simply dropped.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

import httpx
from PIL import Image

log = logging.getLogger("nova.streetview")


def _load_env() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

MAPILLARY_API_URL = "https://graph.mapillary.com/images"
GOOGLE_STREET_VIEW_URL = "https://maps.googleapis.com/maps/api/streetview"
SEARCH_DELTA_DEG = 0.0012  # ~130 m box around the sample point

_client = httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0),
                       limits=httpx.Limits(max_connections=32, max_keepalive_connections=32))
_pool = ThreadPoolExecutor(max_workers=16)
_cache: dict[tuple, dict | None] = {}
_cache_lock = threading.Lock()


def _mapillary_token() -> str | None:
    t = os.environ.get("MAPILLARY_CLIENT_TOKEN") or os.environ.get("MAPILLARY_ACCESS_TOKEN")
    return t.strip() if t and t.strip() else None


def _google_key() -> str | None:
    k = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return k.strip() if k and k.strip() else None


def enabled() -> bool:
    return bool(_mapillary_token() or _google_key())


def sample_path_points(G, path: Sequence, to_ll, n_samples: int = 5) -> list[dict]:
    """Evenly spaced points along a node path (10%, 30%, ... for n=5).

    Each point: sample_idx, u, v (edge), lat, lng, heading (deg), street_name.
    """
    if len(path) < 2:
        return []

    edges = []
    total_len = 0.0
    for u, v in zip(path[:-1], path[1:]):
        d = G[u][v]
        length = float(d.get("length", 1.0))
        edges.append({
            "u": u, "v": v, "length": length,
            "cum_start": total_len, "cum_end": total_len + length,
            "name": d.get("name") if isinstance(d.get("name"), str) else "Unnamed road",
        })
        total_len += length
    if total_len <= 0:
        return []

    samples = []
    for idx in range(n_samples):
        target = (idx + 0.5) / n_samples * total_len
        e = next((e for e in edges if e["cum_start"] <= target <= e["cum_end"]), edges[-1])
        un, vn = G.nodes[e["u"]], G.nodes[e["v"]]
        t = max(0.0, min(1.0, (target - e["cum_start"]) / max(e["length"], 1.0)))
        px = un["x"] + t * (vn["x"] - un["x"])
        py = un["y"] + t * (vn["y"] - un["y"])
        heading = (math.degrees(math.atan2(vn["x"] - un["x"], vn["y"] - un["y"])) + 360) % 360
        lon, lat = to_ll.transform(px, py)
        samples.append({
            "sample_idx": idx, "u": e["u"], "v": e["v"],
            "lat": round(float(lat), 6), "lng": round(float(lon), 6),
            "heading": round(float(heading), 1), "street_name": e["name"],
        })
    return samples


def _fetch_mapillary(lat: float, lng: float, token: str) -> dict | None:
    d = SEARCH_DELTA_DEG
    params = {
        "bbox": f"{lng - d:.6f},{lat - d:.6f},{lng + d:.6f},{lat + d:.6f}",
        "fields": "id,thumb_256_url,thumb_1024_url,computed_geometry,geometry",
        "limit": 10,
    }
    r = _client.get(MAPILLARY_API_URL, headers={"Authorization": f"OAuth {token}"}, params=params)
    if r.status_code != 200:
        log.warning("Mapillary %s at (%.5f, %.5f)", r.status_code, lat, lng)
        return None
    items = [i for i in r.json().get("data", []) if i.get("thumb_256_url")]
    if not items:
        return None

    def dist2(i):
        g = (i.get("computed_geometry") or i.get("geometry") or {}).get("coordinates")
        return (g[0] - lng) ** 2 + (g[1] - lat) ** 2 if g else 1.0

    best = min(items, key=dist2)
    img = _client.get(best["thumb_256_url"])
    if img.status_code != 200:
        return None
    return {
        "photo_id": f"mly:{best['id']}",
        "image_url": best.get("thumb_1024_url") or best["thumb_256_url"],
        "pil": Image.open(io.BytesIO(img.content)).convert("RGB"),
        "source": "mapillary",
    }


def _fetch_google(lat: float, lng: float, heading: float, key: str) -> dict | None:
    params = {"size": "400x300", "location": f"{lat:.6f},{lng:.6f}", "heading": f"{heading:.1f}",
              "fov": "90", "pitch": "0", "source": "outdoor", "key": key}
    r = _client.get(GOOGLE_STREET_VIEW_URL, params=params)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        return None
    # The URL contains the API key, so inline the bytes instead of exposing it.
    return {
        "photo_id": f"gsv:{lat:.5f},{lng:.5f},{heading:.0f}",
        "image_url": "data:image/jpeg;base64," + base64.b64encode(r.content).decode("ascii"),
        "pil": Image.open(io.BytesIO(r.content)).convert("RGB"),
        "source": "google",
    }


def find_photo(lat: float, lng: float, heading: float = 0.0) -> dict | None:
    """Closest real street photo near a point, or None. Cached by ~10 m cell."""
    key = (round(lat, 4), round(lng, 4))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    res = None
    try:
        tok = _mapillary_token()
        if tok:
            res = _fetch_mapillary(lat, lng, tok)
        if res is None and _google_key():
            res = _fetch_google(lat, lng, heading, _google_key())
    except Exception as e:
        log.warning("street photo lookup failed at (%.5f, %.5f): %s", lat, lng, e)
        return None  # don't cache transient errors
    with _cache_lock:
        _cache[key] = res
    return res


def find_photos(points: Sequence[dict]) -> list[dict | None]:
    """Parallel find_photo for many points, preserving order."""
    return list(_pool.map(lambda p: find_photo(p["lat"], p["lng"], p.get("heading", 0.0)), points))
