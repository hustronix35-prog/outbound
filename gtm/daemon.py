from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from gtm.config import get_settings
from gtm.cost import budget_remaining, today_spend_usd
from gtm.models import Campaign, init_db, get_session
from gtm.notify import get_telegram
from gtm.pipeline import (
    classify_replies,
    collect_replies,
    detect_problems,
    discover_leads,
    enrich_qualified,
    learn_icp,
    personalize_ready,
    push_booking,
    qualify_companies,
    record_crm_snapshot,
    research_companies,
    run_followups,
    select_contacts,
    send_ready,
)

logger = logging.getLogger(__name__)


def ensure_default_campaign() -> int:
    settings = get_settings()
    init_db()
    with get_session() as session:
        campaign = session.scalars(select(Campaign).where(Campaign.active.is_(True))).first()
        if campaign:
            return campaign.id
        campaign = Campaign(
            name="outbound-default",
            product_name=settings.product_name,
            product_description=settings.product_description,
            target_market=settings.target_market,
            booking_link=settings.booking_link,
        )
        session.add(campaign)
        session.commit()
        session.refresh(campaign)
        return campaign.id


def run_once(campaign_id: int | None = None, csv_path: str | None = None) -> dict:
    """Full Outbound Agent pass."""
    cid = campaign_id or ensure_default_campaign()
    summary: dict = {
        "campaign_id": cid,
        "agent": "Outbound Agent",
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # ICP Definition is cached inside discover/qualify via build_or_load_icp
    summary["market_discovery"] = discover_leads(cid, csv_path=csv_path)
    summary["company_qualification"] = qualify_companies(cid)
    summary["decision_problem_detection"] = detect_problems(cid)
    summary["contact_selection"] = select_contacts(cid)
    summary["email_enrichment"] = enrich_qualified(cid)
    summary["company_research"] = research_companies(cid)
    summary["personalized_outreach"] = personalize_ready(cid)
    summary["smtp_send"] = send_ready(cid)
    summary["replies_collected"] = collect_replies()
    summary["reply_classification"] = classify_replies(cid)
    summary["followups"] = run_followups(cid)
    summary["meeting_booking"] = push_booking(cid)
    summary["crm_feedback"] = record_crm_snapshot(cid)
    summary["icp_learning"] = learn_icp(cid)
    summary["spend_usd"] = today_spend_usd()
    summary["budget_remaining_usd"] = budget_remaining()
    logger.info("Run complete: %s", summary)
    try:
        get_telegram().report_run_summary(summary)
    except Exception:
        logger.exception("Telegram summary notify failed")
    return summary


def run_daemon(poll_seconds: int = 120, csv_path: str | None = None) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cid = ensure_default_campaign()
    logger.info(
        "Outbound Agent daemon campaign=%s dry_run=%s daily_budget=$%.2f",
        cid,
        settings.dry_run,
        settings.daily_budget_usd,
    )
    while True:
        try:
            if budget_remaining() <= 0:
                logger.warning("Daily budget exhausted — sleeping")
            else:
                run_once(cid, csv_path=csv_path)
        except Exception:
            logger.exception("Daemon loop error")
        time.sleep(poll_seconds)
