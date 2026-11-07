"""CLIP image and text alignment scorer using Hugging Face transformers.

Compares geolocated street-view photos against route vibe descriptions such as:
- 'A tree-lined residential street.'
- 'A pedestrian path through a park.'
- 'A busy multilane road.'
- 'A street with a clearly visible sidewalk.'
"""

from __future__ import annotations

import logging
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

_model = None
_processor = None
MODEL_ID = "openai/clip-vit-base-patch32"


def get_clip():
    """Lazy load CLIP processor and model."""
    global _model, _processor
    if _model is None or _processor is None:
        log.info("Loading CLIP model: %s", MODEL_ID)
        from transformers import CLIPModel, CLIPProcessor

        _processor = CLIPProcessor.from_pretrained(MODEL_ID)
        _model = CLIPModel.from_pretrained(MODEL_ID)
        _model.eval()
    return _processor, _model


def score_images(
    images: Sequence[Image.Image],
    target_prompt: str,
    benchmark_prompts: Sequence[str] | None = None,
) -> list[dict]:
    """Score a sequence of PIL images against the target prompt and benchmark categories.

    Returns a list of dicts with:
      - target_similarity: cosine similarity with target_prompt (0.0 to 1.0 approx)
      - target_score_pct: scaled match percentage (0 to 100)
      - benchmark_probs: softmax distribution over the benchmark prompts
      - top_benchmark: highest probability benchmark description
    """
    if not images:
        return []

    processor, model = get_clip()
    benchmarks = list(benchmark_prompts or DEFAULT_BENCHMARK_PROMPTS)

    # Collect unique text prompts to evaluate
    all_texts = list(benchmarks)
    target_idx = None
    if target_prompt and target_prompt.strip():
        norm_target = target_prompt.strip()
        if norm_target not in all_texts:
            all_texts.append(norm_target)
        target_idx = all_texts.index(norm_target)
    else:
        target_idx = 0

    results = []
    try:
        inputs = processor(text=all_texts, images=list(images), return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            # Normalized image and text features for cosine similarity
            image_embeds = outputs.image_embeds / outputs.image_embeds.norm(dim=-1, keepdim=True)
            text_embeds = outputs.text_embeds / outputs.text_embeds.norm(dim=-1, keepdim=True)

            # Cosine similarity matrix: [num_images, num_texts]
            cosine_sims = torch.matmul(image_embeds, text_embeds.t())

            # Softmax probabilities over benchmark descriptions
            benchmark_logits = outputs.logits_per_image[:, : len(benchmarks)]
            benchmark_probs = benchmark_logits.softmax(dim=-1)

        for i in range(len(images)):
            target_sim = float(cosine_sims[i, target_idx].item())
            # Cosine similarity for CLIP typically ranges 0.15 - 0.35; scale to a 0-100% score
            scaled_score = max(0.0, min(100.0, (target_sim - 0.15) / 0.20 * 100.0))

            b_probs = {benchmarks[j]: round(float(benchmark_probs[i, j].item()) * 100, 1) for j in range(len(benchmarks))}
            top_bench = max(b_probs.items(), key=lambda kv: kv[1])[0]

            results.append({
                "target_similarity": round(target_sim, 4),
                "target_score_pct": round(scaled_score, 1),
                "benchmark_probs": b_probs,
                "top_benchmark": top_bench,
            })
    except Exception as e:
        log.warning("CLIP scoring failed: %s", e)
        for _ in images:
            results.append({
                "target_similarity": 0.25,
                "target_score_pct": 50.0,
                "benchmark_probs": {b: 25.0 for b in benchmarks},
                "top_benchmark": benchmarks[0],
            })

    return results

