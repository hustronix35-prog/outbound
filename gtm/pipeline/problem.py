from __future__ import annotations

import logging
import re

from sqlalchemy import select

from gtm.config import get_settings
from gtm.llm import get_llm
from gtm.models import Campaign, Lead, LeadStatus, get_session

logger = logging.getLogger(__name__)

PROBLEM_SYSTEM = """You detect whether this company likely has a buying decision problem
that our product can address. Return JSON:
{"has_problem": boolean, "problem": string, "score": number 0-1, "signals": string[]}.
Be conservative. Only has_problem=true when evidence is plausible."""


def _keyword_problem(lead: Lead, product: str, pains: list[str]) -> tuple[bool, str, float]:
    blob = f"{lead.qualify_reason} {lead.industry} {lead.title} {lead.company} {product}".lower()
    hits = [p for p in pains if p and p.lower() in blob]
    if hits:
        return True, f"Likely pressure around: {', '.join(hits[:3])}", 0.65
    # Soft default problem from product context for qualified SaaS-y companies
    if lead.company_score >= 0.7 or lead.fit_score >= 0.7:
        return True, "Qualified account; likely pipeline / growth efficiency pressure", 0.55
    return False, "No clear decision problem detected", 0.25


def detect_problems(campaign_id: int, limit: int = 50) -> dict:
    """Decision-Problem Detection — keyword/heuristic first, cheap LLM if keyed."""
    settings = get_settings()
    stats = {"found": 0, "none": 0, "llm": 0}

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        import json

        icp = {}
        try:
            icp = json.loads(campaign.icp_json or "{}")
        except json.JSONDecodeError:
            pass
        pains = list(icp.get("pain_points") or ["pipeline", "reply rate", "outbound", "meetings", "growth"])

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.COMPANY_QUALIFIED.value,
            )
            .order_by(Lead.company_score.desc())
            .limit(limit)
        ).all()

        llm = get_llm() if settings.llm_api_key else None
        for lead in leads:
            if llm is None:
                has, problem, score = _keyword_problem(lead, campaign.product_description, pains)
            else:
                try:
                    result = llm.chat_json(
                        [
                            {"role": "system", "content": PROBLEM_SYSTEM},
                            {
                                "role": "user",
                                "content": (
                                    f"Product: {campaign.product_name} — {campaign.product_description}\n"
                                    f"ICP pains: {pains}\n"
                                    f"Company: {lead.company} ({lead.industry}, {lead.employee_count} emp, "
                                    f"{lead.location})\nTitle on record: {lead.title}\n"
                                    f"Qualify note: {lead.qualify_reason}"
                                ),
                            },
                        ],
                        tier="cheap",
                        max_tokens=280,
                    )
                    stats["llm"] += 1
                    has = bool(result.get("has_problem"))
                    problem = str(result.get("problem") or "")
                    score = float(result.get("score") or 0)
                    if result.get("signals"):
                        problem = problem + " | signals: " + ", ".join(map(str, result["signals"][:4]))
                except Exception as exc:
                    logger.warning("Problem detect LLM failed lead=%s: %s", lead.id, exc)
                    has, problem, score = _keyword_problem(lead, campaign.product_description, pains)

            lead.decision_problem = problem
            lead.problem_score = score
            if has and score >= 0.45:
                lead.status = LeadStatus.PROBLEM_FOUND.value
                stats["found"] += 1
            else:
                lead.status = LeadStatus.NO_PROBLEM.value
                stats["none"] += 1

        session.commit()
    logger.info("Problem detect stats: %s", stats)
    return stats
