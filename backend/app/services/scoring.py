"""Unified retrieval scoring.

This module is the single source of truth for how retrieved chunks are scored
and how copilot answer confidence is derived. Both retrieval backends use it:

- the application-level search used by the in-memory and SQLite repositories, and
- the PostgreSQL-native hybrid search (pgvector + tsvector).

Keeping the combination and thresholds here guarantees that ranking scale and
confidence labels are identical across backends. Previously the two paths
combined raw signals on different magnitudes (hand-rolled BM25 vs. PostgreSQL
``ts_rank``), so the same query produced different rankings and confidence
labels depending on the database in use. Every signal fed into
``combine_signals`` is normalized to ``[0, 1]`` first, so the final score and the
confidence thresholds mean the same thing everywhere.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import List

# Weights for the unified hybrid retrieval score. They sum to 1.0 so the
# combined score stays in [0, 1] when every input signal is in [0, 1].
LEXICAL_WEIGHT = 0.35
VECTOR_WEIGHT = 0.35
ENTITY_WEIGHT = 0.20
PHRASE_WEIGHT = 0.10

# Confidence thresholds on the unified [0, 1] score. An exact equipment-tag
# match with strong lexical support lands in "strong"; a partial match in
# "moderate"; a thin single-signal match in "weak".
STRONG_THRESHOLD = 0.55
MODERATE_THRESHOLD = 0.35
WEAK_THRESHOLD = 0.12


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def bm25_lite(
    query_tokens: List[str],
    chunk_tokens: List[str],
    document_frequency: Counter,
    corpus_size: int,
) -> float:
    """Lightweight BM25-style lexical score used by the application fallback."""
    frequencies = Counter(chunk_tokens)
    score = 0.0
    for token in query_tokens:
        if token not in frequencies:
            continue
        idf = math.log((corpus_size + 1) / (document_frequency.get(token, 0) + 1)) + 1
        score += idf * (frequencies[token] / (frequencies[token] + 1.2))
    return score


def normalize_lexical(raw_scores: List[float]) -> List[float]:
    """Max-normalize lexical scores within one candidate set to [0, 1].

    Lexical rankers (bm25-lite and PostgreSQL ``ts_rank``) produce values on
    different absolute scales, so we normalize each candidate set to its own
    maximum before combining. This keeps the lexical contribution comparable no
    matter which backend generated the candidates.
    """
    max_score = max(raw_scores, default=0.0)
    # Treat a near-zero maximum as "no lexical signal". PostgreSQL ts_rank can
    # return a tiny non-zero value for rows that only qualified via the vector
    # clause; without this floor, max-normalization would divide equal tiny
    # values and hand every candidate a full 1.0 lexical score.
    if max_score <= 1e-9:
        return [0.0 for _ in raw_scores]
    return [(score / max_score if score > 0.0 else 0.0) for score in raw_scores]


def entity_fraction(matched: int, total_query_entities: int) -> float:
    """Fraction of the query's industrial entities present in a chunk."""
    if total_query_entities <= 0:
        return 0.0
    return clamp01(matched / total_query_entities)


def combine_signals(
    lexical_norm: float,
    vector: float,
    entity_frac: float,
    phrase: float,
) -> float:
    """Combine normalized [0, 1] signals into a single [0, 1] relevance score."""
    return (
        LEXICAL_WEIGHT * clamp01(lexical_norm)
        + VECTOR_WEIGHT * clamp01(vector)
        + ENTITY_WEIGHT * clamp01(entity_frac)
        + PHRASE_WEIGHT * clamp01(phrase)
    )


def confidence_from_score(score: float) -> str:
    """Map a unified [0, 1] retrieval score to a confidence label."""
    bounded = clamp01(score)
    if bounded >= STRONG_THRESHOLD:
        return "strong"
    if bounded >= MODERATE_THRESHOLD:
        return "moderate"
    if bounded >= WEAK_THRESHOLD:
        return "weak"
    return "none"
