# Deployment

Backend on a Google Cloud VM, frontend on Vercel.

The two halves are independent: the frontend is static files that call the
backend by URL, so either can be redeployed without touching the other.

---

## Part 1 — Backend on the GCP VM

### 1. Open the port

Once per project. The API listens on 8000.

```bash
gcloud compute firewall-rules create allow-sourcing-desk \
  --allow=tcp:8000 \
  --target-tags=sourcing-desk \
  --description="Sourcing Desk API"

gcloud compute instances add-tags YOUR_VM_NAME \
  --tags=sourcing-desk --zone=YOUR_ZONE
```

Note the VM's external IP — you need it in Part 2:

```bash
gcloud compute instances describe YOUR_VM_NAME --zone=YOUR_ZONE \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

### 2. SSH in and install Docker

```bash
gcloud compute ssh YOUR_VM_NAME --zone=YOUR_ZONE
```

```bash
sudo apt-get update && sudo apt-get install -y docker.io git
sudo usermod -aG docker $USER
newgrp docker          # or log out and back in
```

### 3. Clone and configure

```bash
git clone https://github.com/Abduloxunov/procurement-agent.git
cd procurement-agent/backend
cp .env.example .env
nano .env
```

Fill in `OPENROUTER_API_KEY` and `SERPAPI_KEY`. Then lock it down — this file
holds every secret the system has:

```bash
chmod 600 .env
```

### 4. Build and run

```bash
docker build -t sourcing-desk .

docker run -d --name sourcing-desk --restart=unless-stopped \
  -p 8000:8000 \
  --shm-size=1g --ipc=host \
  -v /var/sourcing-data:/app/data \
  --env-file .env \
  sourcing-desk
```

**`--shm-size=1g --ipc=host` are not optional.** Docker caps `/dev/shm` at
64 MB, Chromium uses it heavily for its render pipeline, and without these
the browser rung crashes under load instead of failing cleanly.

**`-v /var/sourcing-data:/app/data`** is what keeps your database. SQLite and
Qdrant are files inside the container; without the volume, every purchase you
log dies with the next `docker run`.

The build takes 5–10 minutes — it installs Chromium and bakes in the
embedding model so the first request is not a two-minute download.

### 5. Initialise the database

Once, on first deploy:

```bash
docker exec -it sourcing-desk python scripts/init_db.py
```

### 6. Check it

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok","counts":{"parts":0,"offers":0,"purchases":0,"sources":6,"rfqs":0}}
```

From your own machine, using the external IP:

```bash
curl http://EXTERNAL_IP:8000/health
```

If that hangs, the firewall rule or the network tag did not apply.

### Updating later

```bash
cd ~/procurement-agent && git pull
cd backend && docker build -t sourcing-desk .
docker rm -f sourcing-desk
docker run -d --name sourcing-desk --restart=unless-stopped \
  -p 8000:8000 --shm-size=1g --ipc=host \
  -v /var/sourcing-data:/app/data --env-file .env sourcing-desk
```

The volume survives, so your data does.

### Logs

```bash
docker logs -f sourcing-desk
```

---

## Part 2 — Frontend on Vercel

### 1. Point it at your backend

Edit `frontend/config.js`, commit, push:

```js
window.API_BASE = "http://EXTERNAL_IP:8000";
```

No trailing slash.

### 2. Deploy

**Via the dashboard:** New Project → import `procurement-agent` → set
**Root Directory** to `frontend` → Deploy. Framework preset: Other. No build
command, no output directory — it is static files.

**Via CLI:**

```bash
cd frontend
npx vercel --prod
```

### 3. Lock CORS to your frontend

The backend defaults to `ALLOWED_ORIGINS=*`. Once you know the Vercel URL,
narrow it:

```bash
# on the VM, in .env
ALLOWED_ORIGINS=https://procurement-agent.vercel.app
```

Then restart the container.

---

## The mixed-content problem

**Vercel serves over https. Your VM serves over http. Browsers block https
pages from calling http APIs**, so the frontend will load and every request
will fail silently in the console.

Three ways out, cheapest first:

**a. Test over http.** Vercel deployments answer on http as well. Fine for a
demo, not for anything real.

**b. Caddy on the VM, with a domain.** If you have a domain, point an A
record at the VM and let Caddy get a certificate automatically:

```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile <<'EOF'
api.yourdomain.com {
    reverse_proxy localhost:8000
}
EOF
sudo systemctl restart caddy
```

Then `window.API_BASE = "https://api.yourdomain.com"`, open port 443, and the
problem is gone.

**c. Serve the frontend from the VM too.** FastAPI already mounts the
`frontend/` directory, so `http://EXTERNAL_IP:8000` serves the whole app with
no CORS and no mixed content at all. Skip Vercel entirely if you would rather
have one URL.

Option (c) is the least work and the most reliable for a demo. Vercel is
worth it when you want a proper URL to hand people.

---

## Verify end to end

1. Open the Vercel URL.
2. The request dropdown populates — the frontend is reaching the backend.
3. Type: *I need 50 FST100-2006A, budget $3000, by March. RS485 is a hard
   requirement.*
4. Press **Start** → a request is created with the budget and deadline parsed.
5. Press **Search Google + Baidu** → offers appear.
6. Ask *"which supplier is cheapest landed?"* → the agent trace streams live.

If step 2 fails, open the browser console. `Failed to fetch` with nothing else
is almost always mixed content; a CORS message means `ALLOWED_ORIGINS` is too
narrow.

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
| GCP e2-small | free on the $300 credit, then ~$13/month |
| Vercel hobby | free |
| OpenRouter, gemini-2.5-flash-lite | cents per sourcing run |
| SerpApi | free tier, 250 searches/month |
| FastEmbed + Qdrant | free, runs in the container |

The binding constraint is **SerpApi's 250 searches a month**, not compute or
model spend. One thorough sourcing run uses two. Results are cached by part
number for that reason.

## Sizing

Playwright is the only component with real requirements — headless Chromium
wants 2 GB and survives on 1 GB with the flags above. Everything else here is
light. An `e2-small` (2 GB) is comfortable; an `e2-micro` (1 GB) works but the
browser rung will be fragile under load.
