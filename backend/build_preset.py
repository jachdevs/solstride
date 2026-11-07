"""Build a preset area offline from an OpenStreetMap .pbf extract (no Overpass).

    curl -L -o pennsylvania.osm.pbf https://download.geofabrik.de/north-america/us/pennsylvania-latest.osm.pbf
    python build_preset.py --pbf pennsylvania.osm.pbf                 # builds every preset in area.PRESETS
    python build_preset.py --pbf pennsylvania.osm.pbf --name pittsburgh-oakland

Writes backend/data/<name>.pkl.gz (graph + shade casters + elevation), which is committed
so teammates and the demo machine get routes instantly without any map downloads.
Only the elevation grid needs the network (Open-Meteo, a few small requests).
"""

from __future__ import annotations

import argparse
import math
import os
import tempfile
import time

import osmium
import osmnx as ox

import area as areas

# Mirrors osmnx's "walk" network filter.
_BAD_HIGHWAY = ("abandoned", "bus_guideway", "construction", "cycleway", "motor", "no",
                "planned", "platform", "proposed", "raceway", "razed")


def _walkable(tags) -> bool:
    hw = tags.get("highway")
    if not hw or tags.get("area") == "yes":
        return False
    if any(b in hw for b in _BAD_HIGHWAY):
        return False
    if tags.get("foot") == "no" or tags.get("service") == "private":
        return False
    if tags.get("access") in ("private", "no") and tags.get("foot") not in ("yes", "designated", "permissive"):
        return False
    return True


def _is_caster(tags) -> bool:
    return ("building" in tags
            or tags.get("natural") in ("tree_row", "wood")
            or tags.get("landuse") == "forest")


def crop(pbf: str, lat: float, lng: float, radius: float, walk_out: str, feat_out: str) -> None:
    pad = radius + 300
    dlat = pad / 111_320
    dlng = pad / (111_320 * math.cos(math.radians(lat)))
    s, n, w, e = lat - dlat, lat + dlat, lng - dlng, lng + dlng

    t = time.time()
    inside: set[int] = set()
    trees = []
    for node in osmium.FileProcessor(pbf, osmium.osm.NODE):
        loc = node.location
        if loc.valid() and s <= loc.lat <= n and w <= loc.lon <= e:
            inside.add(node.id)
            if node.tags.get("natural") == "tree":
                trees.append(node.id)
    print(f"  pass 1: {len(inside):,} nodes in bbox ({time.time() - t:.0f}s)")

    t = time.time()
    tree_ids = set(trees)
    n_walk = n_feat = 0
    with osmium.BackReferenceWriter(walk_out, ref_src=pbf, overwrite=True) as walk_w, \
         osmium.BackReferenceWriter(feat_out, ref_src=pbf, overwrite=True) as feat_w:
        for obj in osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY):
            if obj.is_node():
                if obj.id in tree_ids:
                    feat_w.add_node(obj)
                continue
            if not any(nd.ref in inside for nd in obj.nodes):
                continue
            if _walkable(obj.tags):
                walk_w.add_way(obj)
                n_walk += 1
            elif _is_caster(obj.tags):
                feat_w.add_way(obj)
                n_feat += 1
    print(f"  pass 2: {n_walk:,} walkable ways, {n_feat:,} shade ways, {len(trees):,} trees "
          f"({time.time() - t:.0f}s)")


def build(pbf: str, preset: dict) -> None:
    name, lat, lng, radius = preset["name"], preset["lat"], preset["lng"], preset["radius"]
    print(f"{name}: {lat}, {lng}, r={radius} m")
    with tempfile.TemporaryDirectory() as tmp:
        walk_osm = os.path.join(tmp, "walk.osm")
        feat_osm = os.path.join(tmp, "features.osm")
        crop(pbf, lat, lng, radius, walk_osm, feat_osm)

        t = time.time()
        G = ox.graph_from_xml(walk_osm, bidirectional=True, simplify=True, retain_all=False)
        U = areas.to_routing_graph(G)
        print(f"  graph: {U.number_of_nodes():,} nodes, {U.number_of_edges():,} edges ({time.time() - t:.0f}s)")

        t = time.time()
        gdf = ox.features_from_xml(feat_osm, tags=areas.CASTER_TAGS)
        casters = areas.casters_from_gdf(gdf, U)
        print(f"  shade casters near streets: {len(casters):,} ({time.time() - t:.0f}s)")

    t = time.time()
    n = 20  # 400 points -> 4 API calls; ~350 m spacing over a 7 km box
    elevation = areas.build_elevation(lat, lng, radius, n=n)
    print(f"  elevation: {n}x{n} grid ({time.time() - t:.0f}s)")

    path = areas.save_preset(name, {"graph": U, "casters": casters, "elevation": elevation})
    print(f"  saved {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--name", help="preset name (default: all presets)")
    args = ap.parse_args()
    presets = [p for p in areas.PRESETS if args.name in (None, p["name"])]
    if not presets:
        raise SystemExit(f"unknown preset {args.name!r}")
    for p in presets:
        build(args.pbf, p)


if __name__ == "__main__":
    main()
