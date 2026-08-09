from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from gtm.config import get_settings
from gtm.llm import BudgetExceeded, get_llm
from gtm.llm.prompts import FOLLOWUP_SYSTEM
from gtm.mail import fetch_unseen, send_email
from gtm.models import Campaign, Lead, LeadStatus, Message, get_session

logger = logging.getLogger(__name__)


def _extract_email(from_addr: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+", from_addr or "")
    return (m.group(0) if m else "").lower()


def collect_replies() -> int:
    mails = fetch_unseen()
    matched = 0
    with get_session() as session:
        for mail in mails:
            addr = _extract_email(mail.from_addr)
            if not addr:
                continue
            lead = session.scalars(
                select(Lead).where(
                    Lead.email == addr,
                    Lead.status.in_(
                        [
                            LeadStatus.SENT.value,
                            LeadStatus.FOLLOW_UP.value,
                            LeadStatus.REPLIED.value,
                        ]
                    ),
                )
            ).first()
            if not lead:
                continue
            session.add(
                Message(
                    lead_id=lead.id,
                    direction="inbound",
                    subject=mail.subject,
                    body=mail.body,
                    message_id=mail.message_id,
                )
            )
            lead.status = LeadStatus.REPLIED.value
            lead.next_follow_up_at = datetime.now(timezone.utc)
            matched += 1
        session.commit()
    return matched


def run_followups(campaign_id: int, limit: int = 20) -> dict:
    settings = get_settings()
    stats = {"sent": 0, "wait": 0, "complete": 0, "suppress": 0}
    now = datetime.now(timezone.utc)

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.disqualified.is_(False),
                or_(
                    Lead.status == LeadStatus.REPLIED.value,
                    Lead.status.in_([LeadStatus.SENT.value, LeadStatus.FOLLOW_UP.value]),
                ),
                Lead.next_follow_up_at.is_not(None),
                Lead.next_follow_up_at <= now,
            )
            .limit(limit)
        ).all()

        llm = get_llm() if get_settings().llm_api_key else None
        for lead in leads:
            history = session.scalars(
                select(Message).where(Message.lead_id == lead.id).order_by(Message.created_at)
            ).all()
            transcript = [
                {"direction": m.direction, "subject": m.subject, "body": m.body[:1500]}
                for m in history
            ]
            if llm is None:
                # No LLM: schedule another wait; never auto-spam follow-ups without a model
                lead.next_follow_up_at = now + timedelta(hours=settings.follow_up_hours)
                stats["wait"] += 1
                continue
            try:
                decision = llm.chat_json(
                    [
                        {"role": "system", "content": FOLLOWUP_SYSTEM},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "product": campaign.product_name,
                                    "booking_link": campaign.booking_link or settings.booking_link,
                                    "lead": {
                                        "name": lead.full_name,
                                        "title": lead.title,
                                        "company": lead.company,
                                    },
                                    "thread": transcript,
                                }
                            ),
                        },
                    ],
                    tier="cheap",
                    max_tokens=450,
                )
            except BudgetExceeded:
                break
            except Exception as exc:
                logger.warning("Follow-up LLM failed lead=%s: %s", lead.id, exc)
                continue

            action = (decision.get("action") or "wait").lower()
            if action == "suppress":
                lead.status = LeadStatus.UNSUBSCRIBED.value
                lead.disqualified = True
                lead.next_follow_up_at = None
                stats["suppress"] += 1
            elif action == "complete":
                lead.status = LeadStatus.COMPLETED.value
                lead.next_follow_up_at = None
                stats["complete"] += 1
            elif action == "send":
                subject = str(decision.get("subject") or f"Re: {lead.subject}")
                body = str(decision.get("body") or "")
                if body:
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
                    lead.status = LeadStatus.FOLLOW_UP.value
                    wait_h = int(decision.get("wait_hours") or settings.follow_up_hours)
                    lead.next_follow_up_at = now + timedelta(hours=wait_h)
                    stats["sent"] += 1
            else:
                wait_h = int(decision.get("wait_hours") or settings.follow_up_hours)
                lead.next_follow_up_at = now + timedelta(hours=wait_h)
                stats["wait"] += 1

        session.commit()
    return stats
