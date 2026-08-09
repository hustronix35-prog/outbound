from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from gtm.providers.base import RawLead


class CsvImportProvider:
    """Deterministic discovery from a CSV — zero API cost."""

    name = "csv"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def available(self) -> bool:
        return self.path.exists()

    def search(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        if not self.available():
            return []
        leads: list[RawLead] = []
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append(
                    RawLead(
                        external_id=row.get("external_id")
                        or row.get("email")
                        or row.get("linkedin_url")
                        or "",
                        full_name=row.get("full_name")
                        or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                        first_name=row.get("first_name") or "",
                        last_name=row.get("last_name") or "",
                        title=row.get("title") or "",
                        company=row.get("company") or "",
                        company_domain=row.get("company_domain") or row.get("domain") or "",
                        linkedin_url=row.get("linkedin_url") or "",
                        location=row.get("location") or "",
                        industry=row.get("industry") or "",
                        employee_count=row.get("employee_count") or "",
                        email=row.get("email") or "",
                        email_confidence=float(row.get("email_confidence") or 0),
                        raw=dict(row),
                    )
                )
                if len(leads) >= limit:
                    break
        return leads


class MockDiscoveryProvider:
    """Local sample leads for dry-run without paid APIs."""

    name = "mock"

    def available(self) -> bool:
        return True

    def search(self, icp: dict[str, Any], limit: int) -> list[RawLead]:
        samples = [
            RawLead(
                external_id="mock-1",
                full_name="Jordan Lee",
                first_name="Jordan",
                last_name="Lee",
                title="VP of Sales",
                company="Northstar SaaS",
                company_domain="northstarsaas.com",
                linkedin_url="https://www.linkedin.com/in/example-jordan",
                location="United States",
                industry="Software",
                employee_count="85",
            ),
            RawLead(
                external_id="mock-2",
                full_name="Sam Rivera",
                first_name="Sam",
                last_name="Rivera",
                title="Head of Growth",
                company="BrightPipe",
                company_domain="brightpipe.io",
                linkedin_url="https://www.linkedin.com/in/example-sam",
                location="United Kingdom",
                industry="SaaS",
                employee_count="40",
            ),
            RawLead(
                external_id="mock-3",
                full_name="Casey Nguyen",
                first_name="Casey",
                last_name="Nguyen",
                title="Founder & CEO",
                company="OrbitMetrics",
                company_domain="orbitmetrics.com",
                linkedin_url="https://www.linkedin.com/in/example-casey",
                location="United States",
                industry="Analytics",
                employee_count="22",
            ),
            RawLead(
                external_id="mock-4",
                full_name="Riley Patil",
                first_name="Riley",
                last_name="Patil",
                title="Intern",
                company="Random Shop",
                company_domain="randomshop.example",
                location="India",
                industry="Retail",
                employee_count="5",
            ),
        ]
        return samples[:limit]


class PatternEmailProvider:
    """Last-resort free guess: first@domain — low confidence, always verify externally."""

    name = "pattern"

    def available(self) -> bool:
        return True

    def find_email(self, lead: RawLead) -> tuple[str, float]:
        if not lead.first_name or not lead.company_domain:
            return "", 0.0
        local = lead.first_name.strip().lower().replace(" ", "")
        return f"{local}@{lead.company_domain}", 0.2
