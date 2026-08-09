from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from gtm.config import get_settings
from gtm.cost import ENRICHMENT_COST_USD, can_spend, record_cost
from gtm.providers.base import RawLead

logger = logging.getLogger(__name__)


def _domain(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    return (urlparse(value).netloc or "").lower().removeprefix("www.")


class ApolloProvider:
    """Apollo discovery.

    Free plan: organizations/search works; people APIs are paywalled.
    Paid plan: people search is used when available, with org fallback.
    """

    name = "apollo"

    def __init__(self) -> None:
        self.api_key = get_settings().apollo_api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-Api-Key": self.api_key, "Cache-Control": "no-cache"}

    def search(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        if not self.available():
            return []
        people = self._search_people(icp, limit)
        if people:
            return people
        # Free-plan path: company discovery only (contacts filled by Hunter later)
        return self.search_companies(icp, limit)

    def search_companies(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        if not self.available():
            return []
        payload: dict[str, Any] = {
            "page": 1,
            "per_page": min(max(limit, 1), 25),
            "q_organization_keyword_tags": (icp.get("keywords_include") or ["saas", "b2b", "software"])[:5],
            "organization_locations": (icp.get("geos") or [])[:8],
        }
        if icp.get("employee_min") or icp.get("employee_max"):
            payload["organization_num_employees_ranges"] = [
                f"{icp.get('employee_min') or 50},{icp.get('employee_max') or 500}"
            ]
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    "https://api.apollo.io/api/v1/organizations/search",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Apollo org search failed: %s", exc)
            return []

        orgs = data.get("organizations") or data.get("accounts") or []
        leads: list[RawLead] = []
        for org in orgs[:limit]:
            domain = _domain(org.get("primary_domain") or org.get("website_url") or "")
            name = org.get("name") or ""
            if not domain and not name:
                continue
            leads.append(
                RawLead(
                    external_id=f"apollo-org:{org.get('id') or domain or name}",
                    full_name="",
                    first_name="",
                    last_name="",
                    title="",
                    company=name,
                    company_domain=domain,
                    location=(org.get("country") or org.get("raw_address") or ""),
                    industry=(org.get("industry") or ""),
                    employee_count=str(org.get("estimated_num_employees") or ""),
                    raw=org,
                )
            )
        logger.info("Apollo companies found: %s", len(leads))
        return leads

    def _search_people(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        payload: dict[str, Any] = {
            "page": 1,
            "per_page": min(limit, 25),
            "person_titles": icp.get("titles") or [],
            "person_locations": icp.get("geos") or [],
            "q_organization_keyword_tags": icp.get("keywords_include") or [],
        }
        if icp.get("employee_min") or icp.get("employee_max"):
            payload["organization_num_employees_ranges"] = [
                f"{icp.get('employee_min') or 1},{icp.get('employee_max') or 10000}"
            ]
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    "https://api.apollo.io/api/v1/mixed_people/api_search",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code == 403:
                    logger.info("Apollo people search not on this plan — using organizations")
                    return []
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Apollo people search failed: %s", exc)
            return []

        people = data.get("people") or []
        leads: list[RawLead] = []
        for p in people[:limit]:
            org = p.get("organization") or {}
            first = p.get("first_name") or ""
            last = p.get("last_name") or ""
            leads.append(
                RawLead(
                    external_id=str(p.get("id") or p.get("linkedin_url") or ""),
                    full_name=p.get("name") or f"{first} {last}".strip(),
                    first_name=first,
                    last_name=last,
                    title=p.get("title") or "",
                    company=org.get("name") or "",
                    company_domain=_domain(org.get("primary_domain") or org.get("website_url") or ""),
                    linkedin_url=p.get("linkedin_url") or "",
                    location=p.get("present_raw_address") or "",
                    industry=(org.get("industry") or ""),
                    employee_count=str(org.get("estimated_num_employees") or ""),
                    email=p.get("email") or "",
                    raw=p,
                )
            )
        return leads

    def find_email(self, lead: RawLead) -> tuple[str, float]:
        # Free plan blocks people/match — skip quietly
        if not self.available() or lead.email:
            return (lead.email, 0.7) if lead.email else ("", 0.0)
        if not can_spend(ENRICHMENT_COST_USD):
            return "", 0.0
        payload = {
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "organization_name": lead.company,
            "domain": lead.company_domain,
            "linkedin_url": lead.linkedin_url,
            "reveal_personal_emails": False,
        }
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(
                    "https://api.apollo.io/api/v1/people/match",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code == 403:
                    return "", 0.0
                resp.raise_for_status()
                person = (resp.json() or {}).get("person") or {}
        except Exception as exc:
            logger.warning("Apollo match failed: %s", exc)
            return "", 0.0
        email = person.get("email") or ""
        if email and email != "julia.r@example.org":
            record_cost(
                kind="enrichment",
                estimated_usd=ENRICHMENT_COST_USD,
                provider=self.name,
                units=1,
                note="people_match",
            )
            return email, 0.8
        return "", 0.0
