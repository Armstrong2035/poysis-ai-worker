"""
Embedding-based document grouping for consolidation clustering.

Replaces asking an LLM to invent groups from scratch over hundreds of raw
documents in one prompt (unreliable at scale — see categorizer.py history).
Grouping is now math (UMAP + HDBSCAN over document centroid embeddings,
already computed during ingestion — no extra embedding cost). The LLM is only
used for two small, focused judgment calls it's actually good at:

1. Picking which of a few candidate granularities looks most useful/coherent.
   HDBSCAN's own validity score (relative_validity_) was tested against real
   data and preferred a useless 2-cluster collapse over a genuinely coherent
   22-cluster split — it isn't a reliable signal here, so an LLM judging
   compact per-candidate summaries replaces it instead.
2. Merging the resulting fine-grained clusters into a smaller set of
   human-facing parent categories, and naming both levels.

Candidate min_cluster_size values are swept rather than computed from a single
formula: empirically (on a 528-document real workspace) cluster count did not
move gradually with this parameter — min_cluster_size=5 gave 22 sensible
clusters, =6 collapsed to 2. A blind per-N formula risks landing on a cliff
like that; sweeping a handful of values and letting the LLM pick avoids it.
"""
import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _candidate_min_cluster_sizes(n_docs: int) -> List[int]:
    ceiling = max(3, n_docs // 8)
    raw = [3, 4, 5, 6, 8, 10]
    return sorted({c for c in raw if c <= ceiling}) or [3]


def _reduce_and_cluster(centroids: np.ndarray, min_cluster_size: int) -> np.ndarray:
    import umap
    import hdbscan

    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    normed = centroids / norms
    reducer = umap.UMAP(n_components=5, metric="cosine", random_state=42)
    reduced = reducer.fit_transform(normed)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
    return clusterer.fit_predict(reduced)


def _summarize_candidate(index: int, min_cluster_size: int, labels: np.ndarray, titles: List[str]) -> str:
    from collections import defaultdict, Counter

    n_docs = len(titles)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    groups: Dict[int, List[str]] = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab != -1:
            groups[lab].append(titles[i])

    lines = [f"=== Candidate {index} (min_cluster_size={min_cluster_size}): "
              f"{n_clusters} clusters, {n_noise} unplaced ({n_noise / n_docs * 100:.0f}%) ==="]
    for cid, size in sorted(Counter(labels).items(), key=lambda x: -x[1]):
        if cid == -1:
            continue
        lines.append(f"  - cluster of {size} docs, examples: {groups[cid][:5]}")
    return "\n".join(lines)


async def pick_best_partition(
    model, source_ids: List[str], titles: List[str], centroids: np.ndarray
) -> Tuple[np.ndarray, int]:
    """Run a small sweep of candidate min_cluster_size values and have the LLM
    judge which produces the most useful, coherent grouping. Returns the
    winning label array (HDBSCAN's -1 = unplaced/noise) and its min_cluster_size.
    """
    import asyncio

    candidate_sizes = _candidate_min_cluster_sizes(len(source_ids))
    candidate_labels = [_reduce_and_cluster(centroids, mcs) for mcs in candidate_sizes]

    if len(candidate_sizes) == 1:
        return candidate_labels[0], candidate_sizes[0]

    summaries = [
        _summarize_candidate(i + 1, mcs, labels, titles)
        for i, (mcs, labels) in enumerate(zip(candidate_sizes, candidate_labels))
    ]

    prompt = f"""You are evaluating {len(candidate_sizes)} different candidate ways of grouping a
person's {len(source_ids)} documents into topic clusters. Each candidate was produced by the same
algorithm with a different sensitivity setting. For each candidate, you're shown the cluster
count, how many documents didn't fit any cluster, and a few example document titles per cluster.

Pick which candidate produces the most useful, coherent set of topic groups for someone browsing
their own knowledge base. Prefer specific, coherent groups over forcing unrelated documents
together, but don't pick a candidate that fragments everything into near-singleton clusters either.

{chr(10).join(summaries)}

Respond with ONLY valid JSON: {{"best_candidate": <number>, "reasoning": "..."}}
"""
    response = await asyncio.to_thread(model.generate_content, prompt)
    parsed = _parse_json(response.text)
    idx = parsed.get("best_candidate") if parsed else None
    if isinstance(idx, int) and 1 <= idx <= len(candidate_sizes):
        return candidate_labels[idx - 1], candidate_sizes[idx - 1]

    print(f"[EmbeddingCluster] Couldn't parse partition choice ({response.text[:200]!r}) — "
          f"defaulting to smallest min_cluster_size candidate")
    return candidate_labels[0], candidate_sizes[0]


async def merge_to_parents(
    model, fine_clusters: List[Dict[str, Any]], target_min: int = 8, target_max: int = 15
) -> Dict[str, Any]:
    """fine_clusters: [{"key": int, "size": int, "titles": [...]}, ...] (already
    coherent, math-derived groups). Ask the LLM to (a) give each one a short
    label and (b) merge them into target_min-target_max parent categories.

    Returns {"parents": {label: [key, ...]}, "cluster_labels": {key: label}}.
    """
    import asyncio

    lines = []
    for c in fine_clusters:
        sample = ", ".join(f'"{t}"' for t in c["titles"][:6])
        lines.append(f'- Group {c["key"]} ({c["size"]} docs): {sample}')

    prompt = f"""You're organizing a personal knowledge base that has already been split into
{len(fine_clusters)} specific, coherent groups by an algorithm. Two things:

1. Give each group a short, specific, human-readable label based on its example titles
   (e.g. "Bank Statements", "React Development", "Bible Study Notes" — not vague names
   like "Miscellaneous" or "Documents").
2. Merge the groups into {target_min}-{target_max} broader parent categories a person would
   use to navigate their files — combine groups that are clearly part of the same broader
   theme, but don't force unrelated groups together just to hit the count.

Groups:
{chr(10).join(lines)}

Return ONLY valid JSON:
{{
  "cluster_labels": {{"<group number>": "Group label", ...}},
  "parents": {{"Parent Category Name": [<group number>, ...], ...}}
}}
Every group number must appear in exactly one parent category.
"""
    response = await asyncio.to_thread(model.generate_content, prompt)
    parsed = _parse_json(response.text)
    if not parsed or "parents" not in parsed:
        print(f"[EmbeddingCluster] Merge step failed to parse — falling back to one parent per group")
        return {
            "parents": {f"Group {c['key']}": [c["key"]] for c in fine_clusters},
            "cluster_labels": {c["key"]: f"Group {c['key']}" for c in fine_clusters},
        }
    return parsed


def _parse_json(raw: str) -> Optional[Dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return None
