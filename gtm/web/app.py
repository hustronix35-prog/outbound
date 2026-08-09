from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from gtm.cost import budget_remaining, today_spend_usd
from gtm.daemon import ensure_default_campaign, run_once
from gtm.models import Campaign, CostEvent, Lead, init_db, get_session

init_db()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="Outbound Agent", version="0.2.0")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    ensure_default_campaign()
    with get_session() as session:
        campaign = session.query(Campaign).filter_by(active=True).first()
        leads = []
        status_counts: dict[str, int] = {}
        if campaign:
            leads = (
                session.query(Lead)
                .filter_by(campaign_id=campaign.id)
                .order_by(Lead.updated_at.desc())
                .limit(100)
                .all()
            )
            for lead in session.query(Lead).filter_by(campaign_id=campaign.id):
                status_counts[lead.status] = status_counts.get(lead.status, 0) + 1
        costs = session.query(CostEvent).order_by(CostEvent.created_at.desc()).limit(20).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "campaign": campaign,
            "leads": leads,
            "status_counts": status_counts,
            "costs": costs,
            "spend": today_spend_usd(),
            "budget_left": budget_remaining(),
        },
    )


@app.post("/run")
def trigger_run():
    cid = ensure_default_campaign()
    run_once(cid)
    return RedirectResponse("/", status_code=303)
