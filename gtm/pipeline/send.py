from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from gtm.config import get_settings
from gtm.mail import send_email
from gtm.models import Lead, LeadStatus, Message, get_session
from gtm.notify import get_telegram

logger = logging.getLogger(__name__)


def _sends_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as session:
        return int(
            session.scalar(
                select(func.count()).select_from(Lead).where(Lead.sent_at >= start)
            )
            or 0
        )


def send_ready(campaign_id: int, limit: int | None = None) -> int:
    settings = get_settings()
    remaining = settings.max_sends_per_day - _sends_today()
    if remaining <= 0:
        logger.info("Daily send cap reached")
        return 0

    sent = 0
    tg = get_telegram()
    with get_session() as session:
        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.READY.value,
                Lead.email != "",
                Lead.body != "",
                Lead.disqualified.is_(False),
            )
            .order_by(Lead.fit_score.desc())
            .limit(min(limit or remaining, remaining))
        ).all()

        for lead in leads:
            result = send_email(to=lead.email, subject=lead.subject, body=lead.body)
            lead.message_id = result.message_id
            lead.sent_at = datetime.now(timezone.utc)
            lead.status = LeadStatus.SENT.value
            lead.next_follow_up_at = lead.sent_at + timedelta(hours=settings.follow_up_hours)
            session.add(
                Message(
                    lead_id=lead.id,
                    direction="outbound",
                    subject=lead.subject,
                    body=lead.body,
                    message_id=result.message_id,
                )
            )
            try:
                tg.report_send(lead, dry_run=result.dry_run or settings.dry_run)
            except Exception:
                logger.exception("Telegram send notify failed")
            sent += 1
        session.commit()
    logger.info("Sent %s emails (dry_run=%s)", sent, settings.dry_run)
    return sent
