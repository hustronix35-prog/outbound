from __future__ import annotations

import json
import logging
import re

import httpx
from sqlalchemy import select

from gtm.config import get_settings
from gtm.cost import can_spend, record_cost
from gtm.llm import get_llm
from gtm.llm.prompts import RESEARCH_SYSTEM
from gtm.models import Campaign, Lead, LeadStatus, get_session

logger = logging.getLogger(__name__)

RESEARCH_COST = 0.008


def _website_snippet(domain: str) -> str:
    if not domain:
        return ""
    host = domain if "://" in domain else f"https://{domain}"
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            resp = client.get(host, headers={"User-Agent": "OutboundIntelligenceBot/0.1"})
            if resp.status_code >= 400:
                return ""
            text = resp.text
            text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:1200]
    except Exception:
        return ""


def _verified_facts(lead: Lead, snippet: str) -> dict:
    """Only emit fields that actually have values — never invent."""
    facts: dict = {
        "company": lead.company or None,
        "domain": lead.company_domain or None,
        "industry": lead.industry or None,
        "location": lead.location or None,
        "employee_count": lead.employee_count or None,
        "prospect_name": lead.full_name or None,
        "prospect_title": lead.title or None,
        "linkedin_url": lead.linkedin_url or None,
        "decision_problem": lead.decision_problem or None,
        "qualify_reason": lead.qualify_reason or None,
        "social_signals": lead.social_signals or None,
        "website_excerpt": snippet or None,
    }
    return {k: v for k, v in facts.items() if v}


def research_companies(campaign_id: int, limit: int = 30) -> dict:
    """Outbound Intelligence research: SIGNAL → PROBLEM → CONSEQUENCE chain."""
    settings = get_settings()
    stats = {"researched": 0, "skipped": 0, "not_relevant": 0}

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.CONTACT_SELECTED.value,
                Lead.email != "",
                Lead.company_research == "",
                Lead.disqualified.is_(False),
            )
            .order_by(Lead.fit_score.desc())
            .limit(limit)
        ).all()

        llm = get_llm() if settings.llm_api_key else None
        for lead in leads:
            snippet = _website_snippet(lead.company_domain)
            facts = _verified_facts(lead, snippet)
            base = json.dumps(facts, ensure_ascii=False)

            if llm and can_spend(RESEARCH_COST):
                try:
                    result = llm.chat_json(
                        [
                            {"role": "system", "content": RESEARCH_SYSTEM},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "product_name": campaign.product_name
                                        or settings.product_name,
                                        "product_description": campaign.product_description
                                        or settings.product_description,
                                        "verified_facts_only": facts,
                                    }
                                ),
                            },
                        ],
                        tier="cheap",
                        max_tokens=500,
                    )
                    lead.company_research = json.dumps(result, ensure_ascii=False)[:4000]
                    hooks = result.get("hooks") or []
                    if hooks:
                        lead.personalization_hook = "; ".join(map(str, hooks[:3]))
                    elif result.get("likely_problem"):
                        lead.personalization_hook = str(result["likely_problem"])[:500]
                    if result.get("likely_problem") and not lead.decision_problem:
                        lead.decision_problem = str(result["likely_problem"])[:1000]
                    if result.get("relevant") is False or str(result.get("confidence", "")).lower() == "low":
                        lead.status = LeadStatus.NO_PROBLEM.value
                        stats["not_relevant"] += 1
                        stats["researched"] += 1
                        continue
                    record_cost(
                        kind="llm",
                        estimated_usd=RESEARCH_COST,
                        provider="research",
                        note="outbound_intelligence_research",
                    )
                except Exception as exc:
                    logger.debug("Research LLM skipped: %s", exc)
                    lead.company_research = base[:2000]
            else:
                lead.company_research = base[:2000]

            if lead.email:
                lead.status = LeadStatus.RESEARCHED.value
            stats["researched"] += 1

        session.commit()
    logger.info("Research stats: %s", stats)
    return stats
