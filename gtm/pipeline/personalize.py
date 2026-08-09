from __future__ import annotations

import json
import logging
import re

import httpx
from sqlalchemy import select

from gtm.config import get_settings
from gtm.cost import SOCIAL_COST_USD, can_spend, record_cost
from gtm.llm import BudgetExceeded, get_llm
from gtm.llm.prompts import build_outbound_intelligence_system
from gtm.models import Campaign, Lead, LeadStatus, get_session

logger = logging.getLogger(__name__)


def _fetch_social_signals(lead: Lead) -> str:
    """Optional Apify enrichment for top leads only — skipped when no token/budget."""
    settings = get_settings()
    if not settings.apify_token or not lead.linkedin_url:
        return ""
    if not can_spend(SOCIAL_COST_USD):
        return ""
    try:
        with httpx.Client(timeout=90.0) as client:
            run = client.post(
                "https://api.apify.com/v2/acts/apify~linkedin-profile-scraper/runs",
                params={"token": settings.apify_token, "waitForFinish": 60},
                json={"startUrls": [{"url": lead.linkedin_url}]},
            )
            if run.status_code >= 400:
                return ""
            data = run.json().get("data") or {}
            dataset_id = data.get("defaultDatasetId")
            if not dataset_id:
                return ""
            items = client.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                params={"token": settings.apify_token, "limit": 1},
            ).json()
        record_cost(
            kind="social",
            estimated_usd=SOCIAL_COST_USD,
            provider="apify",
            units=1,
            note="linkedin_profile",
        )
        if not items:
            return ""
        item = items[0]
        bits = [
            item.get("headline") or "",
            item.get("summary") or item.get("about") or "",
            " | ".join((item.get("recentPosts") or [])[:1])
            if isinstance(item.get("recentPosts"), list)
            else "",
        ]
        return " ".join(b for b in bits if b)[:800]
    except Exception as exc:
        logger.debug("Social enrichment skipped: %s", exc)
        return ""


def _pick_subject(result: dict, company: str) -> str:
    subject = str(result.get("subject") or result.get("recommended_subject") or "").strip()
    if subject:
        return subject[:500]
    opts = result.get("subject_options") or result.get("alternative_subjects") or []
    if isinstance(opts, list) and opts:
        return str(opts[0])[:500]
    return f"One thought on {company}"[:500]


def _build_hook(result: dict, fallback: str) -> str:
    parts = [
        str(result.get("hook") or "").strip(),
        str(result.get("research_signal_used") or result.get("personalization_source") or "").strip(),
        str(result.get("pain_hypothesis") or "").strip(),
        str(
            result.get("why_product_is_relevant")
            or result.get("why_this_angle")
            or ""
        ).strip(),
        f"confidence={result.get('confidence')}" if result.get("confidence") else "",
    ]
    parts = [p for p in parts if p]
    if parts:
        return " | ".join(dict.fromkeys(parts))[:2000]
    return fallback or ""


def _ensure_booking_link(body: str, booking_link: str, confidence: str) -> str:
    if not booking_link or not body:
        return body
    conf = (confidence or "").strip().lower()
    if conf == "low":
        return body
    if booking_link in body:
        return body
    cleaned = re.sub(r"https?://cal\.com/\S+", "", body).rstrip()
    return f"{cleaned}\n\nIf this is relevant, here's my calendar:\n{booking_link}"


def _parse_research_blob(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"summary": raw}
        except json.JSONDecodeError:
            return {"summary": raw}
    return {"summary": raw}


def _sender_first(name: str) -> str:
    name = (name or "Founder").strip()
    return name.split(",")[0].strip().split(" ")[0] or "Founder"


def personalize_ready(campaign_id: int, limit: int = 30) -> int:
    """Outbound Intelligence Agent — relevance gate + personalized email."""
    settings = get_settings()
    count = 0
    skipped = 0
    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError("campaign not found")

        product_name = (campaign.product_name or settings.product_name or "Your Product").strip()
        product_description = (
            campaign.product_description or settings.product_description or ""
        ).strip()
        target_market = (campaign.target_market or settings.target_market or "").strip()
        booking_link = (campaign.booking_link or settings.booking_link or "").strip()
        website = (settings.product_website or "").strip()
        sender_name = (settings.sender_name or "Founder").strip()
        sender_title = (settings.sender_title or "Founder & CEO").strip()
        sender_sign = _sender_first(sender_name)

        system_prompt = build_outbound_intelligence_system(
            product_name=product_name,
            product_description=product_description,
            sender_name=sender_sign,
            sender_title=sender_title,
            website=website,
            booking_link=booking_link,
            target_market=target_market,
        )

        leads = session.scalars(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.RESEARCHED.value,
                Lead.body == "",
                Lead.email != "",
            )
            .order_by(Lead.fit_score.desc())
            .limit(limit)
        ).all()

        llm = get_llm() if settings.llm_api_key else None

        for idx, lead in enumerate(leads):
            if idx < settings.personalize_top_n_social:
                signals = _fetch_social_signals(lead)
                if signals:
                    lead.social_signals = signals

            first = lead.first_name or (lead.full_name.split(" ")[0] if lead.full_name else "there")
            research = _parse_research_blob(lead.company_research)
            hook_ctx = (
                lead.personalization_hook
                or lead.decision_problem
                or research.get("likely_problem")
                or lead.qualify_reason
            )

            if llm is None:
                lead.subject = f"One thought on {lead.company}"
                body = (
                    f"Hey {first},\n\n"
                    f"At a company like {lead.company}, the expensive problems usually aren't "
                    f"inside one team — they're the dependencies across functions that no single "
                    f"tool surfaces early.\n\n"
                    f"That's the problem we're focused on at {product_name}.\n\n"
                    f"Curious — is this something you're already dealing with?"
                )
                if booking_link:
                    body += f"\n\nIf this is relevant, here's my calendar:\n{booking_link}"
                body += f"\n\nBest,\n{sender_sign}\n{sender_title}\n{product_name}"
                lead.body = body
                lead.personalization_hook = str(hook_ctx or "")
                lead.status = LeadStatus.READY.value
                count += 1
                continue

            payload = {
                "product_website": website,
                "booking_link": booking_link,
                "product_name": product_name,
                "product_description": product_description,
                "target_market": target_market,
                "sender_name": sender_sign,
                "sender_title": sender_title,
                "instruction": (
                    f"Answer why THIS person would care about {product_name} NOW. "
                    "If not relevant, set relevant=false and body=''. "
                    "Use booking_link exactly when confidence is High or Medium."
                ),
                "verified_facts_only": {
                    "first_name": first,
                    "full_name": lead.full_name,
                    "title": lead.title,
                    "company": lead.company,
                    "company_domain": lead.company_domain,
                    "industry": lead.industry or None,
                    "location": lead.location or None,
                    "employee_count": lead.employee_count or None,
                    "linkedin_url": lead.linkedin_url or None,
                    "decision_problem": lead.decision_problem or None,
                    "qualify_reason": lead.qualify_reason or None,
                    "social_signals": lead.social_signals or None,
                    "prior_research": research,
                    "hook_context": hook_ctx or None,
                },
            }
            payload["verified_facts_only"] = {
                k: v for k, v in payload["verified_facts_only"].items() if v not in (None, "")
            }

            try:
                result = llm.chat_json(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    tier="cheap",
                    temperature=0.5,
                    max_tokens=1100,
                )
            except BudgetExceeded:
                logger.warning("Budget hit during Outbound Intelligence — stopping batch")
                break
            except Exception as exc:
                logger.warning("Intelligence agent failed lead=%s: %s", lead.id, exc)
                continue

            relevant = result.get("relevant", True)
            confidence = str(result.get("confidence") or "Medium")
            if relevant is False or confidence.lower() == "low":
                lead.personalization_hook = _build_hook(result, hook_ctx or "")
                lead.status = LeadStatus.NO_PROBLEM.value
                skipped += 1
                logger.info(
                    "Skipped lead=%s relevant=%s confidence=%s",
                    lead.id,
                    relevant,
                    confidence,
                )
                continue

            body = str(result.get("body") or "").strip()
            if not body:
                lead.personalization_hook = _build_hook(result, hook_ctx or "")
                lead.status = LeadStatus.NO_PROBLEM.value
                skipped += 1
                continue

            lead.subject = _pick_subject(result, lead.company or "your team")
            lead.body = _ensure_booking_link(body, booking_link, confidence)
            lead.personalization_hook = _build_hook(result, hook_ctx or "")
            lead.status = LeadStatus.READY.value
            count += 1

        session.commit()
    logger.info("Outbound Intelligence personalized=%s skipped=%s", count, skipped)
    return count
