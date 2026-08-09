from __future__ import annotations

import imaplib
import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from email.utils import make_msgid

from gtm.config import get_settings
from gtm.mail.graph import graph_fetch_unseen, graph_send_mail, ms_graph_configured

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    message_id: str
    dry_run: bool


def send_email(*, to: str, subject: str, body: str, in_reply_to: str = "") -> SendResult:
    settings = get_settings()
    message_id = make_msgid()

    if settings.dry_run:
        logger.info("[DRY RUN] To=%s Subject=%s", to, subject)
        return SendResult(message_id=message_id or f"dry-{uuid.uuid4()}", dry_run=True)

    # Prefer Microsoft Graph / Outlook when configured
    if ms_graph_configured():
        mid = graph_send_mail(
            to=to,
            subject=subject,
            body=body,
            from_name=settings.booking_from_name or settings.sender_name,
            in_reply_to=in_reply_to,
        )
        return SendResult(message_id=mid or message_id, dry_run=False)

    msg = EmailMessage()
    from_addr = settings.smtp_from or settings.smtp_user
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    if not settings.smtp_user:
        raise RuntimeError("No mail transport configured (MS Graph or SMTP)")

    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    return SendResult(message_id=message_id, dry_run=False)


@dataclass
class InboundMail:
    from_addr: str
    subject: str
    body: str
    message_id: str
    in_reply_to: str


def fetch_unseen(limit: int = 20) -> list[InboundMail]:
    settings = get_settings()
    if settings.dry_run:
        return []

    if ms_graph_configured():
        rows = graph_fetch_unseen(limit=limit)
        return [
            InboundMail(
                from_addr=r.get("from_addr") or "",
                subject=r.get("subject") or "",
                body=r.get("body") or "",
                message_id=r.get("message_id") or "",
                in_reply_to=r.get("in_reply_to") or "",
            )
            for r in rows
        ]

    user = settings.imap_user or settings.smtp_user
    password = settings.imap_password or settings.smtp_password
    if not user or not password:
        return []

    results: list[InboundMail] = []
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK":
            return []
        ids = data[0].split()[-limit:]
        for num in ids:
            typ, msg_data = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            parsed = BytesParser(policy=default).parsebytes(raw)
            body = ""
            if parsed.is_multipart():
                for part in parsed.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_content()
                        break
            else:
                body = parsed.get_content()
            results.append(
                InboundMail(
                    from_addr=str(parsed.get("From") or ""),
                    subject=str(parsed.get("Subject") or ""),
                    body=str(body or "")[:5000],
                    message_id=str(parsed.get("Message-ID") or ""),
                    in_reply_to=str(parsed.get("In-Reply-To") or ""),
                )
            )
    return results
