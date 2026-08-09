from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from gtm.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class LeadStatus(str, enum.Enum):
    """Outbound Agent funnel states."""

    DISCOVERED = "discovered"
    COMPANY_QUALIFIED = "company_qualified"
    COMPANY_REJECTED = "company_rejected"
    PROBLEM_FOUND = "problem_found"
    NO_PROBLEM = "no_problem"
    CONTACT_SELECTED = "contact_selected"
    ENRICHING = "enriching"
    NO_EMAIL = "no_email"
    RESEARCHED = "researched"
    READY = "ready"
    SENT = "sent"
    FOLLOW_UP = "follow_up"
    REPLIED = "replied"
    INTERESTED = "interested"
    LATER = "later"
    NO = "no"
    BOOKED = "booked"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUBSCRIBED = "unsubscribed"
    # Legacy aliases kept for older rows
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class ReplyLabel(str, enum.Enum):
    INTERESTED = "interested"
    LATER = "later"
    NO = "no"
    UNSUBSCRIBE = "unsubscribe"
    UNKNOWN = "unknown"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="default")
    product_name: Mapped[str] = mapped_column(String(200))
    product_description: Mapped[str] = mapped_column(Text)
    target_market: Mapped[str] = mapped_column(Text)
    booking_link: Mapped[str] = mapped_column(String(500), default="")
    icp_json: Mapped[str] = mapped_column(Text, default="{}")
    icp_learning_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    leads: Mapped[list[Lead]] = relationship(back_populates="campaign")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    external_id: Mapped[str] = mapped_column(String(200), default="", index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    company: Mapped[str] = mapped_column(String(300), default="")
    company_domain: Mapped[str] = mapped_column(String(300), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    industry: Mapped[str] = mapped_column(String(200), default="")
    employee_count: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(300), default="", index=True)
    email_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(40), default=LeadStatus.DISCOVERED.value, index=True)
    heuristic_score: Mapped[float] = mapped_column(Float, default=0.0)
    fit_score: Mapped[float] = mapped_column(Float, default=0.0)
    company_score: Mapped[float] = mapped_column(Float, default=0.0)
    qualify_reason: Mapped[str] = mapped_column(Text, default="")
    decision_problem: Mapped[str] = mapped_column(Text, default="")
    problem_score: Mapped[float] = mapped_column(Float, default=0.0)
    contact_reason: Mapped[str] = mapped_column(Text, default="")
    company_research: Mapped[str] = mapped_column(Text, default="")
    personalization_hook: Mapped[str] = mapped_column(Text, default="")
    social_signals: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    reply_label: Mapped[str] = mapped_column(String(40), default="")
    thread_id: Mapped[str] = mapped_column(String(200), default="")
    message_id: Mapped[str] = mapped_column(String(300), default="")
    disqualified: Mapped[bool] = mapped_column(Boolean, default=False)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    campaign: Mapped[Campaign] = relationship(back_populates="leads")
    messages: Mapped[list[Message]] = relationship(back_populates="lead")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    direction: Mapped[str] = mapped_column(String(20))  # outbound | inbound
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="messages")


class FeedbackEvent(Base):
    """CRM feedback used for ICP learning."""

    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40))  # interested|later|no|booked|sent|rejected
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CostEvent(Base):
    __tablename__ = "cost_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    units: Mapped[int] = mapped_column(Integer, default=0)
    estimated_usd: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_SessionLocal = None

_LEAD_EXTRA_COLUMNS = {
    "company_score": "FLOAT DEFAULT 0",
    "decision_problem": "TEXT DEFAULT ''",
    "problem_score": "FLOAT DEFAULT 0",
    "contact_reason": "TEXT DEFAULT ''",
    "company_research": "TEXT DEFAULT ''",
    "reply_label": "VARCHAR(40) DEFAULT ''",
    "booked_at": "DATETIME",
}


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        _engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_session():
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    # Lightweight SQLite column add for upgrades
    if str(engine.url).startswith("sqlite"):
        insp = inspect(engine)
        if "leads" in insp.get_table_names():
            existing = {c["name"] for c in insp.get_columns("leads")}
            with engine.begin() as conn:
                for col, ddl in _LEAD_EXTRA_COLUMNS.items():
                    if col not in existing:
                        conn.execute(text(f"ALTER TABLE leads ADD COLUMN {col} {ddl}"))
        if "campaigns" in insp.get_table_names():
            existing = {c["name"] for c in insp.get_columns("campaigns")}
            with engine.begin() as conn:
                if "icp_learning_json" not in existing:
                    conn.execute(
                        text("ALTER TABLE campaigns ADD COLUMN icp_learning_json TEXT DEFAULT '{}'")
                    )
