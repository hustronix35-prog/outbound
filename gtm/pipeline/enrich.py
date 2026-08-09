from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from gtm.config import get_settings
from gtm.models import Lead, LeadStatus, get_session
from gtm.providers import (
    ApolloProvider,
    BetterContactProvider,
    HunterProvider,
    PatternEmailProvider,
)
from gtm.providers.base import RawLead

logger = logging.getLogger(__name__)


def _lookups_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as session:
        # Approximate: count leads that moved into enriching/ready/no_email today
        return int(
            session.scalar(
                select(func.count())
                .select_from(Lead)
                .where(
                    Lead.updated_at >= start,
                    Lead.status.in_(
                        [
                            LeadStatus.READY.value,
                            LeadStatus.NO_EMAIL.value,
                            LeadStatus.ENRICHING.value,
                        ]
                    ),
                )
            )
            or 0
        )


def _email_providers():
    providers = []
    for cls in (BetterContactProvider, HunterProvider, ApolloProvider):
        p = cls()
        if p.available():
            providers.append(p)
    # Pattern only in dry_run for demos — never treat as verified for real sends
    if get_settings().dry_run:
        providers.append(PatternEmailProvider())
    return providers


def enrich_qualified(campaign_id: int, limit: int | None = None) -> dict:
    settings = get_settings()
    remaining = settings.max_email_lookups_per_day - _lookups_today()
    if remaining <= 0:
        logger.info("Email lookup daily cap reached")
        return {"enriched": 0, "missed": 0, "skipped_cap": True}

    cap = min(limit or remaining, remaining)
    providers = _email_providers()
    stats = {"enriched": 0, "missed": 0, "had_email": 0}

    with get_session() as session:
        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.CONTACT_SELECTED.value,
                Lead.disqualified.is_(False),
            )
            .order_by(Lead.fit_score.desc())
            .limit(cap)
        ).all()

        for lead in leads:
            lead.status = LeadStatus.ENRICHING.value
            session.flush()

            if lead.email and "@" in lead.email:
                lead.status = LeadStatus.CONTACT_SELECTED.value
                lead.email_confidence = max(lead.email_confidence, 0.7)
                stats["had_email"] += 1
                stats["enriched"] += 1
                continue

            raw = RawLead(
                first_name=lead.first_name,
                last_name=lead.last_name,
                full_name=lead.full_name,
                company=lead.company,
                company_domain=lead.company_domain,
                linkedin_url=lead.linkedin_url,
                email=lead.email,
            )
            found = ""
            conf = 0.0
            for provider in providers:
                email, c = provider.find_email(raw)
                if email and "@" in email:
                    found, conf = email, c
                    break

            if found:
                lead.email = found
                lead.email_confidence = conf
                lead.status = LeadStatus.CONTACT_SELECTED.value
                stats["enriched"] += 1
            else:
                lead.status = LeadStatus.NO_EMAIL.value
                stats["missed"] += 1

        session.commit()
    logger.info("Enrich stats: %s", stats)
    return stats
