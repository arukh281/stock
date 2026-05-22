# Paper Trading Sandbox

Hosted multi-algo paper trading: four isolated portfolios in Supabase, FastAPI worker, Next.js dashboard.

## Algos

| `algo_id` | EOD endpoint | Source |
|-----------|--------------|--------|
| `44ma` | `POST /analyze/44ma` | `44ma/ma44/paper.py` · **full ladder** (`config.json`) |
| `44ma_stacked_2ma` | `POST /analyze/44ma-stacked-2ma` | `44ma/ma44/paper.py` · **stacked 2MA** (`config.stacked_2ma.json`) |
| `financially_free` | `POST /analyze/financially-free` | `financially free/daily_paper_step.py` (Midcap 150). Entries are **pending** at the 20D breakout pivot; a position opens only when a later daily bar’s **high ≥ trigger** (same model as `44ma/ma44/paper.py`). |
| `kali` | `POST /analyze/kali` | `KALI/src/kali/live/daily_reconcile.py` |

Run each **after NSE cash close (IST)**. No “analyze all” in v1.

## Signal gate diagnostics (all algos)

Per-symbol breakdown of which entry filters pass/fail, plus backtest ablations:

```bash
# CLI (from repo root, same PYTHONPATH as API)
python -m sandbox.gates scan 44ma ETERNAL.NS
python -m sandbox.gates scan financially_free RELIANCE.NS
python -m sandbox.gates scan kali RELIANCE.NS
python -m sandbox.gates compare 44ma
```

HTTP (requires `X-API-Key`):

- `GET /gates` — list algos
- `GET /gates/{algo_id}?symbol=ETERNAL.NS` — gate breakdown
- `GET /gates/{algo_id}/compare?start=2018-01-01` — filter ablation backtest

Cancel a stale open pending (UI: **Cancel** on open orders row, or API):

```bash
curl -X POST -H "X-API-Key: $ANALYZE_API_KEY" \
  "http://localhost:8000/portfolio/44ma/pending/ETERNAL.NS/cancel"
```

## Database admin (interactive reset)

From repo root:

```bash
cp sandbox/.env.example sandbox/.env
# edit SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, PYTHON
./database.sh
```

`database.sh` reads **`sandbox/.env`** only (no shell exports needed). Optional override: repo root `.env`.

Menu options per algo (`44ma`, `44ma_stacked_2ma`, `financially_free`, `kali`) or all four:

- Portfolio snapshot (positions, pending, row counts)
- Full reset — positions, pending, journal, closed trades, equity snapshots, run logs, cash/equity → starting capital
- Custom reset — pick which tables to clear and whether to renew cash
- Close one position — delete holding and return cost to cash
- Cancel open pending orders
- Renew cash & equity only (keeps history)
- Set starting capital (optional cash/equity sync)

## Supabase setup

1. Create a Supabase project.
2. Apply migrations in order:

```bash
sandbox/supabase/migrations/20260520000000_initial_schema.sql
sandbox/supabase/migrations/20260521000000_remove_hybrid_swing.sql  # if hybrid was seeded earlier
```

3. Set env vars (service role — backend only):

```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

## Local development

### API (from repo root)

```bash
cp sandbox/.env.example sandbox/.env
# edit SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, PYTHON

pip install -r sandbox/requirements.txt
pip install -r 44ma/requirements.txt
pip install -r "financially free/requirements.txt"
pip install -e KALI

./run-api.sh
```

`run-api.sh` loads `sandbox/.env` and sets `PYTHONPATH` for you — no `export` in the terminal.

If you see `TypeError: Unable to evaluate type annotation 'str | None'`, set `PYTHON=44ma/.venv/bin/python` in `sandbox/.env`.

### Web

From repo root (uses same `sandbox/.env` as the API):

```bash
./run-web.sh
```

Open http://localhost:3000 (API must be running: `./run-api.sh` in another terminal).

Manual setup (optional):

```bash
cd sandbox/web
cp .env.example .env.local
```

Edit `.env.local`:

```
API_URL=http://localhost:8000
ANALYZE_API_KEY=dev-secret
```

```bash
npm install
npm run dev
```

Open http://localhost:3000

## Deploy (Render — recommended)

| Component | Render service | Notes |
|-----------|----------------|-------|
| DB | Supabase (hosted) | Migrations in `sandbox/supabase/migrations/` |
| API | `paper-sandbox-api` | Docker — `sandbox/Dockerfile` |
| Web | `paper-sandbox-web` | Node — `sandbox/web` |

Repo root includes **`render.yaml`** (Blueprint). Both services use the **free** plan (512MB RAM on API; may need a paid instance for full KALI scans — see below).

### One-time setup

1. Push this repo to GitHub (do not commit `sandbox/.env`).
2. [render.com](https://render.com) → **New** → **Blueprint** → connect the repo.
3. Render creates `paper-sandbox-api` and `paper-sandbox-web`. On first sync it prompts for:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
4. `ANALYZE_API_KEY` is auto-generated on the API service and wired to the web service.
5. Wait for both deploys. Open the **web** service URL (`paper-sandbox-web.onrender.com`).

### Verify

```bash
curl https://paper-sandbox-api.onrender.com/health
```

Dashboard should load portfolio data from Supabase via the API.

### Dashboard shows ₹0 and “Bad Gateway” / HTML in algo cards

That HTML is Render’s **502** page: the **web** app called `paper-sandbox-api`, but no healthy API instance answered.

1. **Render → `paper-sandbox-api` → Logs** — look for crash/OOM during startup or missing env vars.
2. **`curl https://paper-sandbox-api.onrender.com/health`** — must return `{"status":"ok"}` within ~60s (free tier cold start). If it times out, the API never came up; redeploy or upgrade instance RAM.
3. **Environment** on the API service: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `ANALYZE_API_KEY` must be set. On the web service, `API_URL` must be the API’s `https://…onrender.com` URL and **`ANALYZE_API_KEY` must match** the API (Blueprint wires this; if you edited keys manually, sync both).
4. After fixing the API, **redeploy `paper-sandbox-web`** so it picks up env changes, then hard-refresh the dashboard.

The web UI now shows a short error instead of raw HTML and retries once after ~20s when the API is waking from sleep.

### Free tier caveats

- Services **sleep after ~15 minutes** of no traffic; first request after idle can take 30–60s.
- API free tier is **512MB RAM**. Heavy `POST /analyze/kali` runs may OOM; upgrade the API instance in Render (**Settings → Instance type**) if needed.
- For daily EOD analyze after market close, use the free **GitHub Actions** workflow (see below). Render Cron Jobs are not on the free tier.

### Manual deploy (without Blueprint)

**API:** Web Service → Docker → Dockerfile path `sandbox/Dockerfile`, context `.`, env vars as in the table below.

**Web:** Web Service → Node → Root directory `sandbox/web`, Build `npm ci && npm run build`, Start `npm start`, env `API_URL` = API’s `https://…onrender.com`, same `ANALYZE_API_KEY`.

### Free EOD cron (GitHub Actions)

Workflow: `.github/workflows/eod-analyze.yml` — runs **Mon–Fri 11:30 UTC** (5:00 PM IST) and calls all four `POST /analyze/...` routes. The API returns immediately (`status: running`); work runs in the background on Render.

1. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**
2. Add:

| Secret | Value |
|--------|--------|
| `API_URL` | `https://paper-sandbox-api.onrender.com` (your API URL, no trailing slash) |
| `ANALYZE_API_KEY` | Copy from Render → `paper-sandbox-api` → **Environment** |

3. Push the workflow file to `main`. Test manually: **Actions → EOD analyze → Run workflow**.

To change time, edit the `cron` line in the workflow ([cron syntax](https://docs.github.com/en/actions/writing-workflows/schedule-trigger); UTC only).

**Other free options:** [cron-job.org](https://cron-job.org) (HTTP POST with header `X-API-Key`) — one job per algo or one job hitting a small wrapper URL.

### Alternative (Fly + Vercel)

| Component | Target |
|-----------|--------|
| API | Fly.io — `fly launch --dockerfile sandbox/Dockerfile` |
| Web | Vercel — root `sandbox/web`, env `API_URL`, `ANALYZE_API_KEY` |

## API auth

All routes except `GET /health` require header:

```
X-API-Key: <ANALYZE_API_KEY>
```

## Environment variables

| Variable | Required | Used by |
|----------|----------|---------|
| `SUPABASE_URL` | yes | API |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | API |
| `ANALYZE_API_KEY` | yes | API + Next BFF |
| `MA44_CONFIG_PATH` | no | Override config for a single 44ma run (sandbox uses per-algo config files) |

## KALI ops

`POST /analyze/kali?skip_fundamentals=true` — admin override only (skips Screener.in).
