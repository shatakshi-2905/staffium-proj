"""Retrieval / ranking metrics and fairness statistics. Pure functions, no database."""
import math
from statistics import mean


def recall_at(ranked: list[str], relevant: set[str], k: int | None = None) -> float | None:
    if not relevant:
        return None
    top = ranked if k is None else ranked[:k]
    return len(relevant & set(top)) / len(relevant)


def precision_at(ranked: list[str], relevant: set[str], k: int) -> float:
    top = ranked[:k]
    return len(relevant & set(top)) / k if k else 0.0


def ndcg_at(ranked: list[str], labels: dict[str, int], k: int) -> float | None:
    """Graded nDCG with gain 2^label - 1. Unlabelled items count as label 0."""
    gains = [(2 ** labels.get(r, 0) - 1) for r in ranked[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted((2 ** v - 1 for v in labels.values()), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else None


def mrr(ranked: list[str], relevant: set[str]) -> float:
    for i, r in enumerate(ranked):
        if r in relevant:
            return 1.0 / (i + 1)
    return 0.0


def adverse_impact_ratio(selection_rates: dict[str, float]) -> float | None:
    """Lowest group selection rate divided by the highest (the 'four-fifths rule' flags < 0.8)."""
    rates = [r for r in selection_rates.values() if r is not None]
    if not rates or max(rates) == 0:
        return None
    return min(rates) / max(rates)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "mean_abs": None, "max_abs": None}
    return {"n": len(values), "mean": round(mean(values), 3), "mean_abs": round(mean(abs(v) for v in values), 3),
            "max_abs": round(max(abs(v) for v in values), 3)}
