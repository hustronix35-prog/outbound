from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from gtm.config import get_settings
from gtm.llm import get_llm
from gtm.models import FeedbackEvent, Lead, LeadStatus, Message, ReplyLabel, get_session
from gtm.notify import get_telegram

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """Classify a cold-email reply. Return JSON:
{"label": "interested"|"later"|"no"|"unsubscribe"|"unknown", "reason": string, "confidence": number}.
interested = wants to talk / book / learn more
later = not now but open later
no = clear rejection
unsubscribe = stop contacting
unknown = unclear"""


def _heuristic_label(text: str) -> tuple[str, float]:
    t = (text or "").lower()
    if any(x in t for x in ("unsubscribe", "remove me", "stop emailing", "do not contact")):
        return ReplyLabel.UNSUBSCRIBE.value, 0.95
    if any(x in t for x in ("not interested", "no thanks", "pass", "don't contact", "dont contact")):
        return ReplyLabel.NO.value, 0.85
    if any(x in t for x in ("next quarter", "next year", "later", "busy", "circle back", "not now")):
        return ReplyLabel.LATER.value, 0.75
    if any(
        x in t
        for x in ("interested", "let's talk", "lets talk", "book", "calendar", "schedule", "sounds good", "tell me more")
    ):
        return ReplyLabel.INTERESTED.value, 0.8
    return ReplyLabel.UNKNOWN.value, 0.4


def classify_replies(campaign_id: int, limit: int = 30) -> dict:
    """Reply Classification → Interested / Later / No (+ unsubscribe)."""
    settings = get_settings()
    stats = {"interested": 0, "later": 0, "no": 0, "unsubscribe": 0, "unknown": 0}

    with get_session() as session:
        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.REPLIED.value,
            )
            .limit(limit)
        ).all()

        llm = get_llm() if settings.llm_api_key else None
        tg = get_telegram()
        for lead in leads:
            inbound = session.scalars(
                select(Message)
                .where(Message.lead_id == lead.id, Message.direction == "inbound")
                .order_by(Message.created_at.desc())
            ).first()
            body = (inbound.body if inbound else "") or ""

            if llm:
                try:
                    result = llm.chat_json(
                        [
                            {"role": "system", "content": CLASSIFY_SYSTEM},
                            {"role": "user", "content": body[:3000]},
                        ],
                        tier="cheap",
                        max_tokens=200,
                    )
                    label = str(result.get("label") or "unknown").lower()
                except Exception:
                    label, _ = _heuristic_label(body)
            else:
                label, _ = _heuristic_label(body)

            if label not in {e.value for e in ReplyLabel}:
                label = ReplyLabel.UNKNOWN.value

            lead.reply_label = label
            if label == ReplyLabel.INTERESTED.value:
                lead.status = LeadStatus.INTERESTED.value
                stats["interested"] += 1
            elif label == ReplyLabel.LATER.value:
                lead.status = LeadStatus.LATER.value
                stats["later"] += 1
            elif label == ReplyLabel.NO.value:
                lead.status = LeadStatus.NO.value
                stats["no"] += 1
            elif label == ReplyLabel.UNSUBSCRIBE.value:
                lead.status = LeadStatus.UNSUBSCRIBED.value
                lead.disqualified = True
                stats["unsubscribe"] += 1
            else:
                stats["unknown"] += 1

            session.add(
                FeedbackEvent(
                    campaign_id=campaign_id,
                    lead_id=lead.id,
                    outcome=label,
                    note="reply_classification",
                )
            )
            try:
                tg.report_reply(lead, label, excerpt=body)
            except Exception:
                logger.exception("Telegram reply notify failed")

        session.commit()
    logger.info("Reply classify stats: %s", stats)
    return stats
