# Outbound Agent

**Cost-aware GTM agent for B2B cold email — with an Outbound Intelligence writer.**

```
                 OUTBOUND AGENT

ICP Definition (from YOUR .env)
      ↓
Market Discovery
      ↓
Company Qualification
 ┌────┴───────────────┐
 │                    │
Heuristics          LLM
 │                    │
 └────────┬───────────┘
          ↓
Decision-Problem Detection
          ↓
Contact Selection
          ↓
Email Enrichment
          ↓
Company Research (signal → problem → consequence)
          ↓
Outbound Intelligence (personalized outreach)
          ↓
Send (SMTP or Microsoft Graph)
          ↓
Reply Classification
    ┌─────┼──────┐
    ↓     ↓      ↓
Interested  Later  No
    ↓
Meeting / Booking
    ↓
CRM + Feedback
    ↓
ICP Learning
```

Describe **your** product and ICP in `.env`. The agent discovers the market, qualifies companies (heuristics first, LLM only when borderline), researches prospects, writes personalized outreach via the Outbound Intelligence Agent, sends via your mailbox, classifies replies, books meetings for interested leads, and feeds outcomes back into ICP learning — under a daily spend cap.

Your product description, ICP, API keys, and mailbox credentials stay in **local `.env` only** (gitignored). They are not shipped in this repo.

---

## Quick start

```bash
git clone https://github.com/hustronix35-prog/outbound.git
cd outbound

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
cp .env.example .env   # Windows: copy .env.example .env
```

Fill `.env` with your product, ICP, LLM key, and optional providers. Keep `DRY_RUN=true` until copy looks right.

```bash
gtm init
gtm run       # full pipeline pass
gtm status
gtm admin     # http://127.0.0.1:8080
gtm daemon --poll 120
```

---

## Outbound Intelligence

The personalize stage is a research → relevance → write agent:

1. Use only **verified** prospect signals  
2. Infer a plausible operational problem (no invented pain)  
3. Skip the lead if the product is not genuinely relevant  
4. Write a short founder-tone email with a soft CTA + optional booking link  

Configure via `PRODUCT_*`, `TARGET_MARKET`, `SENDER_*`, `BOOKING_LINK`, `PRODUCT_WEBSITE`.

---

## Pipeline stages

| Stage | What it does | Cost posture |
|---|---|---|
| **ICP Definition** | Product + target → filter JSON (cached) | LLM once / heuristic from your target text |
| **Market Discovery** | BetterContact / Apollo / CSV / mock | Provider APIs only |
| **Company Qualification** | Fit gate | Heuristics first; LLM mid-band only |
| **Decision-Problem Detection** | Buying problem signal | Keywords / cheap LLM |
| **Contact Selection** | ICP title match | Free |
| **Email Enrichment** | Work email resolve | Paid only after contact select |
| **Company Research** | Site sniff + signal chain | Free fetch; optional cheap LLM |
| **Outbound Intelligence** | Relevance + personalized email | Cheap model |
| **Send** | Your mailbox | Cap per day |
| **Reply Classification** | Interested / Later / No | Heuristic or cheap LLM |
| **Meeting / Booking** | Calendar CTA to interested | Free + mail |
| **CRM + Feedback** | Funnel + outcome log | Free |
| **ICP Learning** | Boost/avoid titles & industries | Free merge into ICP |

---

## Configure

| Need | Env |
|---|---|
| Product / ICP | `PRODUCT_*`, `TARGET_MARKET`, `BOOKING_LINK`, `PRODUCT_WEBSITE`, `SENDER_*` |
| LLM | `LLM_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL_CHEAP` |
| Discovery / email | `BETTERCONTACT_API_KEY` and/or `APOLLO_API_KEY` + `HUNTER_API_KEY` or `data/leads.csv` |
| Mailbox | `MS_*` (Outlook Graph) or `SMTP_*` / `IMAP_*` |
| Telegram reports | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Budget | `DAILY_BUDGET_USD`, `MAX_EMAIL_LOOKUPS_PER_DAY`, `MAX_SENDS_PER_DAY` |

### Outlook / Microsoft Graph

```env
MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
MS_TENANT_ID=...
MS_REFRESH_TOKEN=...
MS_MAILBOX=you@example.com
```

```bash
gtm mail-test --to you@example.com          # auth + dry run
gtm mail-test --to you@example.com --live   # real send
```

**Permissions:** `Mail.Send` is enough for outreach. Grant `Mail.Read` if you want automatic reply reading.

### Telegram setup

1. [@BotFather](https://t.me/BotFather) → `/newbot` → copy token  
2. Message your bot once  
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id`  
4. `gtm telegram-test`

See [`.env.example`](./.env.example).

---

## CLI

```text
gtm init | run | daemon | status | admin | icp | telegram-test | mail-test
gtm discover | qualify | problems | contacts | enrich
gtm research | personalize | send | classify | book | crm | learn
```

---

## Docker

```bash
docker compose up --build
docker compose --profile admin up --build admin
```

---

## Compliance

You are the sender of record. Follow CAN-SPAM / GDPR / CASL, honor unsubscribes, and only contact people with a lawful basis.

## License

[MIT](./LICENSE)
