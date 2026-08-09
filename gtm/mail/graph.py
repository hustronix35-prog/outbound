from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from gtm.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def ms_graph_configured() -> bool:
    s = get_settings()
    return bool(s.ms_client_id and s.ms_client_secret and s.ms_tenant_id and s.ms_refresh_token)


def get_ms_access_token(force: bool = False) -> str:
    """Exchange refresh token for a Graph access token."""
    now = time.time()
    if not force and _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return str(_TOKEN_CACHE["access_token"])

    s = get_settings()
    if not ms_graph_configured():
        raise RuntimeError("Microsoft Graph credentials are not configured")

    url = f"https://login.microsoftonline.com/{s.ms_tenant_id}/oauth2/v2.0/token"
    base = {
        "client_id": s.ms_client_id,
        "client_secret": s.ms_client_secret,
        "refresh_token": s.ms_refresh_token,
        "grant_type": "refresh_token",
    }
    # This app currently has consent for Mail.Send (+ User.Read), not Mail.Read.
    # Prefer scopes that match consent; avoid requesting unconsented Mail.Read.
    scope_attempts: list[dict[str, str]] = [
        {"scope": "https://graph.microsoft.com/.default"},
        {
            "scope": (
                "https://graph.microsoft.com/Mail.Send "
                "https://graph.microsoft.com/User.Read "
                "offline_access openid profile"
            )
        },
        {},  # omit scope
    ]

    last_err = ""
    payload: dict[str, Any] = {}
    with httpx.Client(timeout=30.0) as client:
        for extra in scope_attempts:
            data = {**base, **extra}
            resp = client.post(url, data=data)
            if resp.status_code < 400:
                payload = resp.json()
                break
            try:
                err = resp.json()
            except Exception:
                err = {"error_description": resp.text[:400]}
            last_err = f"{err.get('error')}: {err.get('error_description') or resp.text[:300]}"
            logger.warning("MS token refresh attempt failed: %s", last_err[:240])
        else:
            raise RuntimeError(f"Microsoft token refresh failed — {last_err}")

    access = payload.get("access_token") or ""
    if not access:
        raise RuntimeError(f"No access_token in MS auth response: {payload}")
    expires_in = int(payload.get("expires_in") or 3600)
    _TOKEN_CACHE["access_token"] = access
    _TOKEN_CACHE["expires_at"] = now + expires_in

    new_refresh = payload.get("refresh_token")
    if new_refresh and new_refresh != s.ms_refresh_token:
        _persist_refresh_token(new_refresh)
        get_settings.cache_clear()
        logger.info("Microsoft refresh token rotated and saved to .env")

    return access


def _persist_refresh_token(new_token: str) -> None:
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("MS_REFRESH_TOKEN="):
            out.append(f"MS_REFRESH_TOKEN={new_token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"MS_REFRESH_TOKEN={new_token}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def graph_send_mail(
    *,
    to: str,
    subject: str,
    body: str,
    from_name: str = "",
    in_reply_to: str = "",
) -> str:
    """Send mail via Microsoft Graph. Returns Graph internet message id if available."""
    s = get_settings()
    token = get_ms_access_token()
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }
    if from_name:
        # From is the authenticated mailbox; display name can be set on reply/from
        message["from"] = {
            "emailAddress": {
                "address": s.ms_mailbox or s.smtp_from or "you@example.com",
                "name": from_name,
            }
        }
    if in_reply_to:
        message["internetMessageHeaders"] = [
            {"name": "In-Reply-To", "value": in_reply_to},
            {"name": "References", "value": in_reply_to},
        ]

    payload = {"message": message, "saveToSentItems": True}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=45.0) as client:
        resp = client.post("https://graph.microsoft.com/v1.0/me/sendMail", headers=headers, json=payload)
        if resp.status_code == 401:
            token = get_ms_access_token(force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail", headers=headers, json=payload
            )
        if resp.status_code >= 400:
            # Fallback: send as specific user if /me is blocked
            mailbox = s.ms_mailbox or s.smtp_from
            if mailbox:
                resp = client.post(
                    f"https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail",
                    headers=headers,
                    json=payload,
                )
        resp.raise_for_status()

    # sendMail returns 202 with empty body — synthesize an id
    return f"graph-{int(time.time())}-{abs(hash(to + subject)) % 10_000_000}"


def graph_fetch_unseen(limit: int = 20) -> list[dict[str, str]]:
    """Fetch recent inbox messages via Graph (unread preferred).

    Requires Mail.Read consent. If missing, returns [] and logs a warning.
    """
    try:
        token = get_ms_access_token()
    except Exception as exc:
        logger.warning("Graph inbox skipped (auth): %s", exc)
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$top": str(limit),
        "$orderby": "receivedDateTime desc",
        "$select": "subject,bodyPreview,body,from,internetMessageId,conversationId,isRead",
        "$filter": "isRead eq false",
    }
    with httpx.Client(timeout=45.0) as client:
        resp = client.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            headers=headers,
            params=params,
        )
        if resp.status_code == 401:
            token = get_ms_access_token(force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = client.get(
                "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                headers=headers,
                params=params,
            )
        if resp.status_code == 403:
            logger.warning(
                "Graph inbox forbidden — grant Mail.Read to the Azure app "
                "'Graph mail' and re-consent, then retry."
            )
            return []
        if resp.status_code >= 400:
            params.pop("$filter", None)
            resp = client.get(
                "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                headers=headers,
                params=params,
            )
        if resp.status_code >= 400:
            logger.warning("Graph inbox fetch failed: %s %s", resp.status_code, resp.text[:240])
            return []
        rows = (resp.json() or {}).get("value") or []

    out: list[dict[str, str]] = []
    for row in rows:
        from_obj = ((row.get("from") or {}).get("emailAddress") or {})
        body_obj = row.get("body") or {}
        content = body_obj.get("content") or row.get("bodyPreview") or ""
        out.append(
            {
                "from_addr": from_obj.get("address") or "",
                "subject": row.get("subject") or "",
                "body": str(content)[:5000],
                "message_id": row.get("internetMessageId") or row.get("id") or "",
                "in_reply_to": "",
                "graph_id": row.get("id") or "",
            }
        )
    return out
