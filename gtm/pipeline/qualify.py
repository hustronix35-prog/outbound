from __future__ import annotations

import logging
import re

from sqlalchemy import select

from gtm.config import get_settings
from gtm.llm import BudgetExceeded, get_llm
from gtm.llm.prompts import QUALIFY_SYSTEM
from gtm.models import Campaign, Lead, LeadStatus, get_session
from gtm.pipeline.icp import build_or_load_icp

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def company_heuristic_score(lead: Lead, icp: dict) -> float:
    """Free company-level pre-filter before any LLM spend."""
    score = 0.0
    industry = _norm(lead.industry)
    location = _norm(lead.location)
    company = _norm(lead.company)
    blob = f"{industry} {location} {company} {_norm(lead.title)}"

    for kw in icp.get("keywords_include") or []:
        if _norm(kw) and _norm(kw) in blob:
            score += 0.1
    for kw in icp.get("keywords_exclude") or []:
        if _norm(kw) and _norm(kw) in blob:
            score -= 0.3

    geos = [_norm(g) for g in (icp.get("geos") or [])]
    if geos:
        if any(g and g in location for g in geos):
            score += 0.2
        elif location:
            score -= 0.05
    else:
        score += 0.05

    industries = [_norm(i) for i in (icp.get("industries") or [])]
    if industries:
        if any(i and (i in industry or i in company) for i in industries):
            score += 0.25
        elif industry:
            score += 0.05
    else:
        score += 0.1

    try:
        emp = int(re.sub(r"[^\d]", "", lead.employee_count) or "0")
    except ValueError:
        emp = 0
    lo = icp.get("employee_min")
    hi = icp.get("employee_max")
    if emp and lo is not None and hi is not None:
        if int(lo) <= emp <= int(hi):
            score += 0.25
        else:
            score -= 0.25
    elif not emp:
        score += 0.05

    # Title presence is a weak positive at company stage (contact comes later)
    titles = [_norm(t) for t in (icp.get("titles") or [])]
    title = _norm(lead.title)
    if titles and any(t and t in title for t in titles):
        score += 0.15

    return max(0.0, min(1.0, score))


def qualify_companies(campaign_id: int, limit: int = 50) -> dict:
    """Company Qualification — heuristics first, LLM only for borderline."""
    settings = get_settings()
    stats = {"accepted": 0, "rejected": 0, "llm": 0, "heuristic_only": 0}

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        icp = build_or_load_icp(campaign)
        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status.in_(
                    [LeadStatus.DISCOVERED.value, LeadStatus.QUALIFIED.value, LeadStatus.REJECTED.value]
                ),
            )
            .limit(limit)
        ).all()
        # Only process discovered (re-run safe: skip already company_*)
        leads = [l for l in leads if l.status == LeadStatus.DISCOVERED.value]

        llm = None
        use_llm = bool(settings.llm_api_key)
        for lead in leads:
            h = company_heuristic_score(lead, icp)
            lead.heuristic_score = h
            lead.company_score = h

            if h >= settings.qualify_auto_accept:
                lead.fit_score = h
                lead.status = LeadStatus.COMPANY_QUALIFIED.value
                lead.qualify_reason = "company auto-accept via heuristic"
                stats["accepted"] += 1
                stats["heuristic_only"] += 1
                continue
            if h <= settings.qualify_auto_reject:
                lead.fit_score = h
                lead.status = LeadStatus.COMPANY_REJECTED.value
                lead.qualify_reason = "company auto-reject via heuristic"
                stats["rejected"] += 1
                stats["heuristic_only"] += 1
                continue

            if not use_llm:
                accept = h >= 0.55
                lead.fit_score = h
                lead.status = (
                    LeadStatus.COMPANY_QUALIFIED.value if accept else LeadStatus.COMPANY_REJECTED.value
                )
                lead.qualify_reason = "company borderline heuristic (no LLM)"
                stats["accepted" if accept else "rejected"] += 1
                stats["heuristic_only"] += 1
                continue

            try:
                if llm is None:
                    llm = get_llm()
                result = llm.chat_json(
                    [
                        {"role": "system", "content": QUALIFY_SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                "Qualify the COMPANY (not the person yet).\n"
                                f"ICP: {icp}\n"
                                f"Company={lead.company}, industry={lead.industry}, "
                                f"location={lead.location}, employees={lead.employee_count}, "
                                f"domain={lead.company_domain}, heuristic={h:.2f}"
                            ),
                        },
                    ],
                    tier="cheap",
                    max_tokens=250,
                )
                stats["llm"] += 1
                fit = float(result.get("fit_score") or h)
                lead.fit_score = fit
                lead.company_score = fit
                lead.qualify_reason = str(result.get("reason") or "")
                accept = bool(result.get("accept")) if "accept" in result else fit >= 0.6
                lead.status = (
                    LeadStatus.COMPANY_QUALIFIED.value if accept else LeadStatus.COMPANY_REJECTED.value
                )
                stats["accepted" if accept else "rejected"] += 1
            except BudgetExceeded:
                lead.status = LeadStatus.COMPANY_REJECTED.value
                lead.qualify_reason = "budget exceeded"
                stats["rejected"] += 1
            except Exception as exc:
                logger.warning("Company qualify failed lead=%s: %s", lead.id, exc)
                lead.status = LeadStatus.COMPANY_REJECTED.value
                lead.qualify_reason = f"error: {exc}"
                stats["rejected"] += 1

        session.commit()
    logger.info("Company qualify stats: %s", stats)
    return stats


# Back-compat alias used by older CLI/daemon imports
def qualify_pending(campaign_id: int, limit: int = 50) -> dict:
    return qualify_companies(campaign_id, limit=limit)


def heuristic_score(lead: Lead, icp: dict) -> float:
    return company_heuristic_score(lead, icp)
