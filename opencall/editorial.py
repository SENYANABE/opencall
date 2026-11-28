"""Настраиваемые правила редакционного отбора."""
import json
from pathlib import Path

from lib import days_until

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/editorial.json").read_text(encoding="utf-8"))
POLICY = CONFIG["selection"]
CONFIRMED = "подтверждено"
REVIEW = "требует уточнения"
UNAVAILABLE = "недоступно"


def bucket(row):
    if row.get("access_status") != CONFIRMED:
        return "review"
    return "ru" if row.get("geography") == "Россия" else "international"


def ranking_score(row, assessments):
    facts = {
        "access": 1.0 if row["access_status"] == CONFIRMED else 0.5,
        "time": min(days_until(row["deadline"]) / 14, 1),
    }
    for name in ("specialization", "level", "effort", "value", "transparency"):
        facts[name] = {"высоко": 1.0, "средне": 0.5}.get(assessments.get(name), 0)
    weights = POLICY["weights"]
    return round(100 * sum(weights[name] * value for name, value in facts.items()) / sum(weights.values()))


def rank_verified(rows):
    """Мягкие ориентиры состава, внутри каждой группы выше проверенная релевантность."""
    ordered = sorted(rows, key=lambda r: (-r["score"], r["deadline"], r["id"]))
    counts = dict.fromkeys(POLICY["target_mix"], 0)
    ranked = []
    while ordered:
        position = len(ranked) + 1
        index = next((i for i, row in enumerate(ordered)
                      if counts[bucket(row)] < position * POLICY["target_mix"][bucket(row)] / 100), 0)
        row = ordered.pop(index)
        counts[bucket(row)] += 1
        ranked.append(row)
    return ranked
