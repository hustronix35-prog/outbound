from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from gtm.config import get_settings
from gtm.models import CostEvent, get_session

# Rough public list prices (USD per 1M tokens) — used only for soft budgeting.
MODEL_PRICES = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "claude-3-haiku-20240307": {"in": 0.25, "out": 1.25},
    "claude-3-5-haiku-latest": {"in": 0.80, "out": 4.00},
    "claude-3-5-haiku-20241022": {"in": 0.80, "out": 4.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}

ENRICHMENT_COST_USD = 0.03  # approx per paid email resolve
SOCIAL_COST_USD = 0.02


def estimate_llm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = MODEL_PRICES.get(model, {"in": 0.50, "out": 1.50})
    return (prompt_tokens / 1_000_000) * prices["in"] + (completion_tokens / 1_000_000) * prices["out"]


def today_spend_usd() -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(CostEvent.estimated_usd), 0.0)).where(
                CostEvent.created_at >= start
            )
        )
        return float(total or 0.0)


def budget_remaining() -> float:
    settings = get_settings()
    return max(0.0, settings.daily_budget_usd - today_spend_usd())


def can_spend(amount: float) -> bool:
    return budget_remaining() >= amount


def record_cost(
    *,
    kind: str,
    estimated_usd: float,
    provider: str = "",
    model: str = "",
    units: int = 0,
    note: str = "",
) -> None:
    with get_session() as session:
        session.add(
            CostEvent(
                kind=kind,
                provider=provider,
                model=model,
                units=units,
                estimated_usd=estimated_usd,
                note=note,
            )
        )
        session.commit()
