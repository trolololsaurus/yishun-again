# yishun-again

Satirical, semi-autonomous incident archive for Yishun, Singapore.
An agent pipeline scrapes Singapore news, drafts write-ups, and queues them for
operator review. Operator approves → incident publishes. Confidence ≥ 0.95 → auto-publishes.

**Core constraint:** every published incident links to a publisher URL — never an
aggregator or redirect wrapper. No private individuals unless named in MSM. No
political content, ever. Reddit is a signal source (UGC, not journalism), never a citation.

Full spec: `docs/YishunAgain_TechSpec_v1_9.md`. Before touching `packages/agents/ops/`,
read `docs/AUTONOMY.md`.

---

## Layout

```
apps/web/          Next.js — public site (Vercel)
apps/war-room/     Next.js — private CMS (Cloudflare Access)
packages/agents/   FastAPI agent pipeline (Cloud Run, asia-southeast1)
packages/db/       Supabase migrations (hand-applied, 001–017)
docs/              Spec, autonomy runbook, pipeline change records
```

## Stack

| Layer | Tool |
|---|---|
| Frontend | Next.js 16.2.x / React 19.2.x / Tailwind 3.x |
| Map | MapLibre GL JS 3.x — OpenFreeMap "Liberty" style (keyless) |
| Database | Supabase (Postgres + REST), RLS on all tables |
| Image storage | Cloudflare R2 |
| Backend | FastAPI 0.115.x / Python 3.11 |
| Stage 1 filter | Gemini `gemini-3.1-flash-lite` |
| Stage 2 classify + write | Anthropic `claude-haiku-4-5-20251001` |
| Image gen | Gemini `gemini-3.1-flash-lite-image` |
| Scheduling | Cloud Scheduler → `POST /orchestrator/daily` at 02:58 & 14:58 SGT |

Orchestration is hand-rolled (`ops/daily.py`, `ingestion/orchestrator.py`). APScheduler
is a dependency but is **off in production** — Cloud Run scales to zero.

## Pipeline

```
Scrape (25 sources) → Stage 1 (Gemini) → cluster by story → Stage 2 (Haiku)
→ groundedness + casualty checks → consolidation → war_room_queue
→ confidence ≥ 0.95 ? auto-publish : operator review in War Room
```

Art is generated at publish time (approve or auto-publish). `suicide`/`self-harm`
incidents get a fixed non-graphic police-response scene instead of AI-generated art.

## Setup

```bash
# Frontend (apps/web on :3000, apps/war-room on :3001)
npm install && npm run dev

# Agents backend
cd packages/agents
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Copy `.env.example` → `.env` and fill in. Secrets belong in Cloud Run env vars, never committed.

**Tests are standalone scripts, not pytest:**

```bash
# packages/agents/
for f in test_*.py; do ./.venv/Scripts/python.exe "$f" || echo "FAIL $f"; done

# apps/web/
npm test

# apps/war-room/
node --test apps/war-room/lib/utils.paragraphs.test.ts apps/war-room/lib/utils.incidentRef.test.ts
```

All 108 tests are offline (no network, no API keys, no DB). A red file is a real regression.

## Deploy

```bash
# Frontend — deploys on git push to main
vercel deploy --prod

# Agents backend
gcloud run deploy yishun-agents --source packages/agents \
  --region asia-southeast1 --platform managed --allow-unauthenticated \
  --timeout=3600 --memory=1Gi --min-instances=0 --max-instances=2
```

⚠️ `--allow-unauthenticated` is deliberate. Auth is `OPS_TOKEN` (`X-Ops-Token` on every
route except `/health`). Using `--no-allow-unauthenticated` blocks the War Room (Vercel,
no GCP identity) at the IAM edge and breaks art generation. See CLAUDE.md § Deployment.

## Legal guardrails (hardcoded — never remove)

1. `source_urls ≥ 1 URL` — DB `CHECK (cardinality(source_urls) >= 1)` (migration 010)
2. Signal sources (EDMW/HWZ, Reddit) never in `source_urls` — `source_allowlist.is_signal_source()`
3. No personal information beyond public source URLs — operator gate only
4. Political content → `confidence = 0` + reject marker + alert (evaluated before field validation)
5. Suicide/self-harm incidents → fixed non-graphic scene, never generated content

Regression guards: `test_stage2_guardrails.py`, `test_political_alert.py`,
`test_source_allowlist.py`, `test_image_suppression.py`.
