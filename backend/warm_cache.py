"""Warm the area cache for a location (run before the demo).

    python warm_cache.py --lat 40.7128 --lng -74.0060 --miles 1 3 5

Downloads streets, buildings/trees and elevation for each distance and pickles
them to backend/.cache so the first /route request is instant, even after a restart.
Warm the exact spot the demo will start from: the cache key is lat/lng rounded to 3 decimals (~100 m).
"""

import argparse
import time

import area as areas
from router import M_PER_MI


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lng", type=float, required=True)
    ap.add_argument("--miles", type=float, nargs="+", default=[1, 3, 5])
    args = ap.parse_args()

    # Largest radius first: smaller requests reuse a bigger cached area.
    for mi in sorted(args.miles, reverse=True):
        t = time.time()
        radius = areas.radius_for_distance(mi * M_PER_MI)
        a = areas.get_area(args.lat, args.lng, radius)
        G = a.graph.result()
        n_cast = len(a.casters.result())
        a.elevation.result()
        areas.apply_elevation(a)
        areas.compute_sun_fraction(a)
        print(f"{mi} mi -> area {a.key}: {G.number_of_nodes()} nodes, {n_cast} shade casters ({time.time() - t:.1f}s)")
    time.sleep(1)  # let the persist callbacks finish writing
    print("Cache warm.")


if __name__ == "__main__":
    main()
