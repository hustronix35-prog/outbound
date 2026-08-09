from __future__ import annotations

import logging
from typing import Any

import httpx

from gtm.config import get_settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Lightweight Telegram Bot API reporter for outbound events."""

    def __init__(self) -> None:
        s = get_settings()
        self.token = (s.telegram_bot_token or "").strip()
        self.chat_id = (s.telegram_chat_id or "").strip()
        self.notify_sends = s.telegram_notify_sends
        self.notify_replies = s.telegram_notify_replies
        self.notify_bookings = s.telegram_notify_bookings
        self.notify_summary = s.telegram_notify_summary

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, *, disable_preview: bool = True) -> bool:
        if not self.enabled:
            logger.debug("Telegram not configured — skip notify")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": disable_preview,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:300])
                    return False
            return True
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    def ping(self) -> bool:
        return self.send(
            "Outbound Agent connected.\n"
            "You will get reach / reply / booking / run reports here."
        )

    def report_send(self, lead: Any, *, dry_run: bool) -> None:
        if not self.notify_sends:
            return
        mode = "DRY RUN" if dry_run else "SENT"
        self.send(
            f"Outbound · {mode}\n"
            f"Who: {lead.full_name or '—'}\n"
            f"Title: {lead.title or '—'}\n"
            f"Company: {lead.company or '—'}\n"
            f"Email: {lead.email or '—'}\n"
            f"Fit: {getattr(lead, 'fit_score', 0):.2f}\n"
            f"Problem: {(lead.decision_problem or '—')[:180]}\n"
            f"Subject: {(lead.subject or '—')[:120]}"
        )

    def report_reply(self, lead: Any, label: str, excerpt: str = "") -> None:
        if not self.notify_replies:
            return
        self.send(
            f"Outbound · REPLY · {label.upper()}\n"
            f"Who: {lead.full_name or '—'} @ {lead.company or '—'}\n"
            f"Email: {lead.email or '—'}\n"
            f"Excerpt: {(excerpt or '—')[:280]}"
        )

    def report_booking(self, lead: Any, link: str) -> None:
        if not self.notify_bookings:
            return
        self.send(
            f"Outbound · BOOKING LINK SENT\n"
            f"Who: {lead.full_name or '—'} @ {lead.company or '—'}\n"
            f"Email: {lead.email or '—'}\n"
            f"Link: {link}"
        )

    def report_run_summary(self, summary: dict[str, Any]) -> None:
        if not self.notify_summary:
            return
        funnel = (summary.get("crm_feedback") or {}).get("funnel") or {}
        funnel_line = ", ".join(f"{k}={v}" for k, v in sorted(funnel.items())[:12]) or "—"
        classify = summary.get("reply_classification") or {}
        self.send(
            "Outbound · RUN SUMMARY\n"
            f"Campaign: {summary.get('campaign_id')}\n"
            f"Discovered: {summary.get('market_discovery')}\n"
            f"Company qualify: {summary.get('company_qualification')}\n"
            f"Problems: {summary.get('decision_problem_detection')}\n"
            f"Contacts: {summary.get('contact_selection')}\n"
            f"Enriched: {summary.get('email_enrichment')}\n"
            f"Researched: {summary.get('company_research')}\n"
            f"Personalized: {summary.get('personalized_outreach')}\n"
            f"Sent: {summary.get('smtp_send')}\n"
            f"Replies in: {summary.get('replies_collected')}\n"
            f"Classify: {classify}\n"
            f"Bookings: {summary.get('meeting_booking')}\n"
            f"Funnel: {funnel_line}\n"
            f"Spend: ${float(summary.get('spend_usd') or 0):.4f} "
            f"(left ${float(summary.get('budget_remaining_usd') or 0):.2f})"
        )


def get_telegram() -> TelegramNotifier:
    return TelegramNotifier()
