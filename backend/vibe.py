"""Route 'vibe': real street photos along a loop, scored with CLIP against a description.

Used two ways:
  * /photos (no description): called by the frontend after the route renders, so
    photos never delay the route itself.
  * /route with a description: re-ranks the top few candidate loops by how well their
    photos match, fetching every candidate's photos in parallel.
Points without a real photo are dropped (no placeholders).
"""

from __future__ import annotations

import logging
import time

import street_view
from clip_scorer import score_images

log = logging.getLogger("solstride.vibe")

N_SAMPLES = 5
RERANK_TOP_K = 3
DEFAULT_PROMPT = "A pedestrian path or road."


def _vibe_from(points: list[dict], photos: list[dict | None], description: str) -> dict | None:
    pairs = [(p, ph) for p, ph in zip(points, photos) if ph is not None]
    if not pairs:
        return None
    evals = score_images([ph["pil"] for _, ph in pairs], target_prompt=description or DEFAULT_PROMPT,
                         keys=[ph["photo_id"] for _, ph in pairs])
    out = []
    for (p, ph), ev in zip(pairs, evals):
        out.append({
            **{k: p[k] for k in ("sample_idx", "u", "v", "lat", "lng", "heading", "street_name")},
            "image": ph["image_url"],
            "source": ph["source"],
            "is_real": True,
            **ev,
        })
    return {
        "description": description,
        "avg_match_pct": round(sum(o["target_score_pct"] for o in out) / len(out), 1),
        "photos": out,
    }


def photos_for_points(points: list[dict], description: str = "") -> dict | None:
    t = time.time()
    vibe = _vibe_from(points, street_view.find_photos(points), description.strip())
    log.info("photos: %d/%d real in %.2fs", len(vibe["photos"]) if vibe else 0, len(points), time.time() - t)
    return vibe


def rerank(G, to_ll, candidates: list[tuple], description: str):
    """candidates: [(score, path, summary)] sorted best-first. Returns (path, summary, vibe)."""
    t = time.time()
    top = candidates[:RERANK_TOP_K]
    pts_per = [street_view.sample_path_points(G, path, to_ll, N_SAMPLES) for _, path, _ in top]
    flat = [p for pts in pts_per for p in pts]
    photos = street_view.find_photos(flat)  # one parallel batch for all candidates

    best = None
    i = 0
    for (base, path, summ), pts in zip(top, pts_per):
        vibe = _vibe_from(pts, photos[i:i + len(pts)], description)
        i += len(pts)
        match = vibe["avg_match_pct"] if vibe else 50.0  # no photos: neutral
        total = base * 0.3 + (100.0 - match) / 100.0 * 3.5
        if best is None or total < best[0]:
            best = (total, path, summ, vibe)
    log.info("rerank %d candidates by '%s' in %.2fs", len(top), description[:40], time.time() - t)
    return best[1], best[2], best[3]
