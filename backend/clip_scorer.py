"""CLIP image/text alignment for street photos (Hugging Face transformers).

Speed-ups: the model is preloaded at server start (warm_up), image embeddings are
cached by photo id, text embeddings by prompt, and all images are embedded in one batch.
"""

from __future__ import annotations

import logging
import threading
from typing import Sequence

import torch
from PIL import Image

log = logging.getLogger("nova.clip")

DEFAULT_BENCHMARK_PROMPTS = [
    "A tree-lined residential street.",
    "A pedestrian path through a park.",
    "A busy multilane road.",
    "A street with a clearly visible sidewalk.",
]
MODEL_ID = "openai/clip-vit-base-patch32"

_model = None
_processor = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()
_img_cache: dict[str, torch.Tensor] = {}
_txt_cache: dict[str, torch.Tensor] = {}


def get_clip():
    global _model, _processor
    with _load_lock:
        if _model is None:
            log.info("Loading CLIP model: %s", MODEL_ID)
            from transformers import CLIPModel, CLIPProcessor

            _processor = CLIPProcessor.from_pretrained(MODEL_ID)
            _model = CLIPModel.from_pretrained(MODEL_ID).eval()
    return _processor, _model


def warm_up() -> None:
    """Load weights and cache the benchmark prompt embeddings (call in a background thread)."""
    try:
        _text_embeds(DEFAULT_BENCHMARK_PROMPTS)
        log.info("CLIP ready")
    except Exception as e:
        log.warning("CLIP warm-up failed: %s", e)


def _as_tensor(out) -> torch.Tensor:
    """transformers 4 returns a tensor from get_*_features; 5 returns an output object."""
    return out if isinstance(out, torch.Tensor) else out.pooler_output


def _text_embeds(texts: Sequence[str]) -> torch.Tensor:
    missing = [t for t in texts if t not in _txt_cache]
    if missing:
        processor, model = get_clip()
        with _infer_lock, torch.no_grad():
            inp = processor(text=missing, return_tensors="pt", padding=True)
            emb = _as_tensor(model.get_text_features(**inp))
            emb = emb / emb.norm(dim=-1, keepdim=True)
        for t, e in zip(missing, emb):
            _txt_cache[t] = e
    return torch.stack([_txt_cache[t] for t in texts])


def _image_embeds(keys: Sequence[str], images: Sequence[Image.Image]) -> torch.Tensor:
    missing = [(k, im) for k, im in zip(keys, images) if k not in _img_cache]
    if missing:
        processor, model = get_clip()
        with _infer_lock, torch.no_grad():
            inp = processor(images=[im for _, im in missing], return_tensors="pt")
            emb = _as_tensor(model.get_image_features(**inp))
            emb = emb / emb.norm(dim=-1, keepdim=True)
        for (k, _), e in zip(missing, emb):
            _img_cache[k] = e
    return torch.stack([_img_cache[k] for k in keys])


def score_images(
    images: Sequence[Image.Image],
    target_prompt: str,
    benchmark_prompts: Sequence[str] | None = None,
    keys: Sequence[str] | None = None,
) -> list[dict]:
    """Score images against target_prompt and a set of benchmark descriptions.

    Each result: target_similarity (cosine), target_score_pct (0-100, scaled),
    benchmark_probs (softmax %, per benchmark), top_benchmark.
    `keys` (e.g. photo ids) enable embedding caching across requests.
    """
    if not images:
        return []
    benchmarks = list(benchmark_prompts or DEFAULT_BENCHMARK_PROMPTS)
    target = (target_prompt or "").strip() or benchmarks[0]
    keys = list(keys) if keys else [f"anon:{id(im)}" for im in images]

    try:
        _, model = get_clip()
        img = _image_embeds(keys, images)
        txt = _text_embeds(benchmarks + [target])
        sims = img @ txt.T  # cosine similarities [n_images, n_benchmarks + 1]
        probs = (sims[:, : len(benchmarks)] * model.logit_scale.exp()).softmax(dim=-1)
    except Exception as e:
        log.warning("CLIP scoring failed: %s", e)
        return [{"target_similarity": 0.25, "target_score_pct": 50.0,
                 "benchmark_probs": {b: 25.0 for b in benchmarks}, "top_benchmark": benchmarks[0]}
                for _ in images]
    finally:
        for k in keys:  # don't let anonymous (uncacheable) keys pile up
            if k.startswith("anon:"):
                _img_cache.pop(k, None)

    results = []
    for i in range(len(images)):
        sim = float(sims[i, -1])
        # CLIP cosine similarity typically ranges 0.15–0.35; scale to 0–100%.
        pct = max(0.0, min(100.0, (sim - 0.15) / 0.20 * 100.0))
        b_probs = {b: round(float(probs[i, j]) * 100, 1) for j, b in enumerate(benchmarks)}
        results.append({
            "target_similarity": round(sim, 4),
            "target_score_pct": round(pct, 1),
            "benchmark_probs": b_probs,
            "top_benchmark": max(b_probs.items(), key=lambda kv: kv[1])[0],
        })
    return results
