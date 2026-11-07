"""Street View client supporting Mapillary API v4 (and Google Maps as secondary).

Queries geolocated street-level photos around coordinates using Mapillary's graph API,
extracts thumbnails for CLIP scoring, and provides clean fallbacks.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
from typing import Sequence
import httpx
from PIL import Image, ImageDraw

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

MAPILLARY_CLIENT_TOKEN = os.environ.get("MAPILLARY_CLIENT_TOKEN") or os.environ.get("MAPILLARY_ACCESS_TOKEN")
MAPILLARY_API_URL = "https://graph.mapillary.com/images"

GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GOOGLE_STREET_VIEW_URL = "https://maps.googleapis.com/maps/api/streetview"


def sample_path_points(G, path: Sequence, to_ll, n_samples: int = 5) -> list[dict]:
    """Sample n_samples evenly distributed points along a node path.

    Returns a list of dicts with:
      - sample_idx: 0 to n_samples-1
      - u, v: edge node IDs
      - lat, lng: coordinates of the sample point
      - heading: bearing along the road segment (degrees 0-360)
      - street_name: name of the road
    """
    if len(path) < 2:
        return []

    # Calculate cumulative distance along edges
    edges = []
    total_len = 0.0
    for u, v in zip(path[:-1], path[1:]):
        edge_data = G[u][v]
        length = float(edge_data.get("length", 1.0))
        edges.append({
            "u": u,
            "v": v,
            "length": length,
            "cum_start": total_len,
            "cum_end": total_len + length,
            "geom": edge_data.get("geom"),
            "name": edge_data.get("name") if isinstance(edge_data.get("name"), str) else "Unnamed road",
        })
        total_len += length

    if total_len <= 0:
        return []

    samples = []
    # Scattered fractions: e.g. for n=5 -> 10%, 30%, 50%, 70%, 90%
    fractions = [(i + 0.5) / n_samples for i in range(n_samples)]

    for idx, frac in enumerate(fractions):
        target_dist = frac * total_len
        # Find matching edge
        matched = edges[-1]
        for e in edges:
            if e["cum_start"] <= target_dist <= e["cum_end"]:
                matched = e
                break

        u, v = matched["u"], matched["v"]
        u_node = G.nodes[u]
        v_node = G.nodes[v]

        edge_len = max(matched["length"], 1.0)
        t = max(0.0, min(1.0, (target_dist - matched["cum_start"]) / edge_len))

        # Linear interpolation in projected coordinates
        px = u_node["x"] + t * (v_node["x"] - u_node["x"])
        py = u_node["y"] + t * (v_node["y"] - u_node["y"])

        # Bearing from u to v
        dx = v_node["x"] - u_node["x"]
        dy = v_node["y"] - u_node["y"]
        bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360

        lon, lat = to_ll.transform(px, py)
        samples.append({
            "sample_idx": idx,
            "u": u,
            "v": v,
            "lat": round(float(lat), 6),
            "lng": round(float(lon), 6),
            "heading": round(float(bearing), 1),
            "street_name": matched["name"],
        })

    return samples


def fetch_mapillary_image(lat: float, lng: float, token: str) -> tuple[Image.Image, str] | None:
    """Fetch closest street image from Mapillary within a small bounding box."""
    # Search up to ~150m around target lat/lng
    for delta in (0.0008, 0.0016):
        bbox = f"{lng - delta:.6f},{lat - delta:.6f},{lng + delta:.6f},{lat + delta:.6f}"
        headers = {"Authorization": f"OAuth {token.strip()}"}
        params = {
            "bbox": bbox,
            "fields": "id,thumb_1024_url,thumb_2048_url,compass_angle",
            "limit": 2,
        }
        try:
            r = httpx.get(MAPILLARY_API_URL, headers=headers, params=params, timeout=5.0)
            if r.status_code == 200:
                data = r.json().get("data", [])
                for item in data:
                    img_url = item.get("thumb_1024_url") or item.get("thumb_2048_url")
                    if img_url:
                        img_resp = httpx.get(img_url, timeout=6.0)
                        if img_resp.status_code == 200:
                            img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                            b64 = "data:image/jpeg;base64," + base64.b64encode(img_resp.content).decode("ascii")
                            return img, b64
        except Exception as e:
            log.warning("Mapillary fetch error at (%f, %f): %s", lat, lng, e)
    return None


def fetch_google_image(lat: float, lng: float, heading: float, key: str) -> tuple[Image.Image, str] | None:
    """Fetch Google Street View photo."""
    try:
        params = {
            "size": "400x300",
            "location": f"{lat:.6f},{lng:.6f}",
            "heading": f"{heading:.1f}",
            "fov": "90",
            "pitch": "0",
            "source": "outdoor",
            "key": key.strip(),
        }
        resp = httpx.get(GOOGLE_STREET_VIEW_URL, params=params, timeout=8.0)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            b64 = "data:image/jpeg;base64," + base64.b64encode(resp.content).decode("ascii")
            return img, b64
    except Exception as e:
        log.warning("Google Street View fetch error: %s", e)
    return None


def _make_fallback_image(lat: float, lng: float, street_name: str, sample_idx: int) -> Image.Image:
    """Generate a clean synthetic street placeholder if API is offline or not configured."""
    img = Image.new("RGB", (400, 300), color=(240, 244, 248))
    draw = ImageDraw.Draw(img)

    # Stylized sky and scenery
    draw.rectangle([0, 0, 400, 140], fill=(185, 215, 245))
    draw.ellipse([-50, 100, 200, 200], fill=(95, 145, 90))
    draw.ellipse([150, 90, 450, 210], fill=(85, 135, 80))
    # Road / walkway
    draw.polygon([(160, 140), (240, 140), (380, 300), (20, 300)], fill=(120, 125, 130))
    draw.line([(200, 140), (200, 300)], fill=(255, 255, 255), width=2)
    # Tree crowns
    draw.ellipse([20, 70, 90, 150], fill=(60, 120, 65))
    draw.ellipse([310, 65, 380, 145], fill=(60, 120, 65))

    # Badge overlay
    draw.rectangle([10, 10, 390, 50], fill=(20, 25, 35, 210))
    draw.text((20, 15), f"Point #{sample_idx + 1}: {street_name[:30]}", fill=(255, 255, 255))
    draw.text((20, 32), f"Lat {lat:.4f}, Lng {lng:.4f} (Mapillary Stand-in)", fill=(200, 220, 240))

    return img


def fetch_street_view(lat: float, lng: float, heading: float = 0, street_name: str = "", sample_idx: int = 0) -> tuple[Image.Image, str, bool]:
    """Fetch street photo for a lat/lng using Mapillary first, then Google, then fallback.

    Returns:
      (PIL.Image, base64_data_url, is_real)
    """
    mapillary_token = os.environ.get("MAPILLARY_CLIENT_TOKEN") or os.environ.get("MAPILLARY_ACCESS_TOKEN")
    if mapillary_token and mapillary_token.strip():
        res = fetch_mapillary_image(lat, lng, mapillary_token)
        if res is not None:
            return res[0], res[1], True

    google_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if google_key and google_key.strip():
        res = fetch_google_image(lat, lng, heading, google_key)
        if res is not None:
            return res[0], res[1], True

    # Fallback image
    img = _make_fallback_image(lat, lng, street_name, sample_idx)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return img, b64, False
