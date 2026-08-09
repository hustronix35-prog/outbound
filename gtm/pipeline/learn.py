from __future__ import annotations

import json
import logging
from collections import Counter

from sqlalchemy import select

from gtm.models import Campaign, FeedbackEvent, Lead, LeadStatus, get_session
from gtm.pipeline.icp import build_or_load_icp

logger = logging.getLogger(__name__)


def record_crm_snapshot(campaign_id: int) -> dict:
    """CRM + Feedback — funnel snapshot for the campaign."""
    with get_session() as session:
        rows = session.scalars(select(Lead).where(Lead.campaign_id == campaign_id)).all()
        counts = Counter(l.status for l in rows)
        feedback = session.scalars(
            select(FeedbackEvent).where(FeedbackEvent.campaign_id == campaign_id)
        ).all()
        outcomes = Counter(f.outcome for f in feedback)
        snapshot = {
            "funnel": dict(counts),
            "outcomes": dict(outcomes),
            "leads": len(rows),
            "feedback_events": len(feedback),
        }
    logger.info("CRM snapshot: %s", snapshot)
    return snapshot


def learn_icp(campaign_id: int) -> dict:
    """ICP Learning — adjust ICP weights from booked/interested vs no/rejected."""
    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        icp = build_or_load_icp(campaign)

        positives = session.scalars(
            select(Lead).where(
                Lead.campaign_id == campaign_id,
                Lead.status.in_(
                    [
                        LeadStatus.INTERESTED.value,
                        LeadStatus.BOOKED.value,
                        LeadStatus.COMPLETED.value,
                    ]
                ),
            )
        ).all()
        negatives = session.scalars(
            select(Lead).where(
                Lead.campaign_id == campaign_id,
                Lead.status.in_(
                    [
                        LeadStatus.NO.value,
                        LeadStatus.COMPANY_REJECTED.value,
                        LeadStatus.NO_PROBLEM.value,
                    ]
                ),
            )
        ).all()

        pos_titles = Counter(_norm_title(l.title) for l in positives if l.title)
        neg_titles = Counter(_norm_title(l.title) for l in negatives if l.title)
        pos_ind = Counter((l.industry or "").strip() for l in positives if l.industry)
        neg_ind = Counter((l.industry or "").strip() for l in negatives if l.industry)

        boost_titles = [t for t, _ in pos_titles.most_common(5) if t and neg_titles[t] < pos_titles[t]]
        avoid_titles = [t for t, c in neg_titles.most_common(5) if c >= 2 and pos_titles[t] == 0]
        boost_industries = [i for i, _ in pos_ind.most_common(5) if i]

        learning = {
            "samples_positive": len(positives),
            "samples_negative": len(negatives),
            "boost_titles": boost_titles,
            "avoid_titles": avoid_titles,
            "boost_industries": boost_industries,
            "notes": "Derived from CRM feedback; merge into next ICP refresh.",
        }

        # Soft-merge into ICP without wiping human filters
        titles = list(icp.get("titles") or [])
        for t in boost_titles:
            if t and t not in titles:
                titles.insert(0, t)
        icp["titles"] = titles[:12]
        excludes = list(icp.get("keywords_exclude") or [])
        for t in avoid_titles:
            if t and t not in excludes:
                excludes.append(t)
        icp["keywords_exclude"] = excludes[:20]
        industries = list(icp.get("industries") or [])
        for i in boost_industries:
            if i and i not in industries:
                industries.append(i)
        icp["industries"] = industries[:12]

        campaign.icp_json = json.dumps(icp)
        campaign.icp_learning_json = json.dumps(learning)
        session.commit()

    logger.info("ICP learning: %s", learning)
    return learning


def _norm_title(title: str) -> str:
    return " ".join((title or "").split()).strip()
