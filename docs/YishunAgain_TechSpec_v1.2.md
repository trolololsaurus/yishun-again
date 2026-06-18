# YISHUN AGAIN — TECHNICAL SPECIFICATION
## For Coding Agents / Developers
**Version:** 1.0 | **Phase:** 1 — Foundation Build
**Last Updated:** 2024

---

## 0. CONTEXT FOR AGENT

You are building a satirical, semi-autonomous incident archive for Yishun, Singapore. The operator reviews and approves all content before publish. Your job is to build the infrastructure that makes this possible. Follow this spec exactly. When in doubt, ask the operator. Do not invent features not listed here.

**Core constraint:** Every published incident must link to a verifiable source. No private individuals unless named in MSM or Reddit. No political content. Ever.

---

## 1. REPOSITORY STRUCTURE

```
yishun-again/
├── apps/
│   ├── web/                    # Next.js frontend (public site)
│   └── war-room/               # Next.js admin CMS (private, operator only)
├── packages/
│   ├── agents/                 # Python agent pipeline (FastAPI)
│   │   ├── scrapers/           # Per-source scraping agents
│   │   ├── filters/            # Stage 1 (Groq) + Stage 2 (Claude) filters
│   │   ├── classifiers/        # Classification + severity scoring
│   │   ├── writers/            # Incident draft generation
│   │   ├── art/                # Pixel art prompt generation + Modal.run calls
│   │   ├── cards/              # Share card generation (UTM-tagged)
│   │   └── orchestrator/       # LangGraph orchestrator
│   ├── db/                     # Supabase schema, migrations, types
│   └── shared/                 # Shared types, constants, utils
├── infra/
│   ├── cloudbuild.yaml         # Google Cloud Run config for agents backend
│   └── cloudflare/             # Cloudflare R2 + Stream config
└── docs/
    ├── PRD.docx
    └── ARCHITECTURE.drawio
```

---

## 2. TECH STACK — EXACT VERSIONS

| Layer | Tool | Version | Notes |
|-------|------|---------|-------|
| Frontend | Next.js | 14.x (App Router) | Vercel deploy |
| Map | MapLibre GL JS | 3.x | Custom pixel art marker icons |
| Database | Supabase | Latest | Postgres + REST API |
| Image storage | Cloudflare R2 | — | Via S3-compatible API |
| Video storage | Cloudflare Stream | — | Phase 2 only |
| CDN + DDoS | Cloudflare | Free tier | All traffic routed through CF |
| Admin auth | Cloudflare Access | Free tier | Zero-trust, service token |
| Backend | FastAPI | 0.110.x | Python 3.11+ |
| Agent hosting | Google Cloud Run | — | Single shared-cpu-1x machine to start |
| Stage 1 filter | Groq API | — | llama3-8b-8192 model |
| Stage 2 writer | Anthropic API | — | claude-haiku-4-5-20251001 default, claude-sonnet-4-6 for quality tasks |
| Orchestrator | LangGraph | 0.1.x | Python |
| Image gen | Modal.run | — | SDXL + LoRA, async job |
| Scheduling | APScheduler | 3.x | Embedded in FastAPI |
| CSS | Tailwind CSS | 3.x | Pixel art + retro tabloid theme |

---

## 3. DATABASE SCHEMA

Use Supabase. All tables in `public` schema. Enable Row Level Security (RLS) — public reads only, all writes via service role key from agents backend.

### 3.1 `incidents` table

```sql
CREATE TABLE incidents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  published_at    TIMESTAMPTZ,
  incident_date   DATE NOT NULL,
  title           TEXT NOT NULL,
  summary         TEXT NOT NULL,                    -- agent-drafted, operator-approved
  classification  TEXT NOT NULL CHECK (classification IN ('heart', 'clown', 'dagger', 'custom')),
  custom_label    TEXT,                             -- if classification = 'custom'
  severity        INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
  block_number    TEXT,                             -- e.g. "Block 123"
  area_name       TEXT,                             -- e.g. "Yishun Ring Road"
  latitude        DECIMAL(9,6),
  longitude       DECIMAL(9,6),
  source_urls     TEXT[] NOT NULL,                  -- min 1 required
  corroboration_count INTEGER DEFAULT 1,
  edmw_signal_count   INTEGER DEFAULT 0,            -- signal only, never displayed as source
  pixel_art_url   TEXT,                             -- Cloudflare R2 URL
  share_card_url  TEXT,                             -- Cloudflare R2 URL
  slug            TEXT UNIQUE NOT NULL,             -- URL-friendly, auto-generated
  seo_title       TEXT,
  seo_description TEXT,
  is_published    BOOLEAN DEFAULT FALSE,
  chaos_contribution DECIMAL(4,2),                  -- computed weight for Chaos Index
  agent_confidence DECIMAL(3,2),                    -- 0.00–1.00
  tags            TEXT[]
);

CREATE INDEX idx_incidents_published ON incidents(is_published, published_at DESC);
CREATE INDEX idx_incidents_classification ON incidents(classification);
CREATE INDEX idx_incidents_location ON incidents(latitude, longitude) WHERE latitude IS NOT NULL;
CREATE INDEX idx_incidents_date ON incidents(incident_date DESC);
```

### 3.2 `sources` table

```sql
CREATE TABLE sources (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL UNIQUE,
  url             TEXT NOT NULL,
  type            TEXT NOT NULL CHECK (type IN ('msm', 'reddit', 'signal')),
  -- msm = Mothership/CNA/Stomp (quotable)
  -- reddit = r/singapore, r/singaporeraw (quotable)
  -- signal = EDMW (signal only, never attributed)
  is_active       BOOLEAN DEFAULT TRUE,
  scrape_interval_minutes INTEGER DEFAULT 60,
  reliability_score DECIMAL(3,2) DEFAULT 0.70,     -- updated by orchestrator
  added_at        TIMESTAMPTZ DEFAULT NOW(),
  approved_by_operator BOOLEAN DEFAULT FALSE,       -- MUST be true before scraping
  discovery_notes TEXT                              -- from source discovery agent
);

-- Seed data
-- English MSM
INSERT INTO sources (name, url, type, scrape_interval_minutes, approved_by_operator) VALUES
  ('CNA', 'https://www.channelnewsasia.com', 'msm', 60, true),
  ('The Straits Times', 'https://www.straitstimes.com', 'msm', 60, true),
  ('Mothership', 'https://mothership.sg', 'msm', 60, true),
  ('Stomp', 'https://stomp.straitstimes.com', 'msm', 120, true),
  ('MustShareNews', 'https://mustsharenews.com', 'msm', 60, true),
  ('The Independent Singapore', 'https://theindependent.sg', 'msm', 60, true),
  ('Jom', 'https://jom.media', 'msm', 360, true),
-- Aggregators
  ('Yahoo News Singapore', 'https://sg.news.yahoo.com', 'msm', 120, true),
  ('AsiaOne', 'https://www.asiaone.com', 'msm', 120, true),
-- Multilingual MSM (content translated to English by Stage 2 agent)
  ('Lianhe Zaobao', 'https://www.zaobao.com.sg', 'msm', 180, true),
  ('Shin Min Daily News', 'https://www.shinmin.sg', 'msm', 180, true),
  ('Berita Harian', 'https://www.beritaharian.sg', 'msm', 180, true),
  ('Tamil Murasu', 'https://tamilmurasu.com.sg', 'msm', 180, true),
-- Reddit
  ('Reddit Singapore', 'https://www.reddit.com/r/singapore', 'reddit', 30, true),
  ('Reddit SingaporeRaw', 'https://www.reddit.com/r/singaporeraw', 'reddit', 30, true),
-- Signal only
  ('HWZ EDMW', 'https://forums.hardwarezone.com.sg/eat-drink-man-woman-16', 'signal', 60, true);
```

### 3.3 `utm_events` table

```sql
CREATE TABLE utm_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id     UUID REFERENCES incidents(id) ON DELETE SET NULL,
  timestamp       TIMESTAMPTZ DEFAULT NOW(),
  utm_source      TEXT,     -- telegram, reddit, hwz, direct, unknown
  utm_medium      TEXT,     -- share_card, link, organic
  utm_campaign    TEXT,     -- incident classification type
  geo_country     TEXT,
  geo_city        TEXT,
  geo_region      TEXT,
  vpn_suspected   BOOLEAN DEFAULT FALSE,
  user_agent_hash TEXT,     -- hashed, no PII
  referrer        TEXT
);

CREATE INDEX idx_utm_incident ON utm_events(incident_id);
CREATE INDEX idx_utm_timestamp ON utm_events(timestamp DESC);
CREATE INDEX idx_utm_source ON utm_events(utm_source);
```

### 3.4 `training_signals` table

```sql
CREATE TABLE training_signals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id     UUID REFERENCES incidents(id) ON DELETE CASCADE,
  timestamp       TIMESTAMPTZ DEFAULT NOW(),
  action          TEXT NOT NULL CHECK (action IN ('approve', 'edit_approve', 'reject')),
  reject_reason   TEXT CHECK (reject_reason IN ('noise', 'duplicate', 'unverified', 'too_thin', 'legal_risk', NULL)),
  original_draft  TEXT,
  edited_draft    TEXT,                             -- NULL if approved as-is
  original_classification TEXT,
  edited_classification   TEXT,
  original_severity INTEGER,
  edited_severity   INTEGER,
  operator_changes  JSONB,                          -- diff of all changes made
  agent_confidence_was DECIMAL(3,2)
);

CREATE INDEX idx_training_action ON training_signals(action);
CREATE INDEX idx_training_timestamp ON training_signals(timestamp DESC);
```

### 3.5 `war_room_queue` table

```sql
CREATE TABLE war_room_queue (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  raw_content     JSONB NOT NULL,                   -- scraped raw data
  source_url      TEXT NOT NULL,
  source_type     TEXT NOT NULL,
  proposed_classification TEXT,
  proposed_severity INTEGER,
  proposed_summary TEXT,
  proposed_title  TEXT,
  proposed_pixel_prompt TEXT,
  proposed_slug   TEXT,
  agent_confidence DECIMAL(3,2),
  corroboration_count INTEGER DEFAULT 1,
  edmw_signal_count   INTEGER DEFAULT 0,
  status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'escalated')),
  processed_at    TIMESTAMPTZ,
  incident_id     UUID REFERENCES incidents(id)     -- set after approval
);

CREATE INDEX idx_queue_status ON war_room_queue(status, created_at DESC);
```

### 3.6 `chaos_index_snapshots` table

```sql
CREATE TABLE chaos_index_snapshots (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_at TIMESTAMPTZ DEFAULT NOW(),
  score_30d   DECIMAL(5,2),
  score_90d   DECIMAL(5,2),
  score_365d  DECIMAL(5,2),
  score_alltime DECIMAL(5,2),
  descriptor  TEXT,   -- 'Quiet' / 'Simmering' / 'Elevated' / 'Critical' / 'Apocalyptic'
  incident_count_30d INTEGER,
  dagger_count_30d   INTEGER,
  clown_count_30d   INTEGER,
  heart_count_30d    INTEGER
);
```

---

## 4. AGENT PIPELINE

### 4.1 Scrape Agent

**File:** `packages/agents/scrapers/scrape_agent.py`

```python
# Responsibilities:
# - Poll each source on its configured interval
# - Extract article URLs and content for Yishun-tagged content
# - Pass raw content to Stage 1 filter
# - Log all scrape activity

# Yishun keyword list (expand as needed):
YISHUN_KEYWORDS = [
    "yishun", "yishun ring road", "yishun ave", "yishun street",
    "yishun mrt", "northpoint", "khoo teck puat", "yishun park",
    "yishun dam", "yishun pond", "sembawang"  # adjacent
]

# Scrape interval is per source, set in sources table
# APScheduler job per source
# Output: raw_content dict → Stage 1 queue
```

**RSS-first approach:** CNA, Mothership have RSS feeds. Use them. Fall back to HTML scraping only if RSS unavailable.

**Reddit:** Use Reddit JSON API (no auth required for public subreddits):
```
https://www.reddit.com/r/singapore/search.json?q=yishun&sort=new&limit=25
```

**HWZ EDMW:** HTML scraping only. Search for "yishun" in thread titles. Extract thread title, post count, view count only. Never extract post content.

### 4.2 Stage 1 Filter — Groq

**File:** `packages/agents/filters/stage1_filter.py`

```python
# Model: llama3-8b-8192 via Groq API
# Purpose: Fast, free noise rejection
# Target rejection rate: 60-70% of raw scrape volume
# Pass threshold: confidence >= 0.4

STAGE1_SYSTEM_PROMPT = """
You are a content filter for a Yishun, Singapore incident archive.

Your job: determine if a piece of content is worth logging as a Yishun incident.

Return JSON only:
{
  "is_relevant": boolean,
  "confidence": float (0.0-1.0),
  "reason": string (one sentence)
}

PASS if content describes:
- A specific incident, event, or occurrence in Yishun
- A person associated with Yishun making news
- A crime, accident, unusual event, positive community story in Yishun

REJECT if content is:
- General news mentioning Yishun only in passing
- Advertisements, property listings, event promotions
- Opinion pieces with no specific incident
- Clearly duplicate of something already archived
- Political content of any kind
"""
```

### 4.3 Stage 2 Writer — Claude

**File:** `packages/agents/filters/stage2_writer.py`

```python
# Model: claude-haiku-4-5-20251001 for classification
#        claude-sonnet-4-6 for final draft writing
# Only Stage 1 approved content reaches here

STAGE2_SYSTEM_PROMPT = """
You are an editorial agent for Yishun Again, a satirical incident archive for Yishun, Singapore.

Tone: Dry. Deadpan. Factual with a raised eyebrow. Never sensational. Never political. Never defamatory.

Given source content, return JSON only:
{
  "title": string (max 80 chars, SEO-optimised, includes 'Yishun'),
  "summary": string (max 300 chars, factual, dry tone),
  "classification": "heart" | "clown" | "dagger",
  "severity": integer 1-5,
  "block_number": string | null,
  "area_name": string | null,
  "latitude": float | null,
  "longitude": float | null,
  "slug": string (URL-safe, max 60 chars),
  "seo_title": string (max 60 chars),
  "seo_description": string (max 155 chars),
  "pixel_art_prompt": string (detailed prompt for SDXL pixel art generation),
  "tags": string[],
  "confidence": float (0.0-1.0),
  "chaos_contribution": float (1-5 scale, Daggers weighted 3x, Clowns 1.5x, Hearts -1x)
}

Classification guide:
- heart: Good news, community wins, positive stories
- clown: Absurd, stupid, baffling behaviour — no serious harm
- dagger: Crime, violence, serious incidents

Severity guide (dagger):
1 = Minor offence, no injury
2 = Property crime, minor injury
3 = Assault, significant incident
4 = Serious crime, major incident  
5 = Homicide, major catastrophe

Pixel art prompt guide:
- Always specify: "16-bit JRPG pixel art style, Yishun HDB environment"
- Describe the scene without depicting real people
- Keep it interpretive, not photorealistic
- Example: "16-bit JRPG pixel art style, Yishun HDB void deck at night, yellow police tape, pixel art lamp post, dark atmospheric lighting"
"""
```

### 4.4 Corroboration Agent

**File:** `packages/agents/classifiers/corroboration.py`

```python
# Before queuing for War Room, verify corroboration:
# 1. Search other active sources for same incident
# 2. Count matching sources (by date + location + incident type)
# 3. Log EDMW thread count as edmw_signal_count (never as a source)
# 4. If corroboration_count == 0 after search: still queue, but flag as unverified
#    Operator can approve or reject as 'unverified'
# Minimum to auto-publish: 1 MSM or Reddit source
```

### 4.5 Source Discovery Agent

**File:** `packages/agents/scrapers/source_discovery.py`

```python
# Runs: First Monday of every month via APScheduler
# Method: Web search for Yishun news coverage from unknown sources
# Output: Candidate sources logged to sources table with approved_by_operator = FALSE
# Operator sees candidates in War Room under "New Sources" tab
# Nothing is scraped until operator sets approved_by_operator = TRUE
```

---

## 5. WAR ROOM CMS

**Path:** `apps/war-room/`

**Access control:** Protected by Cloudflare Access. No public route. Operator authenticates via Cloudflare zero-trust (email OTP or GitHub SSO). Never expose war room URL publicly.

### 5.1 Queue View

```
/war-room/queue
```

Shows all `war_room_queue` entries with `status = 'pending'`, sorted by `created_at DESC`.

Per card displays:
- Source link (clickable, opens in new tab)
- Proposed classification (icon + label)
- Severity (1–5 stars)
- Proposed title + summary
- Pixel art prompt
- Confidence score (colour-coded: green ≥0.8, yellow ≥0.5, red <0.5)
- Corroboration count
- EDMW signal count (labelled "Forum buzz" — never "source")

Actions:
- **Approve** → creates incident record, sets is_published = true, logs training signal (action: 'approve')
- **Edit & Approve** → opens edit modal, saves changes, logs training signal (action: 'edit_approve', operator_changes: diff)
- **Reject** → dropdown: noise / duplicate / unverified / too thin / legal risk → logs training signal (action: 'reject', reject_reason)

### 5.2 Incident Management

```
/war-room/incidents
```

Full CRUD on published incidents. Edit, unpublish, delete.

### 5.3 New Sources Tab

```
/war-room/sources
```

Shows all sources with `approved_by_operator = FALSE`. Operator can approve (adds to scraping pipeline) or dismiss (sets is_active = false).

### 5.4 Analytics View

```
/war-room/analytics
```

- UTM event breakdown by source, campaign, geography
- Agent confidence trend over time
- Approval/rejection rate by classification type
- Top performing incidents by UTM events
- Suspected VPN traffic flag count

---

## 6. FRONTEND — PUBLIC SITE

**Path:** `apps/web/`
**Framework:** Next.js 14 App Router
**Styling:** Tailwind CSS + custom pixel art theme

### 6.1 Theme Tokens

```css
/* Pixel art retro tabloid theme */
:root {
  --color-bg: #0D0D0D;
  --color-surface: #1A1A1A;
  --color-border: #333333;
  --color-accent-red: #E74C3C;
  --color-accent-yellow: #F1C40F;
  --color-text-primary: #F5F5F5;
  --color-text-secondary: #AAAAAA;
  --color-heart: #E74C3C;
  --color-clown: #F1C40F;
  --color-dagger: #8E44AD;
  --font-display: 'Press Start 2P', monospace;  /* pixel font for headings */
  --font-body: 'Courier New', monospace;         /* typewriter for body */
}
```

**Font:** Import 'Press Start 2P' from Google Fonts for headers. Courier New or similar monospace for body.

### 6.2 Routes

```
/                           # Homepage — map hero + Chaos Index
/incidents/[slug]           # Individual incident page (SSG + ISR)
/timeline                   # Full historical timeline
/people/[slug]              # People of interest profiles
/about                      # What is this site + legal disclaimer
```

### 6.3 Homepage Layout

```
┌─────────────────────────────────────────────────────┐
│  YISHUN AGAIN    [pixel art logo]    Chaos Index: 73 │
│  "Singapore's Most Cursed Estate — Documented."      │
├─────────────────────────────────────────────────────┤
│                                                      │
│         MAPBOX MAP (full width, 60vh)                │
│         Block-level markers, colour by classification│
│         Click marker → incident popover              │
│                                                      │
├─────────────────────────────────────────────────────┤
│  [❤️ 12]  [🤡 34]  [🗡️ 67]   Filter buttons         │
├──────────────────────────┬──────────────────────────┤
│  RECENT INCIDENTS (feed) │  CHAOS INDEX BREAKDOWN   │
│  Last 10, date sorted    │  30d / 90d / 365d tabs   │
│  Each: icon + title +    │  Bar chart by type       │
│  severity + share link   │  Trend sparkline         │
└──────────────────────────┴──────────────────────────┘
```

### 6.4 Incident Page

```
/incidents/[slug]
```

- SSG (static generation) + ISR (revalidate: 3600)
- Schema.org Event markup
- Open Graph tags for share card preview
- Source links section (all linked, labelled by source name)
- Corroboration badge: "Reported by X sources"
- Share card download button (UTM-tagged)
- Related incidents (same area or classification, last 5)

### 6.5 Share Card Generation

Per incident, generate a 1200x630px image:

```
┌──────────────────────────────────────────────┐
│  [CLASSIFICATION ICON]  YISHUN AGAIN          │
│                                               │
│  [PIXEL ART SCENE - 400x300px]               │
│                                               │
│  [INCIDENT TITLE - max 2 lines]              │
│  [DATE]  [SEVERITY: ★★★☆☆]                  │
│                                               │
│  yishunagain.com                               │
└──────────────────────────────────────────────┘
```

Generated via: `canvas` npm package (server-side) or `@vercel/og` (edge function)
Stored in: Cloudflare R2
URL format: `https://assets.yishunagain.com/cards/[incident-id].png`

UTM share URL embedded in OG meta:
```html
<meta property="og:url" content="https://yishunagain.com/incidents/[slug]?utm_source=share_card&utm_medium=og&utm_campaign=[classification]" />
```

---

## 7. CHAOS INDEX COMPUTATION

**Compute on:** Every new incident published. Store snapshot in `chaos_index_snapshots`.

```python
def compute_chaos_index(window_days: int = 30) -> float:
    """
    Weights:
    - dagger: severity * 3.0
    - clown: severity * 1.5
    - heart:  severity * -1.0 (positive news reduces score)
    
    Normalised to 0–100.
    Max theoretical score (all severity-5 daggers): 100
    """
    incidents = get_incidents_in_window(window_days)
    
    raw_score = 0
    for inc in incidents:
        if inc.classification == 'dagger':
            raw_score += inc.severity * 3.0
        elif inc.classification == 'clown':
            raw_score += inc.severity * 1.5
        elif inc.classification == 'heart':
            raw_score -= inc.severity * 1.0
    
    # Normalise: assume max 100 incidents at max weight in window
    normalised = min(100, max(0, (raw_score / 300) * 100))
    return round(normalised, 2)

DESCRIPTORS = {
    (0, 20): "Quiet",
    (20, 40): "Simmering",
    (40, 60): "Elevated",
    (60, 80): "Critical",
    (80, 100): "Apocalyptic"
}
```

---

## 8. UTM TRACKING

### 8.1 Logging Endpoint

```
POST /api/utm/log
```

Called client-side on page load if UTM params present in URL. No cookies. No fingerprinting beyond hashed user agent.

```python
# Payload
{
  "incident_id": "uuid",
  "utm_source": "telegram",
  "utm_medium": "share_card",
  "utm_campaign": "dagger",
  "referrer": "https://t.me/...",  # stripped to domain only
}

# Server enriches with:
# - geo from Cloudflare CF-IPCountry header (no IP stored)
# - vpn_suspected from Cloudflare bot score or IP ASN check
# - user_agent_hash: SHA256(user_agent)[:16] (no PII)
```

### 8.2 Share URL Format

```
https://yishunagain.com/incidents/{slug}?utm_source={source}&utm_medium=share_card&utm_campaign={classification}
```

Source values: `telegram`, `reddit`, `hwz`, `whatsapp`, `organic`, `direct`

---

## 9. ART PIPELINE

### 9.1 Style Lock

Train one SDXL LoRA on a curated set of 50–100 pixel art images with consistent style:
- 16-bit JRPG aesthetic
- HDB block environments
- Dark, moody colour palette
- No photorealism

**LoRA training:** Run once on Modal.run. Store weights in Cloudflare R2. Never retrain without operator approval.

### 9.2 Image Generation Call

**File:** `packages/agents/art/art_agent.py`

```python
import modal

# Modal.run async job
# Input: pixel_art_prompt from Stage 2 writer
# Output: PNG stored to Cloudflare R2
# Triggered: after operator approves incident in War Room

async def generate_pixel_art(prompt: str, incident_id: str) -> str:
    """
    Returns Cloudflare R2 URL of generated image.
    Full prompt = f"{prompt}, {LORA_TRIGGER_WORD}, masterpiece, detailed pixel art"
    Negative prompt = "photorealistic, 3d render, photograph, blurry, people faces"
    """
    ...
```

---

## 10. ENVIRONMENT VARIABLES

```bash
# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=           # public reads
SUPABASE_SERVICE_ROLE_KEY=   # agent writes (never expose to frontend)

# Anthropic
ANTHROPIC_API_KEY=

# Groq
GROQ_API_KEY=

# MapLibre + OpenFreeMap (no token needed)
NEXT_PUBLIC_MAPLIBRE_STYLE=https://tiles.openfreemap.org/styles/liberty

# Cloudflare R2
CF_R2_ACCOUNT_ID=
CF_R2_ACCESS_KEY_ID=
CF_R2_SECRET_ACCESS_KEY=
CF_R2_BUCKET_NAME=yishun-assets

# Cloudflare Stream (Phase 2)
CF_STREAM_TOKEN=

# Modal.run
MODAL_TOKEN_ID=
MODAL_TOKEN_SECRET=

# Reddit (optional, rate limit bypass)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=

# App
NEXT_PUBLIC_SITE_URL=https://yishunagain.com
WAR_ROOM_URL=https://warroom.yishunagain.com   # private subdomain, CF Access protected
```

---

## 11. DEPLOYMENT

### 11.1 Frontend (Vercel)

```bash
# apps/web
vercel deploy --prod

# Environment: set all NEXT_PUBLIC_* vars in Vercel dashboard
# Domain: yishunagain.com + www.yishunagain.com
# Cloudflare proxy: YES (orange cloud on both)
```

### 11.2 Agents Backend (Google Cloud Run)

```bash
# packages/agents
# Build and deploy to Cloud Run (Singapore region)
gcloud run deploy yishun-agents \
  --source . \
  --region asia-southeast1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --set-env-vars PORT=8080

# Dockerfile (in packages/agents/)
# FROM python:3.11-slim
# WORKDIR /app
# COPY requirements.txt .
# RUN pip install -r requirements.txt
# COPY . .
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 11.3 Cloudflare Setup

1. Add domain to Cloudflare (nameservers)
2. Create R2 bucket: `yishun-assets` → public access → custom domain `assets.yishunagain.com`
3. Cloudflare Access: protect `warroom.yishunagain.com` with email OTP (operator email only)
4. WAF rules: block non-SG traffic from War Room subdomain (optional, extra protection)
5. Page Rules: cache all `/incidents/*` pages aggressively (static content)

### 11.4 War Room (separate Vercel project or same repo)

```bash
# apps/war-room
# Deploy to warroom.yishunagain.com
# Protected entirely by Cloudflare Access
# Never expose in public DNS or sitemap
```

---

## 12. SEO REQUIREMENTS

Every incident page must include:

```html
<!-- Title -->
<title>{seo_title} | Yishun Again</title>

<!-- Open Graph -->
<meta property="og:title" content="{seo_title}" />
<meta property="og:description" content="{seo_description}" />
<meta property="og:image" content="{share_card_url}" />
<meta property="og:url" content="https://yishunagain.com/incidents/{slug}" />
<meta property="og:type" content="article" />

<!-- Schema.org Event -->
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "{title}",
  "startDate": "{incident_date}",
  "location": {
    "@type": "Place",
    "name": "{area_name}, Yishun, Singapore",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Yishun",
      "addressCountry": "SG"
    }
  },
  "description": "{summary}"
}
</script>

<!-- Sitemap: auto-generated, submit to Google Search Console -->
```

---

## 13. LEGAL GUARDRAILS — HARDCODED

These are not features. They are non-negotiable constraints baked into the pipeline.

1. **No incident is published without `source_urls` containing at least 1 URL.** Database constraint enforces this.
2. **`sources` entries with `type = 'signal'` (EDMW) are never included in `source_urls` of any incident.** Agent pipeline enforces this with explicit check.
3. **No personal information is stored in `incidents` table beyond what appears in public source URLs.**
4. **No political content.** If Stage 2 writer detects political framing, it must set confidence = 0 and flag in proposed_summary: "[POLITICAL CONTENT DETECTED — REJECT]"
5. **War Room is never accessible without Cloudflare Access authentication.** No bypass route. No API endpoint without service role key.
6. **`utm_events` stores no IP addresses, no cookies, no persistent user identifiers.**

---

## 14. BUILD ORDER (Phase 1)

Execute in this sequence. Do not skip ahead.

```
Step 1:  Supabase schema — all tables, indexes, RLS policies
Step 2:  Cloudflare R2 bucket + domain
Step 3:  FastAPI skeleton on Google Cloud Run — health check endpoint only
Step 4:  Stage 1 filter (Groq) — unit tested with sample content
Step 5:  Stage 2 writer (Claude) — unit tested with sample content
Step 6:  Scraping agents — CNA + Mothership first, then others
Step 7:  War Room CMS — queue view + approve/reject flow
Step 8:  Training signal logging — verify signals persist correctly
Step 9:  Next.js frontend — map hero + Chaos Index (static mock data first)
Step 10: Wire frontend to Supabase — live data
Step 11: Share card generation + UTM logging
Step 12: Art pipeline (Modal.run + LoRA) — generate test cards
Step 13: SEO — meta tags, sitemap, schema markup
Step 14: Cloudflare Access for War Room
Step 15: Pre-load historical incidents (operator-assisted backfill)
Step 16: Launch ignition sequence
```

---

## 15. WHAT NOT TO BUILD (Phase 1)

- No user accounts
- No comments
- No upvotes / downvotes
- No TikTok video pipeline (Phase 2)
- No distribution orchestrator (Phase 3)
- No monetisation / ad code
- No mobile app
- No admin user roles (single operator only)
- No public API
