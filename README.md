# Procurement Agent — Sourcing Desk

A multi-agent system for sourcing industrial parts. Give it a part number and
a quantity; it searches Google and Baidu, reads supplier pages, and returns a
comparison table with **landed cost in Tashkent** — currency converted,
incoterms applied, freight, customs duty and VAT included.

> A $45 sticker price is a **$68.55** part once it reaches Tashkent. Ranking
> on listed price picks the wrong supplier. That gap is the whole product.

```
supplier                        listed incoterm   freight  duty+vat   LANDED/u  uplift
Shenzhen Tiancheng Trading     260 CNY EXW         250.00    764.88      60.15     56%
Hunan Firstrate Sensor          40 USD EXW?        250.00    787.76      61.96     55%
Tashkent Instrument Supply      71 USD DDP           0.00      0.00      71.71      1%
```

## What it does

- **Searches two webs.** Google for the English supplier web, Baidu for the
  Chinese industrial web — which surfaces manufacturers Google never returns.
- **Reads pages three ways.** Plain fetch, then a headless browser, then
  manual paste for anything CAPTCHA-walled. Every row records which rung
  produced it.
- **Computes landed cost.** Currency, incoterm, freight by mode, duty by HS
  code, 12% VAT. Arithmetic runs in Python, never in tokens.
- **Shows how the winner changes with quantity.** At one unit buying locally
  is cheapest; at volume the Chinese quote wins.
- **Checks substitutes.** Parameter by parameter, separating hard blockers
  (RS485 vs 4–20 mA) from trade-offs from *not stated — ask, do not assume*.
- **Remembers.** Tell it what you bought in one sentence; it completes the
  record and uses it to sharpen future comparisons.
- **Asks suppliers.** Drafts RFQ emails for anyone who publishes no price.
  Nothing sends without approval.

## Architecture

Five specialists behind a supervisor, with a critic that verifies every
answer before it is shown.

```
question -> supervisor -> [part | scout | history | analyst] -> supervisor
                 |
              finish -> generate -> critic -> answer
                             ^         |
                             +-- revise+
```

| Agent | Does |
|---|---|
| **Scout** | Search, rank by source weight, scrape, extract offers |
| **Part** | Datasheet retrieval, including spec tables read from images |
| **History** | Text-to-SQL over what we actually paid |
| **Analyst** | Landed cost, comparison, quantity sensitivity |
| **RFQ** | Draft supplier emails, parse the replies |

Built with LangGraph, FastAPI, Qdrant, SQLite, and one model throughout:
`gemini-2.5-flash-lite` via OpenRouter. Embeddings run locally with FastEmbed
— no key, no cost, no rate limit.

## Quick start

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m playwright install chromium
cp .env.example .env            # add your OpenRouter key
.venv/Scripts/python scripts/init_db.py
.venv/Scripts/python -m uvicorn app.api:app --port 8000
```

Open http://localhost:8000 and type what you need:

> I need 50 FST100-2006A, budget $3000, by March. RS485 is a hard requirement.

## Documentation

- [backend/README.md](backend/README.md) — how it works, and the bugs worth knowing about
- [docs/deploy.md](docs/deploy.md) — GCP VM plus Vercel, step by step
- [docs/sourcing-desk-design.html](docs/sourcing-desk-design.html) — the design document

## Repository

```
backend/    agents, analysis, API, evaluation harness
frontend/   one HTML file — served by FastAPI locally, by Vercel in production
docs/       design document and deployment guide
```
