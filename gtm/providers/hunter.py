from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from gtm.config import get_settings
from gtm.cost import ENRICHMENT_COST_USD, can_spend, record_cost
from gtm.providers.base import RawLead

logger = logging.getLogger(__name__)


class HunterProvider:
    name = "hunter"

    def __init__(self) -> None:
        self.api_key = get_settings().hunter_api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def find_email(self, lead: RawLead) -> tuple[str, float]:
        if not self.available() or not lead.company_domain:
            return "", 0.0
        if not can_spend(ENRICHMENT_COST_USD):
            return "", 0.0
        params = {
            "domain": lead.company_domain,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "api_key": self.api_key,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get("https://api.hunter.io/v2/email-finder", params=params)
                resp.raise_for_status()
                data = resp.json().get("data") or {}
        except Exception as exc:
            logger.warning("Hunter find failed: %s", exc)
            return "", 0.0

        email = data.get("email") or ""
        score = float(data.get("score") or 0) / 100.0
        if email:
            record_cost(
                kind="enrichment",
                estimated_usd=ENRICHMENT_COST_USD,
                provider=self.name,
                units=1,
                note="email_finder",
            )
        return email, score

    def people_from_domain(
        self,
        *,
        company: str,
        domain: str,
        industry: str = "",
        employee_count: str = "",
        location: str = "",
        wanted_titles: list[str] | None = None,
        limit: int = 5,
    ) -> list[RawLead]:
        """Domain search → people/emails (works on Hunter free credits)."""
        if not self.available() or not domain:
            return []
        if not can_spend(0.01):
            return []
        params: dict[str, Any] = {
            "domain": domain,
            "api_key": self.api_key,
            "limit": min(max(limit, 1), 10),
            "type": "personal",
        }
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.get("https://api.hunter.io/v2/domain-search", params=params)
                resp.raise_for_status()
                data = resp.json().get("data") or {}
        except Exception as exc:
            logger.warning("Hunter domain-search failed for %s: %s", domain, exc)
            return []

        emails = data.get("emails") or []
        record_cost(
            kind="enrichment",
            estimated_usd=0.01,
            provider=self.name,
            units=1,
            note=f"domain_search:{domain}",
        )
        wanted = [_norm(t) for t in (wanted_titles or [])]
        leads: list[RawLead] = []
        for row in emails:
            title = row.get("position") or ""
            if wanted and not _title_matches(title, wanted):
                continue
            first = row.get("first_name") or ""
            last = row.get("last_name") or ""
            email = row.get("value") or ""
            if not email and not (first or last):
                continue
            leads.append(
                RawLead(
                    external_id=f"hunter:{email or (first+last+domain)}",
                    full_name=f"{first} {last}".strip(),
                    first_name=first,
                    last_name=last,
                    title=title,
                    company=company or data.get("organization") or "",
                    company_domain=domain,
                    linkedin_url=(row.get("linkedin") or ""),
                    location=location,
                    industry=industry,
                    employee_count=employee_count,
                    email=email,
                    email_confidence=float(row.get("confidence") or 0) / 100.0,
                    raw=row,
                )
            )
            if len(leads) >= limit:
                break
        return leads


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _title_matches(title: str, wanted: list[str]) -> bool:
    t = _norm(title)
    if not t:
        return False
    for w in wanted:
        if not w:
            continue
        if w in t:
            return True
        toks = [x for x in w.split() if len(x) > 2]
        if toks and all(x in t for x in toks):
            return True
    # seniority fallbacks for ICP leadership roles
    return any(x in t for x in ("ceo", "coo", "founder", "chief", "vp ", "head of", "vice president"))
