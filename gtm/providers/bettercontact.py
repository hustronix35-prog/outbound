from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from gtm.config import get_settings
from gtm.cost import ENRICHMENT_COST_USD, can_spend, record_cost
from gtm.providers.base import RawLead

logger = logging.getLogger(__name__)

API_BASE = "https://app.bettercontact.rocks/api/v1"


def _domain(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = urlparse(value).netloc or ""
    return host.lower().removeprefix("www.")


class BetterContactProvider:
    """Licensed firmographic discovery + work-email enrichment."""

    name = "bettercontact"

    def __init__(self) -> None:
        self.api_key = get_settings().bettercontact_api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        if not self.available():
            return []
        # Lead Finder-style payload — API shapes vary; keep resilient parsing.
        payload = {
            "filters": {
                "job_titles": icp.get("titles") or [],
                "seniorities": icp.get("seniorities") or [],
                "industries": icp.get("industries") or [],
                "locations": icp.get("geos") or [],
                "company_sizes": _company_sizes(icp),
                "keywords": icp.get("keywords_include") or [],
            },
            "limit": limit,
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{API_BASE}/lead-finder/search",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code >= 400:
                    # Fallback endpoint name used by some accounts
                    resp = client.post(
                        f"{API_BASE}/people/search",
                        headers=self._headers(),
                        json=payload,
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("BetterContact search failed: %s", exc)
            return []

        rows = data.get("data") or data.get("results") or data.get("people") or []
        leads: list[RawLead] = []
        for row in rows[:limit]:
            leads.append(_row_to_lead(row))
        return leads

    def find_email(self, lead: RawLead) -> tuple[str, float]:
        if not self.available():
            return "", 0.0
        if not can_spend(ENRICHMENT_COST_USD):
            return "", 0.0
        payload = {
            "data": [
                {
                    "first_name": lead.first_name,
                    "last_name": lead.last_name,
                    "company": lead.company,
                    "company_domain": lead.company_domain,
                    "linkedin_url": lead.linkedin_url,
                }
            ]
        }
        try:
            with httpx.Client(timeout=90.0) as client:
                resp = client.post(
                    f"{API_BASE}/async",
                    headers=self._headers(),
                    json=payload,
                )
                # Some plans use /enrichment
                if resp.status_code >= 400:
                    resp = client.post(
                        f"{API_BASE}/enrichment",
                        headers=self._headers(),
                        json=payload,
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("BetterContact enrich failed: %s", exc)
            return "", 0.0

        email, conf = _extract_email(data)
        if email:
            record_cost(
                kind="enrichment",
                estimated_usd=ENRICHMENT_COST_USD,
                provider=self.name,
                units=1,
                note="email_resolve",
            )
        return email, conf


def _company_sizes(icp: dict[str, Any]) -> list[str]:
    lo = icp.get("employee_min")
    hi = icp.get("employee_max")
    if lo is None and hi is None:
        return []
    return [f"{lo or 1}-{hi or 10000}"]


def _row_to_lead(row: dict[str, Any]) -> RawLead:
    first = row.get("first_name") or row.get("firstname") or ""
    last = row.get("last_name") or row.get("lastname") or ""
    full = row.get("full_name") or row.get("name") or f"{first} {last}".strip()
    domain = row.get("company_domain") or row.get("domain") or _domain(row.get("website") or "")
    return RawLead(
        external_id=str(row.get("id") or row.get("linkedin_url") or full),
        full_name=full,
        first_name=first or (full.split(" ")[0] if full else ""),
        last_name=last or (" ".join(full.split(" ")[1:]) if full else ""),
        title=row.get("title") or row.get("job_title") or "",
        company=row.get("company") or row.get("company_name") or "",
        company_domain=domain,
        linkedin_url=row.get("linkedin_url") or row.get("linkedin") or "",
        location=row.get("location") or row.get("country") or "",
        industry=row.get("industry") or "",
        employee_count=str(row.get("employee_count") or row.get("company_size") or ""),
        email=row.get("email") or "",
        email_confidence=float(row.get("email_confidence") or 0),
        raw=row,
    )


def _extract_email(data: dict[str, Any]) -> tuple[str, float]:
    if isinstance(data.get("email"), str) and "@" in data["email"]:
        return data["email"], float(data.get("confidence") or 0.8)
    rows = data.get("data") or data.get("results") or []
    if rows and isinstance(rows, list):
        row = rows[0]
        email = row.get("email") or row.get("work_email") or ""
        if email:
            return email, float(row.get("confidence") or row.get("email_confidence") or 0.75)
    return "", 0.0
