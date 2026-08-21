# Deploying Sourcing Desk

One container, one volume. The whole system — API, agents, SQLite, embedded
Qdrant, the frontend — runs as a single service.

## Why one service instead of Next.js on Vercel

The design originally called for a Next.js frontend on Vercel and a separate
backend. It became a single FastAPI service serving a static page, because:

- The frontend is one HTML file with no build step, so a separate deployment
  buys nothing and costs a second thing to keep alive.
- The streaming trace is server-sent events straight from the LangGraph
  stream. Splitting the tiers would add a CORS hop for no benefit.
- One service is one thing to deploy, one URL, one log stream. For an MVP
  demonstrated on a laptop or a small VM, that matters more than tier purity.

Split it later if the frontend grows past a page.

## The constraint that decides the host

Playwright. Headless Chromium wants 2 GB and survives on 1 GB with care.
Everything else here is light.

| Option | Gives | Verdict |
|---|---|---|
| Local machine | everything, no limits | development |
| **e2-small, europe-central2** | 2 GB, ~60 ms from Tashkent, $300 credit | **recommended for the MVP** |
| e2-micro, us-central1 | 1 GB, Always Free forever, ~200 ms | fallback when credit runs out |
| Cloud Run Job for scraping | 2 GB bursts, billed while running | if scraping outgrows the VM |

Always Free is US-regions only, so a Warsaw e2-small on the credit is both
roomier and three times closer.

## Local

```bash
cd backend
.venv/Scripts/python -m uvicorn app.api:app --reload --port 8000
```

Open http://localhost:8000.

## Docker

```bash
cd backend
docker build -t sourcing-desk .
docker run -p 8000:8000 \
  --shm-size=1g --ipc=host \
  -v sourcing-data:/app/data \
  --env-file .env \
  sourcing-desk
```

`--shm-size=1g` and `--ipc=host` are not optional. Docker caps `/dev/shm` at
64 MB by default and Chromium uses it heavily for its render pipeline; without
these the browser rung crashes under load rather than failing cleanly.

## Google Cloud, e2-small

```bash
gcloud compute instances create sourcing-desk \
  --machine-type=e2-small \
  --zone=europe-central2-a \
  --image-family=cos-stable --image-project=cos-cloud \
  --boot-disk-size=30GB \
  --tags=http-server

gcloud compute firewall-rules create allow-sourcing-desk \
  --allow=tcp:8000 --target-tags=http-server
```

Then on the instance:

```bash
docker run -d --restart=unless-stopped -p 8000:8000 \
  --shm-size=1g --ipc=host \
  -v /var/sourcing-data:/app/data \
  --env-file /var/sourcing.env \
  gcr.io/PROJECT_ID/sourcing-desk
```

Put the keys in `/var/sourcing.env` with mode `600`. They never belong in the
image.

## After deploying

```bash
curl https://YOUR_HOST/health
```

```json
{"status":"ok","counts":{"parts":1,"offers":3,"purchases":1,"sources":6,"rfqs":0}}
```

Then seed the database once inside the container:

```bash
docker exec -it CONTAINER python scripts/init_db.py
```

## What is deliberately not exposed

`/rfq/{id}/send` defaults to `dry_run=true`. Sending is opt-in on every call,
and `mailer.send` refuses any draft whose status is not `approved` — there is
no flag that overrides it. An agent reaching real suppliers under someone's
name is the one irreversible action in this system, so it is gated in code
rather than by convention.

## Costs

| Item | Cost |
|---|---|
| GCP e2-small | free on the $300 credit, then ~$13/month |
| GCP e2-micro | free forever, US regions only |
| OpenRouter, gemini-2.5-flash-lite | cents per sourcing run |
| SerpApi | free tier, 250 searches/month |
| FastEmbed + Qdrant | free, runs in the container |

The binding constraint is SerpApi's 250 searches a month, not compute or
model spend. Results are cached by part number for that reason.
