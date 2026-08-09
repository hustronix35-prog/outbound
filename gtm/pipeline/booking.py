from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from gtm.config import get_settings
from gtm.mail import send_email
from gtm.models import Campaign, FeedbackEvent, Lead, LeadStatus, Message, get_session
from gtm.notify import get_telegram

logger = logging.getLogger(__name__)


def push_booking(campaign_id: int, limit: int = 20) -> dict:
    """Meeting / Booking — send calendar CTA to interested leads."""
    settings = get_settings()
    stats = {"booked_sent": 0, "skipped": 0}

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")
        link = campaign.booking_link or settings.booking_link
        if not link:
            logger.warning("No booking link configured")
            return {"booked_sent": 0, "skipped": 0, "error": "no_booking_link"}

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.INTERESTED.value,
                Lead.email != "",
            )
            .limit(limit)
        ).all()

        tg = get_telegram()
        for lead in leads:
            first = lead.first_name or (lead.full_name.split(" ")[0] if lead.full_name else "there")
            subject = f"Re: {lead.subject}" if lead.subject else f"Quick time with {settings.sender_name}"
            body = (
                f"Hi {first},\n\n"
                f"Glad this resonated. Grab any open slot here and we'll make it concrete:\n"
                f"{link}\n\n"
                f"{settings.sender_name}"
            )
            result = send_email(
                to=lead.email,
                subject=subject,
                body=body,
                in_reply_to=lead.message_id,
            )
            session.add(
                Message(
                    lead_id=lead.id,
                    direction="outbound",
                    subject=subject,
                    body=body,
                    message_id=result.message_id,
                )
            )
            lead.message_id = result.message_id or lead.message_id
            lead.status = LeadStatus.BOOKED.value
            lead.booked_at = datetime.now(timezone.utc)
            session.add(
                FeedbackEvent(
                    campaign_id=campaign_id,
                    lead_id=lead.id,
                    outcome="booked",
                    note="booking_link_sent",
                )
            )
            try:
                tg.report_booking(lead, link)
            except Exception:
                logger.exception("Telegram booking notify failed")
            stats["booked_sent"] += 1

        session.commit()
    logger.info("Booking stats: %s", stats)
    return stats
