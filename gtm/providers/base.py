from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class RawLead:
    external_id: str = ""
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    company: str = ""
    company_domain: str = ""
    linkedin_url: str = ""
    location: str = ""
    industry: str = ""
    employee_count: str = ""
    email: str = ""
    email_confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class DiscoveryProvider(Protocol):
    name: str

    def search(self, icp: dict[str, Any], limit: int) -> list[RawLead]: ...


class EmailProvider(Protocol):
    name: str

    def find_email(self, lead: RawLead) -> tuple[str, float]: ...
