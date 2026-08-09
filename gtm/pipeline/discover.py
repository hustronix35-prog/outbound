from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select

from gtm.config import get_settings
from gtm.models import Campaign, Lead, LeadStatus, get_session
from gtm.pipeline.icp import build_or_load_icp
from gtm.providers import (
    ApolloProvider,
    BetterContactProvider,
    CsvImportProvider,
    MockDiscoveryProvider,
)
from gtm.providers.base import RawLead
from gtm.providers.hunter import HunterProvider

logger = logging.getLogger(__name__)


def _providers(csv_path: str | None = None):
    providers = []
    bc = BetterContactProvider()
    if bc.available():
        providers.append(bc)
    apollo = ApolloProvider()
    if apollo.available():
        providers.append(apollo)
    path = csv_path
    if not path:
        hint = Path("data/leads.csv")
        if hint.exists():
            path = str(hint)
    if path:
        csv_p = CsvImportProvider(path)
        if csv_p.available():
            providers.append(csv_p)
    if not providers:
        providers.append(MockDiscoveryProvider())
    return providers


def _expand_companies_with_hunter(companies: list[RawLead], icp: dict, limit: int) -> list[RawLead]:
    hunter = HunterProvider()
    if not hunter.available():
        return []
    titles = list(icp.get("titles") or [])
    people: list[RawLead] = []
    # Cap domains to conserve Hunter credits
    for company in companies[: max(1, min(10, limit))]:
        if not company.company_domain:
            continue
        found = hunter.people_from_domain(
            company=company.company,
            domain=company.company_domain,
            industry=company.industry,
            employee_count=company.employee_count,
            location=company.location,
            wanted_titles=titles,
            limit=3,
        )
        people.extend(found)
        if len(people) >= limit:
            break
    return people[:limit]


def discover_leads(campaign_id: int, csv_path: str | None = None) -> int:
    settings = get_settings()
    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        icp = build_or_load_icp(campaign)
        campaign.icp_json = json.dumps(icp)
        session.commit()

        existing = {
            row[0]
            for row in session.execute(
                select(Lead.external_id).where(Lead.campaign_id == campaign_id)
            )
        }

    collected: list[RawLead] = []
    limit = settings.max_discover_per_run
    company_rows: list[RawLead] = []

    for provider in _providers(csv_path):
        remaining = limit - len(collected)
        if remaining <= 0:
            break
        found = provider.search(icp, remaining)
        logger.info("Discovery via %s → %s rows", provider.name, len(found))
        # Apollo free returns company shells (no person name) — expand via Hunter
        if provider.name == "apollo":
            people_like = [r for r in found if r.full_name or r.first_name]
            company_like = [r for r in found if not (r.full_name or r.first_name)]
            collected.extend(people_like)
            company_rows.extend(company_like)
        else:
            collected.extend(found)

    if len(collected) < limit and company_rows:
        expanded = _expand_companies_with_hunter(company_rows, icp, limit - len(collected))
        logger.info("Hunter expanded companies → %s people", len(expanded))
        collected.extend(expanded)

    # If still empty and no API people, keep mock only when nothing else configured
    if not collected and not any(p.name != "mock" for p in _providers(csv_path)):
        collected = MockDiscoveryProvider().search(icp, limit)

    inserted = 0
    with get_session() as session:
        for raw in collected:
            if not (raw.full_name or raw.first_name or raw.email):
                continue  # skip bare company shells
            if not raw.external_id:
                raw.external_id = f"{raw.full_name}|{raw.company}|{raw.email}".lower()
            if raw.external_id in existing:
                continue
            existing.add(raw.external_id)
            session.add(
                Lead(
                    campaign_id=campaign_id,
                    external_id=raw.external_id,
                    full_name=raw.full_name,
                    first_name=raw.first_name,
                    last_name=raw.last_name,
                    title=raw.title,
                    company=raw.company,
                    company_domain=raw.company_domain,
                    linkedin_url=raw.linkedin_url,
                    location=raw.location,
                    industry=raw.industry,
                    employee_count=raw.employee_count,
                    email=raw.email,
                    email_confidence=raw.email_confidence,
                    status=LeadStatus.DISCOVERED.value,
                    raw_json=json.dumps(raw.to_dict(), default=str),
                )
            )
            inserted += 1
        session.commit()
    logger.info("Inserted %s new leads", inserted)
    return inserted


def default_csv_hint() -> Path | None:
    path = Path("data/leads.csv")
    return path if path.exists() else None
