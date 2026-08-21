# Deploying — Render + Vercel

Backend on Render, frontend on Vercel. Both free.

The two halves are independent: the frontend is static files that call the
backend by URL, so either can be redeployed without touching the other.

---

## Read this first — three free-tier limits

None of them break the project, but two of them will embarrass you in a demo
if you meet them for the first time on stage.

### 1. 512 MB of RAM — the browser rung cannot run

Render's free instance has 512 MB. Headless Chromium wants 1–2 GB. So the
Playwright rung is **local-only**, and the deployed extraction ladder is:

```
direct fetch  →  manual paste  →  RFQ email
```

The code already handles this. A page it cannot read returns a clear failure
with a reason; it never invents a price. Say this plainly if asked — it is a
hosting limit you designed around, not a missing feature.

This is also why the blueprint uses Render's **native Python runtime instead
of the Docker image**. The Dockerfile installs Chromium, which would only add
~400 MB to a build that could never launch it.

### 2. The instance sleeps after 15 minutes — cold start is about a minute

**Warm it up before you present.** Open the API URL five minutes before you
start:

```bash
curl https://YOUR-SERVICE.onrender.com/health
```

If you skip this, your first click on stage hangs for a minute with no
explanation.

### 3. No persistent disk — the database resets on every restart

SQLite and Qdrant are files, and the free plan wipes the filesystem whenever
the instance restarts or redeploys. So:

- `python scripts/init_db.py` runs on **every** boot (it is in the start
  command, and it is re-runnable and non-destructive)
- Purchases you log against the deployed instance **will not survive** a
  restart
- Anything you want present during the demo has to be created during the demo,
  or seeded at startup

For durable data, a paid disk is $0.25/GB per month — or run the backend on
your GCP VM instead, where the Docker image with Chromium works fully.

---

## Part 1 — Backend on Render

### Step 1 · Create the service

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect the GitHub repo `Abduloxunov/procurement-agent`
3. Render reads `render.yaml` from the repo root and proposes one web service
4. Click **Apply**

It will ask for the three secrets marked `sync: false`. Everything else — the
Python version, the model names, the VAT and duty rates — is already in the
blueprint.

### Step 2 · Fill in the secrets

| Key | Value |
|---|---|
| `OPENROUTER_API_KEY` | your key from openrouter.ai/keys |
| `SERPAPI_KEY` | your key from serpapi.com |
| `ALLOWED_ORIGINS` | `*` for now — narrow it in Step 6 |

### Step 3 · Wait for the first build

Three to five minutes. Most of it is installing `onnxruntime` and `fastembed`.
Watch the log for `Application startup complete.`

### Step 4 · Check it

```bash
curl https://YOUR-SERVICE.onrender.com/health
```

```json
{"status":"ok","counts":{"parts":0,"offers":0,"purchases":0,"sources":6,"rfqs":0}}
```

`sources: 6` proves `init_db.py` ran and seeded the registry. If you get
`sources: 0`, the start command failed — check the logs.

**If the service restarts repeatedly with no error**, it ran out of memory.
The embedding model loads lazily on the first datasheet question, and that is
the moment most likely to exceed 512 MB. Two ways out: skip datasheet
questions in the demo, or move the backend to your GCP VM.

---

## Part 2 — Frontend on Vercel

### Step 5 · Point the frontend at the backend

Edit `frontend/config.js`, commit, push:

```js
window.API_BASE = "https://YOUR-SERVICE.onrender.com";
```

No trailing slash. **Both are https**, so there is no mixed-content problem —
this is the main reason Render is easier than a bare VM here.

### Step 6 · Deploy

**Dashboard:** [vercel.com/new](https://vercel.com/new) → import the repo →
set **Root Directory** to `frontend` → Framework Preset **Other** → Deploy.
No build command, no output directory — it is static files.

**CLI:**

```bash
cd frontend
npx vercel --prod
```

### Step 7 · Lock CORS down

Back in Render → Environment → set:

```
ALLOWED_ORIGINS=https://procurement-agent.vercel.app
```

Use your real Vercel URL. Save; Render restarts automatically.

---

## Verify end to end

1. Open the Vercel URL
2. The request dropdown loads — the frontend is reaching Render
3. Type: *I need 50 FST100-2006A, budget $3000, by March. RS485 is a hard requirement.*
4. **Start** → a request is created with the budget and deadline parsed out
5. **Search — quick** → offers appear
6. Ask *"which supplier is cheapest landed?"* → the agent trace streams live

If step 2 fails, open the browser console:

| Symptom | Cause |
|---|---|
| Request hangs ~60s then works | Cold start. Expected. Warm it first. |
| `Failed to fetch`, nothing else | `API_BASE` wrong, or the service is down |
| A CORS message | `ALLOWED_ORIGINS` does not match the Vercel URL exactly |
| `sources: 0` in /health | `init_db.py` did not run |

---

## Demo checklist

Do these in order, five minutes before you present.

- [ ] `curl .../health` to wake the instance — **do not skip this**
- [ ] Open the Vercel URL and confirm the dropdown loads
- [ ] Create one request and run a search, so there is data on screen
- [ ] Leave the tab open — 15 minutes of idle puts it back to sleep

---

## What is deliberately not exposed

`/rfq/{id}/send` defaults to `dry_run=true`, and `mailer.send` refuses any
draft not marked `approved`. There is no flag that overrides it and no other
function that sends.

An agent emailing real suppliers under someone's name is the one irreversible
action in this system, so it is gated in code rather than by convention.

---

## Costs

| Item | Cost |
|---|---|
| Render web service | free (512 MB, sleeps after 15 min) |
| Vercel hobby | free |
| OpenRouter, gemini-2.5-flash-lite | cents per sourcing run |
| SerpApi | free, 250 searches/month |
| FastEmbed + Qdrant | free, runs in the instance |

The binding constraint is **SerpApi's 250 searches a month**, not compute.
One thorough run uses two, so results are cached by part number.

---

## Alternative — the GCP VM

If Render's 512 MB proves too tight, or you want the browser rung working in
the deployed build, the Docker image runs fully on a small VM:

```bash
docker build -t procurement-agent .
docker run -d --restart=unless-stopped -p 8000:8000 \
  --shm-size=1g --ipc=host \
  -v /var/procurement-data:/app/data \
  --env-file .env \
  procurement-agent
```

`--shm-size=1g --ipc=host` are not optional — Docker caps `/dev/shm` at 64 MB
and Chromium crashes without them.

The trade-off: the VM serves http, and a Vercel frontend on https cannot call
an http API. Either put Caddy in front for a certificate, or serve the
frontend from the VM too — FastAPI already mounts the `frontend/` directory,
so `http://YOUR_IP:8000` gives you the whole app on one origin.
