from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from gtm.config import get_settings
from gtm.cost import today_spend_usd
from gtm.daemon import ensure_default_campaign, run_daemon, run_once
from gtm.models import Campaign, Lead, LeadStatus, init_db, get_session
from gtm.pipeline import (
    classify_replies,
    detect_problems,
    discover_leads,
    enrich_qualified,
    learn_icp,
    personalize_ready,
    push_booking,
    qualify_companies,
    record_crm_snapshot,
    research_companies,
    select_contacts,
    send_ready,
)
from gtm.pipeline.icp import build_or_load_icp

app = typer.Typer(
    help="Outbound Agent — cost-aware GTM automation",
    no_args_is_help=True,
)
console = Console()


@app.command("init")
def init_cmd() -> None:
    """Create DB + default campaign from .env."""
    cid = ensure_default_campaign()
    console.print(f"[green]Ready.[/green] campaign_id={cid}")


@app.command("run")
def run_cmd(
    campaign_id: Optional[int] = typer.Option(None, help="Campaign id"),
    csv: Optional[str] = typer.Option(None, help="Optional CSV lead source"),
) -> None:
    """Run one full pipeline pass."""
    summary = run_once(campaign_id, csv_path=csv)
    console.print_json(json.dumps(summary))


@app.command("daemon")
def daemon_cmd(
    poll: int = typer.Option(120, help="Seconds between loops"),
    csv: Optional[str] = typer.Option(None),
) -> None:
    """Continuously automate the outbound loop."""
    run_daemon(poll_seconds=poll, csv_path=csv)


@app.command("discover")
def discover_cmd(campaign_id: Optional[int] = None, csv: Optional[str] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(f"Market discovery inserted: {discover_leads(cid, csv_path=csv)}")


@app.command("qualify")
def qualify_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(qualify_companies(cid))


@app.command("problems")
def problems_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(detect_problems(cid))


@app.command("contacts")
def contacts_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(select_contacts(cid))


@app.command("enrich")
def enrich_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(enrich_qualified(cid))


@app.command("research")
def research_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(research_companies(cid))


@app.command("personalize")
def personalize_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(f"Personalized: {personalize_ready(cid)}")


@app.command("send")
def send_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(f"Sent: {send_ready(cid)}")


@app.command("classify")
def classify_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(classify_replies(cid))


@app.command("book")
def book_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(push_booking(cid))


@app.command("crm")
def crm_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(record_crm_snapshot(cid))


@app.command("learn")
def learn_cmd(campaign_id: Optional[int] = None) -> None:
    cid = campaign_id or ensure_default_campaign()
    console.print(learn_icp(cid))


@app.command("status")
def status_cmd(campaign_id: Optional[int] = None) -> None:
    init_db()
    settings = get_settings()
    with get_session() as session:
        cid = campaign_id
        if cid is None:
            c = session.query(Campaign).filter_by(active=True).first()
            cid = c.id if c else None
        table = Table(title=f"Outbound funnel (campaign={cid})")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        if cid:
            for status in LeadStatus:
                count = (
                    session.query(Lead)
                    .filter_by(campaign_id=cid, status=status.value)
                    .count()
                )
                if count:
                    table.add_row(status.value, str(count))
        console.print(table)
    console.print(
        f"dry_run={settings.dry_run}  today_spend~=${today_spend_usd():.4f}  "
        f"budget=${settings.daily_budget_usd:.2f}"
    )


@app.command("icp")
def icp_cmd(campaign_id: Optional[int] = None, force: bool = False) -> None:
    cid = campaign_id or ensure_default_campaign()
    with get_session() as session:
        campaign = session.get(Campaign, cid)
        assert campaign
        icp = build_or_load_icp(campaign, force=force)
        campaign.icp_json = json.dumps(icp)
        session.commit()
    console.print_json(json.dumps(icp))


@app.command("mail-test")
def mail_test_cmd(
    to: str = typer.Option(..., help="Recipient email"),
    live: bool = typer.Option(False, help="Actually send (sets DRY_RUN=false for this process)"),
) -> None:
    """Test Outlook/MS Graph or SMTP send."""
    import os

    from gtm.config import get_settings
    from gtm.mail import send_email
    from gtm.mail.graph import get_ms_access_token

    if live:
        os.environ["DRY_RUN"] = "false"
    get_settings.cache_clear()
    settings = get_settings()
    console.print(f"transport={settings.mail_transport} dry_run={settings.dry_run} live={live}")
    if settings.mail_transport == "ms_graph":
        try:
            token = get_ms_access_token(force=True)
            console.print(f"[green]MS Graph token OK[/green] (len={len(token)})")
        except Exception as exc:
            console.print(f"[red]MS Graph auth failed:[/red] {exc}")
            raise typer.Exit(1)
    elif settings.mail_transport == "none":
        console.print("[red]No MS Graph or SMTP configured[/red]")
        raise typer.Exit(1)

    if not live and settings.dry_run:
        console.print("[yellow]DRY_RUN=true — pass --live to send a real test email[/yellow]")
        result = send_email(to=to, subject="Outbound mail test (dry)", body="Dry run only.")
        console.print(f"dry result id={result.message_id}")
        return

    result = send_email(
        to=to,
        subject="Outbound Agent — mail test",
        body=(
            "This is a test email from the Outbound Agent "
            f"via {settings.mail_transport}.\n\n"
            f"From display: {settings.booking_from_name}\n"
        ),
    )
    console.print(f"[green]Sent[/green] dry_run={result.dry_run} id={result.message_id}")


@app.command("telegram-test")
def telegram_test_cmd() -> None:
    """Send a test message to your Telegram chat."""
    from gtm.config import get_settings
    from gtm.notify import get_telegram

    # Reload settings from .env (clear cache if token was just added)
    get_settings.cache_clear()
    tg = get_telegram()
    if not tg.enabled:
        console.print(
            "[red]Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env[/red]"
        )
        raise typer.Exit(1)
    ok = tg.ping()
    if ok:
        console.print("[green]Telegram test message sent.[/green]")
    else:
        console.print("[red]Telegram API call failed — check token/chat id.[/red]")
        raise typer.Exit(1)


@app.command("admin")
def admin_cmd(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Start local web admin."""
    import uvicorn

    settings = get_settings()
    init_db()
    ensure_default_campaign()
    uvicorn.run(
        "gtm.web.app:app",
        host=host or settings.admin_host,
        port=port or settings.admin_port,
        reload=False,
    )


if __name__ == "__main__":
    app()
