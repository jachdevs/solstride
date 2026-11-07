"""Loop generation: start -> A -> B -> start, scored on length, sun preference and hills."""

from __future__ import annotations

import math
import random
import time

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

M_PER_MI = 1609.344
DETOUR = 1.3  # street distance / straight-line distance, roughly


def nearest(G, xs, ys):
    """Nearest graph node(s) to projected coordinates, via a KD-tree cached on the graph."""
    if "_kd" not in G.graph:
        nodes = list(G.nodes)
        G.graph["_kd"] = (cKDTree([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes]), nodes)
    tree, nodes = G.graph["_kd"]
    if np.isscalar(xs):
        return nodes[tree.query((xs, ys))[1]]
    return [nodes[i] for i in tree.query(np.column_stack([xs, ys]))[1]]


def _edge_sun(frac: dict | None, u, v) -> float:
    if frac is None:
        return 0.5
    s = frac.get((u, v))
    return frac.get((v, u), 0.5) if s is None else s


def make_weight(frac: dict | None, sun: str, elevation: float, used: set):
    """Edge cost multiplier.

    sun: 'min' prefers shade, 'max' prefers sun.
    elevation: 0 = avoid grades, 1 = seek grades.
    used: edges already on the loop; reuse is penalised to avoid out-and-backs.
    """
    hill_bias = 1 - 2 * elevation  # +1 flat ... -1 hilly

    def w(u, v, d):
        s = _edge_sun(frac, u, v)
        sun_cost = 1 + 1.5 * (s if sun == "min" else 1 - s) if frac is not None else 1.0
        g = min(abs(d.get("grade", 0.0)), 0.12)
        hill_cost = max(0.25, 1 + hill_bias * g * 10)
        reuse = 6.0 if (u, v) in used or (v, u) in used else 1.0
        return d["length"] * sun_cost * hill_cost * reuse

    return w


def _offset(x, y, bearing_deg, dist):
    b = math.radians(bearing_deg)
    return x + dist * math.sin(b), y + dist * math.cos(b)


def _try_loop(G, start, sx, sy, bearing, r, weight_factory):
    """Route start -> A -> B -> start. Returns node path or None."""
    ax, ay = _offset(sx, sy, bearing, r)
    bx, by = _offset(sx, sy, bearing + 60, r)
    a, b = nearest(G, [ax, bx], [ay, by])
    if len({start, a, b}) < 3:
        return None
    used: set = set()
    path = [start]
    for src, dst in ((start, a), (a, b), (b, start)):
        try:
            _, leg = nx.bidirectional_dijkstra(G, src, dst, weight=weight_factory(used))
        except nx.NetworkXNoPath:
            return None
        used.update(zip(leg[:-1], leg[1:]))
        path.extend(leg[1:])
    return path


def _summarise(G, path, frac):
    length = sun_len = gain = 0.0
    seen: dict = {}
    for u, v in zip(path[:-1], path[1:]):
        d = G[u][v]
        L = d["length"]
        length += L
        sun_len += L * _edge_sun(frac, u, v)
        k = (min(u, v), max(u, v))
        seen[k] = seen.get(k, 0) + L
        dz = G.nodes[v].get("elev", 0) - G.nodes[u].get("elev", 0)
        if dz > 0:
            gain += dz
    unique = sum(G[u][v]["length"] for u, v in seen)
    overlap = 1 - unique / length if length else 0
    return {
        "length_m": length,
        "sun_frac": sun_len / length if length else 0,
        "gain_m": gain,
        "overlap": overlap,
    }


def generate_loop(G: nx.Graph, lat: float, lng: float, to_proj, distance_mi: float, sun: str,
                  elevation: float, frac: dict | None, deadline: float, seed: int | None = None,
                  to_ll=None, description: str | None = None):
    """Return (path, summary, candidates_tried, vibe_info). Respects `deadline` (time.time())."""
    rng = random.Random(seed)
    target = distance_mi * M_PER_MI
    sx, sy = to_proj.transform(lng, lat)
    start = nearest(G, sx, sy)
    sx, sy = G.nodes[start]["x"], G.nodes[start]["y"]

    def factory(used):
        return make_weight(frac, sun, elevation, used)

    base_bearing = rng.uniform(0, 360)
    bearings = [(base_bearing + i * 45) % 360 for i in range(8)]
    rng.shuffle(bearings)

    candidates = []
    tried = 0
    for bearing in bearings:
        if tried and time.time() > deadline:
            break
        r = target / (3 * DETOUR)
        path = summ = None
        for _ in range(3):  # rescale the triangle until the length is close
            tried += 1
            p = _try_loop(G, start, sx, sy, bearing, r, factory)
            if p is None:
                break
            path, summ = p, _summarise(G, p, frac)
            err = summ["length_m"] / target - 1
            if abs(err) < 0.08 or time.time() > deadline:
                break
            r *= target / summ["length_m"]
        if path is None:
            continue

        len_err = abs(summ["length_m"] / target - 1)
        sun_term = summ["sun_frac"] if sun == "min" else 1 - summ["sun_frac"]
        gain_per_km = summ["gain_m"] / (summ["length_m"] / 1000)
        hill_term = (gain_per_km / 40) * (1 - 2 * elevation)  # ~40 m/km is properly hilly
        score = 4 * len_err + 1.0 * sun_term + 0.5 * hill_term + 2 * summ["overlap"]
        candidates.append((score, path, summ))

    if not candidates:
        raise RuntimeError("Couldn't find a loop from here. Try another start point or distance.")

    candidates.sort(key=lambda c: c[0])

    # If description is provided and to_ll is available, evaluate candidates with Street View + CLIP
    best_vibe = None
    if description and description.strip() and to_ll:
        from street_view import sample_path_points, fetch_street_view
        from clip_scorer import score_images

        desc_text = description.strip()
        evaluated_candidates = []

        # Evaluate all good candidate loops
        for base_score, c_path, c_summ in candidates:
            pts = sample_path_points(G, c_path, to_ll, n_samples=5)
            images = []
            img_b64s = []
            is_reals = []
            for p in pts:
                img, b64_str, is_real = fetch_street_view(
                    p["lat"], p["lng"], p["heading"], p["street_name"], p["sample_idx"]
                )
                images.append(img)
                img_b64s.append(b64_str)
                is_reals.append(is_real)

            clip_evals = score_images(images, target_prompt=desc_text)
            
            # Calculate average match from REAL photos only, so synthetic fallback images do not dilute visual scores
            real_scores = [c_eval["target_score_pct"] for is_real, c_eval in zip(is_reals, clip_evals) if is_real]
            real_avg_match = sum(real_scores) / len(real_scores) if real_scores else None

            photos_data = []
            for p, b64_str, is_real, c_eval in zip(pts, img_b64s, is_reals, clip_evals):
                # If this image was a fallback, assign the average score of real photos on the loop
                effective_score = c_eval["target_score_pct"] if is_real else (real_avg_match if real_avg_match is not None else c_eval["target_score_pct"])
                photos_data.append({
                    "sample_idx": p["sample_idx"],
                    "u": p["u"],
                    "v": p["v"],
                    "lat": p["lat"],
                    "lng": p["lng"],
                    "heading": p["heading"],
                    "street_name": p["street_name"],
                    "image": b64_str,
                    "is_real": is_real,
                    "target_similarity": c_eval["target_similarity"],
                    "target_score_pct": round(effective_score, 1),
                    "benchmark_probs": c_eval["benchmark_probs"],
                    "top_benchmark": c_eval["top_benchmark"],
                })

            final_avg_match = real_avg_match if real_avg_match is not None else (
                sum(p["target_score_pct"] for p in photos_data) / max(len(photos_data), 1)
            )

            vibe_summary = {
                "description": desc_text,
                "avg_match_pct": round(final_avg_match, 1),
                "photos": photos_data,
            }
            # Visual alignment weight: strongly prioritize paths whose real street photos match the desired vibe
            clip_penalty = (100.0 - final_avg_match) / 100.0
            total_score = base_score * 0.3 + clip_penalty * 3.5
            evaluated_candidates.append((total_score, c_path, c_summ, vibe_summary))

        evaluated_candidates.sort(key=lambda x: x[0])
        best = evaluated_candidates[0]
        return best[1], best[2], tried, best[3]

    # If no description provided, but to_ll is given, still sample 5 photos for visualization
    if to_ll:
        try:
            from street_view import sample_path_points, fetch_street_view
            from clip_scorer import score_images

            c_score, c_path, c_summ = candidates[0]
            pts = sample_path_points(G, c_path, to_ll, n_samples=5)
            images, img_b64s, is_reals = [], [], []
            for p in pts:
                img, b64_str, is_real = fetch_street_view(p["lat"], p["lng"], p["heading"], p["street_name"], p["sample_idx"])
                images.append(img)
                img_b64s.append(b64_str)
                is_reals.append(is_real)
            clip_evals = score_images(images, target_prompt="A pedestrian path or road.")
            photos_data = []
            for p, b64_str, is_real, c_eval in zip(pts, img_b64s, is_reals, clip_evals):
                photos_data.append({
                    "sample_idx": p["sample_idx"],
                    "u": p["u"],
                    "v": p["v"],
                    "lat": p["lat"],
                    "lng": p["lng"],
                    "heading": p["heading"],
                    "street_name": p["street_name"],
                    "image": b64_str,
                    "is_real": is_real,
                    "target_similarity": c_eval["target_similarity"],
                    "target_score_pct": c_eval["target_score_pct"],
                    "benchmark_probs": c_eval["benchmark_probs"],
                    "top_benchmark": c_eval["top_benchmark"],
                })
            best_vibe = {
                "description": "",
                "avg_match_pct": round(sum(p["target_score_pct"] for p in photos_data) / max(len(photos_data), 1), 1),
                "photos": photos_data,
            }
        except Exception:
            best_vibe = None

    best = candidates[0]
    return best[1], best[2], tried, best_vibe
