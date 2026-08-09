from __future__ import annotations

import json
import logging
import re

from gtm.config import DATA_DIR, get_settings
from gtm.llm import get_llm
from gtm.llm.prompts import ICP_SYSTEM
from gtm.models import Campaign

logger = logging.getLogger(__name__)


def build_or_load_icp(campaign: Campaign, force: bool = False) -> dict:
    if campaign.icp_json and campaign.icp_json != "{}" and not force:
        try:
            return json.loads(campaign.icp_json)
        except json.JSONDecodeError:
            pass

    cache_path = DATA_DIR / "cache" / f"icp_campaign_{campaign.id}.json"
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    settings = get_settings()
    if not settings.llm_api_key:
        icp = _heuristic_icp(campaign)
        logger.info("ICP built without LLM (no API key)")
    else:
        llm = get_llm()
        user = (
            f"Product: {campaign.product_name}\n"
            f"Description: {campaign.product_description}\n"
            f"Target market: {campaign.target_market}\n"
            f"Fallback product description: {settings.product_description}"
        )
        icp = llm.chat_json(
            [
                {"role": "system", "content": ICP_SYSTEM},
                {"role": "user", "content": user},
            ],
            tier="cheap",
            max_tokens=700,
        )
        logger.info("ICP generated for campaign %s", campaign.id)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(icp, indent=2), encoding="utf-8")
    return icp


def _heuristic_icp(campaign: Campaign) -> dict:
    """Zero-cost ICP bootstrap from campaign target_market text only (no vendor defaults)."""
    text = f"{campaign.target_market} {campaign.product_description}".lower()
    title_map = {
        "founder": "Founder",
        "ceo": "CEO",
        "coo": "COO",
        "cto": "CTO",
        "head of product": "Head of Product",
        "vp product": "VP Product",
        "head of operations": "Head of Operations",
        "vp operations": "VP Operations",
        "head of growth": "Head of Growth",
        "vp sales": "VP Sales",
        "chief operating": "COO",
    }
    titles = [label for key, label in title_map.items() if key in text]
    if not titles:
        titles = ["Founder", "CEO", "Head of Product"]

    geos: list[str] = []
    geo_map = [
        (("united states", " us", "usa", "u.s"), "United States"),
        (("united kingdom", " uk", "britain"), "United Kingdom"),
        ((" eu", "europe", "germany", "france", "netherlands"), "European Union"),
        (("australia", " aus"), "Australia"),
        (("new zealand", " nz"), "New Zealand"),
        (("canada", " ca "), "Canada"),
        (("india",), "India"),
    ]
    padded = f" {text} "
    for keys, label in geo_map:
        if any(k in padded for k in keys):
            geos.append(label)

    emp_min, emp_max = None, None
    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:employee|people|ppl)?", text)
    if range_match:
        emp_min, emp_max = int(range_match.group(1)), int(range_match.group(2))

    industries: list[str] = []
    for label, keys in [
        ("SaaS", ("saas",)),
        ("Software", ("software",)),
        ("Artificial Intelligence", ("artificial intelligence", " ai ", "machine learning")),
        ("Technology", ("technology", "tech")),
        ("Fintech", ("fintech", "financial")),
        ("Healthcare", ("healthcare", "health tech")),
    ]:
        if any(k in padded for k in keys):
            industries.append(label)
    if not industries:
        industries = ["Software", "Technology"]

    return {
        "titles": titles,
        "seniorities": ["c-level", "founder", "vp", "head", "director"],
        "industries": industries,
        "employee_min": emp_min,
        "employee_max": emp_max,
        "geos": geos,
        "keywords_include": [],
        "keywords_exclude": ["intern", "student", "agency recruiter", "freelance"],
        "company_types": [],
        "pain_points": [],
        "disqualify_rules": ["solo founder with no team", "staffing agency"],
        "best_fit_triggers": [],
        "source": "heuristic_from_target_market",
    }
