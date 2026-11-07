"""On-demand area data: walk graph, shade casters (buildings/trees), elevation.

Each area is keyed by rounded lat/lng + radius bucket. The three downloads run in
parallel as futures so a request can use whatever finished before its deadline,
while slower pieces keep going in the background and land in the cache for the
next request. Finished areas are pickled to disk so a warmed cache survives restarts.
"""

from __future__ import annotations

import logging
import math
import os
import pickle
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
import networkx as nx
import numpy as np
import osmnx as ox
import shapely
from pyproj import Transformer
from scipy.interpolate import RegularGridInterpolator
from shapely.affinity import translate
from shapely.geometry import LineString, Point

from sun import sun_position

log = logging.getLogger("nova.area")
logging.getLogger("httpx").setLevel(logging.WARNING)

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)
ox.settings.use_cache = True
ox.settings.cache_folder = os.path.join(CACHE_DIR, "http")
# Server-side query timeout stays 90 s (it's part of the query text, so changing it would
# invalidate osmnx's on-disk HTTP cache); the client waits longer for slow mirrors.
ox.settings.overpass_settings = "[out:json][timeout:90]{maxsize}"
ox.settings.requests_timeout = 180
ox.settings.log_console = False

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api",
    "https://overpass.private.coffee/api",
    "https://overpass.kumi.systems/api",
]
RADIUS_BUCKET_M = 500
CASTER_NEAR_EDGE_M = 40  # only cast shadows from things this close to a walkable edge
DEFAULT_LEVEL_H = 3.2
DEFAULT_BUILDING_H = 9.0
TREE_H = 8.0
TREE_CROWN_R = 4.0
MAX_SHADOW_M = 250
ELEV_GRID_N = 14  # 196 points -> 2 API calls

_pool = ThreadPoolExecutor(max_workers=12)
_lock = threading.Lock()
_areas: dict[tuple, "Area"] = {}


# ---------------------------------------------------------------- data classes

@dataclass
class Caster:
    geom: shapely.Geometry  # projected footprint
    height: float           # metres; math.inf => always shaded (e.g. woods)


@dataclass
class Area:
    key: tuple
    lat: float
    lng: float
    radius: float
    graph: Future
    casters: Future
    elevation: Future
    created: float = field(default_factory=time.time)
    shade_cache: dict = field(default_factory=dict)  # time bucket -> {edge_key: sun_frac}
    elev_applied: bool = False


# ---------------------------------------------------------------- helpers

def radius_for_distance(distance_m: float) -> float:
    """Graph radius needed to fit a loop of the given length starting at the centre."""
    r = distance_m / 3.9 * 1.15 + 250
    return min(max(r, 600), 4500)


def _bucket(radius: float) -> int:
    return int(math.ceil(radius / RADIUS_BUCKET_M) * RADIUS_BUCKET_M)


def _disk_path(key: tuple) -> str:
    lat, lng, rad = key
    return os.path.join(CACHE_DIR, f"area_{lat:.3f}_{lng:.3f}_{rad}.pkl")


def _done(value) -> Future:
    f: Future = Future()
    f.set_result(value)
    return f


# ---------------------------------------------------------------- builders

def _overpass(fn, *args, **kwargs):
    """Call an osmnx downloader, falling back across Overpass mirrors on server errors."""
    last = None
    for url in OVERPASS_MIRRORS:
        ox.settings.overpass_url = url
        try:
            return fn(*args, **kwargs)
        except (ox._errors.ResponseStatusCodeError, ConnectionError, ValueError) as e:
            if isinstance(e, ox._errors.InsufficientResponseError):
                raise
            log.warning("overpass %s failed (%s), trying next mirror", url, str(e)[:120])
            last = e
        except Exception as e:  # requests errors (timeouts, bad JSON from a gateway page)
            if type(e).__module__.startswith("requests"):
                log.warning("overpass %s failed (%s), trying next mirror", url, str(e)[:120])
                last = e
            else:
                raise
    raise last


def _build_graph(lat: float, lng: float, radius: float) -> nx.Graph:
    t = time.time()
    G = _overpass(ox.graph_from_point, (lat, lng), dist=radius, network_type="walk", simplify=True)
    Gp = ox.project_graph(G)
    crs = Gp.graph["crs"]

    # Collapse to an undirected simple graph keeping the shortest parallel edge.
    U = nx.Graph(crs=crs)
    for n, d in Gp.nodes(data=True):
        U.add_node(n, x=d["x"], y=d["y"], lat=G.nodes[n]["y"], lon=G.nodes[n]["x"])
    for u, v, d in Gp.edges(data=True):
        if u == v:
            continue
        length = float(d.get("length", 0)) or 1.0
        if U.has_edge(u, v) and U[u][v]["length"] <= length:
            continue
        geom = d.get("geometry")
        if geom is None:
            geom = LineString([(Gp.nodes[u]["x"], Gp.nodes[u]["y"]), (Gp.nodes[v]["x"], Gp.nodes[v]["y"])])
        U.add_edge(u, v, length=length, geom=geom, a=u, sun=0.5, grade=0.0,
                   name=d.get("name") if isinstance(d.get("name"), str) else None)
    log.info("graph %.4f,%.4f r=%d: %d nodes, %d edges in %.1fs",
             lat, lng, radius, U.number_of_nodes(), U.number_of_edges(), time.time() - t)
    return U


def _height(row) -> float:
    def num(v):
        try:
            return float(str(v).split(";")[0].replace("m", "").strip())
        except (TypeError, ValueError):
            return None

    h = num(row.get("height"))
    if h and h > 0:
        return h
    lv = num(row.get("building:levels"))
    if lv and lv > 0:
        return lv * DEFAULT_LEVEL_H + 1
    return DEFAULT_BUILDING_H


def _build_casters(lat: float, lng: float, radius: float, graph_future: Future) -> list[Caster]:
    t = time.time()
    tags = {"building": True, "natural": ["tree", "tree_row", "wood"], "landuse": "forest"}
    try:
        gdf = _overpass(ox.features_from_point, (lat, lng), tags=tags, dist=radius)
    except ox._errors.InsufficientResponseError:
        return []  # genuinely nothing here (open country)
    # Any other error propagates so the future fails and get_area() retries it later,
    # instead of caching "no buildings" (which would mark every street as full sun).

    G: nx.Graph = graph_future.result()
    gdf = ox.projection.project_gdf(gdf, to_crs=G.graph["crs"])

    casters: list[Caster] = []
    cols = set(gdf.columns)
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        r = {c: row[c] for c in ("building", "natural", "landuse", "height", "building:levels") if c in cols}
        natural = r.get("natural") if isinstance(r.get("natural"), str) else None
        landuse = r.get("landuse") if isinstance(r.get("landuse"), str) else None
        building = r.get("building")
        if natural == "wood" or landuse == "forest":
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                casters.append(Caster(geom, math.inf))
        elif natural == "tree":
            casters.append(Caster(geom.centroid.buffer(TREE_CROWN_R, quad_segs=4), _height(r) if "height" in r else TREE_H))
        elif natural == "tree_row":
            casters.append(Caster(geom.buffer(TREE_CROWN_R / 1.5, quad_segs=2), TREE_H))
        elif isinstance(building, str) and geom.geom_type in ("Polygon", "MultiPolygon"):
            casters.append(Caster(geom, _height(r)))

    # Keep only casters near a walkable edge.
    if casters:
        edges = [d["geom"] for _, _, d in G.edges(data=True)]
        etree = shapely.STRtree(edges)
        geoms = np.array([c.geom for c in casters])
        hits = etree.query(geoms, predicate="dwithin", distance=CASTER_NEAR_EDGE_M)
        near = set(hits[0].tolist())
        casters = [c for i, c in enumerate(casters) if i in near]

    log.info("casters: %d near edges in %.1fs", len(casters), time.time() - t)
    return casters


def _build_elevation(lat: float, lng: float, radius: float):
    """Sample a coarse grid from Open-Meteo (90 m DEM) and return an interpolator."""
    t = time.time()
    dlat = radius / 111_320
    dlng = radius / (111_320 * math.cos(math.radians(lat)))
    lats = np.linspace(lat - dlat, lat + dlat, ELEV_GRID_N)
    lngs = np.linspace(lng - dlng, lng + dlng, ELEV_GRID_N)
    pts = [(a, b) for a in lats for b in lngs]

    def fetch(chunk):
        # Sequential with backoff: the free API rate-limits bursts (HTTP 429).
        for attempt in range(4):
            r = httpx.get(
                "https://api.open-meteo.com/v1/elevation",
                params={
                    "latitude": ",".join(f"{p[0]:.5f}" for p in chunk),
                    "longitude": ",".join(f"{p[1]:.5f}" for p in chunk),
                },
                timeout=15,
            )
            if r.status_code == 429 and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["elevation"]

    chunks = [pts[i:i + 100] for i in range(0, len(pts), 100)]
    vals = [v for chunk in chunks for v in fetch(chunk)]
    grid = np.array(vals, dtype=float).reshape(ELEV_GRID_N, ELEV_GRID_N)
    log.info("elevation grid in %.1fs (range %.0f–%.0f m)", time.time() - t, grid.min(), grid.max())
    return RegularGridInterpolator((lats, lngs), grid, bounds_error=False, fill_value=None)


# ---------------------------------------------------------------- area cache

def _persist_when_ready(area: Area) -> None:
    def _save(_):
        if not all(f.done() and f.exception() is None for f in (area.graph, area.casters, area.elevation)):
            return
        try:
            payload = {
                "lat": area.lat, "lng": area.lng, "radius": area.radius,
                "graph": area.graph.result(), "casters": area.casters.result(),
                "elevation": area.elevation.result(),
            }
            path = _disk_path(area.key)
            tmp = f"{path}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)  # atomic: readers never see a half-written file
            log.info("persisted area %s", area.key)
        except RuntimeError as e:  # graph mutated mid-pickle by a concurrent request; retry shortly
            log.info("persist deferred: %s", e)
            if os.path.exists(tmp):
                os.remove(tmp)
            threading.Timer(3, _save, args=(None,)).start()
        except Exception as e:
            log.warning("persist failed: %s", e)

    for f in (area.graph, area.casters, area.elevation):
        f.add_done_callback(_save)


def _failed(f: Future) -> bool:
    return f.done() and f.exception() is not None


def _retry_failed(area: Area) -> None:
    """Resubmit optional downloads that errored (e.g. a rate-limited elevation call)."""
    retried = False
    if _failed(area.casters):
        area.casters = _pool.submit(_build_casters, area.lat, area.lng, area.radius, area.graph)
        retried = True
    if _failed(area.elevation):
        area.elevation = _pool.submit(_build_elevation, area.lat, area.lng, area.radius)
        retried = True
    if retried:
        log.info("retrying failed downloads for %s", area.key)
        _persist_when_ready(area)


def get_area(lat: float, lng: float, radius: float) -> Area:
    """Return an Area covering (lat, lng, radius), starting downloads if needed."""
    rlat, rlng = round(lat, 3), round(lng, 3)
    need = _bucket(radius)
    with _lock:
        # Any in-memory area at this spot with a big enough radius will do.
        best = None
        for (klat, klng, krad), a in _areas.items():
            if klat == rlat and klng == rlng and krad >= need:
                if best is None or krad < best.key[2]:
                    best = a
        if best is not None:
            # Drop areas whose graph failed; retry failed shade/elevation downloads.
            if best.graph.done() and best.graph.exception() is not None:
                _areas.pop(best.key, None)
            else:
                _retry_failed(best)
                return best

        # Disk cache (warmed ahead of time).
        for krad in range(need, 4500 + RADIUS_BUCKET_M, RADIUS_BUCKET_M):
            key = (rlat, rlng, krad)
            path = _disk_path(key)
            if os.path.exists(path):
                try:
                    with open(path, "rb") as fh:
                        p = pickle.load(fh)
                    area = Area(key, p["lat"], p["lng"], p["radius"], _done(p["graph"]),
                                _done(p["casters"]), _done(p["elevation"]))
                    _areas[key] = area
                    log.info("loaded area %s from disk", key)
                    return area
                except Exception as e:
                    log.warning("bad cache file %s: %s", path, e)

        key = (rlat, rlng, need)
        # Build centred on the rounded point so the cache key matches its contents.
        g = _pool.submit(_build_graph, rlat, rlng, need)
        c = _pool.submit(_build_casters, rlat, rlng, need, g)
        e = _pool.submit(_build_elevation, rlat, rlng, need)
        area = Area(key, rlat, rlng, need, g, c, e)
        _areas[key] = area
        _persist_when_ready(area)
        return area


# ---------------------------------------------------------------- per-request layers

def apply_elevation(area: Area) -> bool:
    """Write node elevations and edge grades into the graph (once). Returns success."""
    if area.elev_applied:
        return True
    if not area.elevation.done() or area.elevation.exception() is not None:
        return False
    G: nx.Graph = area.graph.result()
    interp = area.elevation.result()
    nodes = list(G.nodes)
    pts = np.array([[G.nodes[n]["lat"], G.nodes[n]["lon"]] for n in nodes])
    elev = interp(pts)
    for n, z in zip(nodes, elev):
        G.nodes[n]["elev"] = float(z)
    for u, v, d in G.edges(data=True):
        d["grade"] = (G.nodes[v]["elev"] - G.nodes[u]["elev"]) / max(d["length"], 1.0)
    area.elev_applied = True
    return True


def compute_sun_fraction(area: Area, when=None) -> tuple[dict, float, float] | None:
    """Return ({(u,v): sun_fraction}, sun_alt, sun_az) for the current sun, cached per 10 min.

    sun_fraction is the share of an edge's length that is in direct sun.
    Returns None if shade casters aren't available yet.
    """
    alt, az = sun_position(area.lat, area.lng, when)
    bucket = int(time.time() // 600) if when is None else int(when.timestamp() // 600)
    if bucket in area.shade_cache:
        return area.shade_cache[bucket], alt, az

    G: nx.Graph = area.graph.result()
    if alt <= 0:
        frac = {(u, v): 0.0 for u, v in G.edges}
        area.shade_cache[bucket] = frac
        return frac, alt, az

    if not area.casters.done() or area.casters.exception() is not None:
        return None
    casters: list[Caster] = area.casters.result()

    t = time.time()
    tan_alt = math.tan(math.radians(alt))
    # Shadows fall away from the sun.
    ux, uy = -math.sin(math.radians(az)), -math.cos(math.radians(az))
    shadows = []
    for c in casters:
        if math.isinf(c.height):
            shadows.append(c.geom)
            continue
        L = min(c.height / tan_alt, MAX_SHADOW_M)
        moved = translate(c.geom, ux * L, uy * L)
        shadows.append(shapely.convex_hull(shapely.union(c.geom, moved)))

    frac: dict = {}
    edge_keys = list(G.edges)
    edge_geoms = [G.edges[k]["geom"] for k in edge_keys]
    if shadows:
        merged = shapely.union_all(shapely.make_valid(np.array(shadows)))
        parts = list(getattr(merged, "geoms", [merged]))
        parts = [p for p in parts if p.geom_type in ("Polygon", "MultiPolygon")]
        tree = shapely.STRtree(parts)
        e_idx, p_idx = tree.query(np.array(edge_geoms), predicate="intersects")
        shaded_len = np.zeros(len(edge_keys))
        if len(e_idx):
            inter = shapely.intersection(np.array(edge_geoms)[e_idx], np.array(parts)[p_idx])
            np.add.at(shaded_len, e_idx, shapely.length(inter))
        for i, k in enumerate(edge_keys):
            L = max(edge_geoms[i].length, 1e-6)
            frac[k] = float(max(0.0, 1.0 - shaded_len[i] / L))
    else:
        frac = {k: 1.0 for k in edge_keys}

    log.info("shade for %d edges / %d casters in %.2fs (alt %.1f az %.1f)",
             len(edge_keys), len(casters), time.time() - t, alt, az)
    area.shade_cache = {bucket: frac}  # keep only the current bucket
    return frac, alt, az


def to_latlng(area: Area):
    crs = area.graph.result().graph["crs"]
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
