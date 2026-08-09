from __future__ import annotations

import logging
import re

from sqlalchemy import select

from gtm.config import get_settings
from gtm.models import Campaign, Lead, LeadStatus, get_session
from gtm.pipeline.icp import build_or_load_icp

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _title_rank(title: str, wanted: list[str]) -> float:
    t = _norm(title)
    if not t:
        return 0.2
    best = 0.0
    for w in wanted:
        nw = _norm(w)
        if not nw:
            continue
        if nw == t or nw in t:
            best = max(best, 1.0)
        elif any(tok in t for tok in nw.split() if len(tok) > 3):
            best = max(best, 0.7)
    # seniority boosts
    for token, boost in (
        ("founder", 0.15),
        ("ceo", 0.15),
        ("cro", 0.12),
        ("vp", 0.1),
        ("head", 0.1),
        ("director", 0.08),
    ):
        if token in t:
            best = min(1.0, best + boost)
    if "intern" in t or "student" in t:
        best = min(best, 0.1)
    return best


def select_contacts(campaign_id: int, limit: int = 50) -> dict:
    """Contact Selection — keep rows whose title matches ICP decision-makers."""
    settings = get_settings()
    stats = {"selected": 0, "skipped": 0}

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        icp = build_or_load_icp(campaign)
        titles = list(icp.get("titles") or [])
        if not titles:
            titles = ["VP of Sales", "Head of Growth", "Founder", "CEO", "CRO"]

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.PROBLEM_FOUND.value,
            )
            .order_by(Lead.problem_score.desc())
            .limit(limit)
        ).all()

        for lead in leads:
            rank = _title_rank(lead.title, titles)
            if rank >= 0.55 and lead.full_name:
                lead.contact_reason = f"title match score={rank:.2f} for ICP roles"
                lead.status = LeadStatus.CONTACT_SELECTED.value
                # blend into fit
                lead.fit_score = max(lead.fit_score, (lead.company_score + lead.problem_score + rank) / 3)
                stats["selected"] += 1
            else:
                lead.contact_reason = f"weak title match ({rank:.2f}) or missing name"
                lead.status = LeadStatus.NO_PROBLEM.value  # park as not actionable contact
                stats["skipped"] += 1

        session.commit()
    logger.info("Contact select stats: %s", stats)
    return stats
