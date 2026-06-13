# YISHUN AGAIN — TECHNICAL SPECIFICATION
## For Coding Agents / Developers
**Version:** 1.9 | **Phase:** 1 — Foundation Build
**Last Updated:** June 2026

### Changelog
| Version | Date | Changes |
|---|---|---|
| 1.0 | May 2026 | Initial spec |
| 1.4 | May 2026 | Scraper health, milestone herald, EDMW tiers, War Room hybrid A+B view |
| 1.5 | June 2026 | Incident consolidation model, developing stories, related incident linking, Step 16 backfill spec, LoRA training confirmed, Cloudflare Access middleware, R2 public domain confirmed |
| 1.6 | June 2026 | Developing story lifecycle (source roles, 180-day timeout), pattern detection agent, people_profiles schema, autonomy graduation tracker, dismiss reason taxonomy, recalibration system |
| 1.7 | June 2026 | UI overhaul (one-page layout, game HUD, typography scale), classification display renames, death counter removed, hero incidents inserted, backfill agent built, geocoding agent, link validator, ISR revalidation, Chaos Index renamed, tech debt log |
| 1.8 | June 2026 | Backfill scope expanded 1980–2025, Wikipedia promoted to primary discovery source, phased Google News batches, EDMW signal in backfill from 2015+, Reddit explicitly out of backfill scope, groq_budget.py added, wikipedia_discovery.py added, scrapers inventory table, pre-backfill checklist, backfill_agent.py year-range refactor documented |
| 1.9 | June 2026 | **Forward-looking ingestion architecture (Option B):** new §4.9 ingestion layer (trigger-agnostic `run_ingestion_pass()` entrypoint, pluggable Source interface, GoogleNewsRSSSource adapter, RecencyFilter, FallbackLadder, IngestionReport) — detailed design in `docs/INGESTION_DESIGN.md`. New §3.7 `pipeline_state` + `pipeline_run_history` tables (watermark store). Corrected §11.2 trigger model (Cloud Scheduler→HTTP replaces broken in-process APScheduler under min-instances 0). §4.0b reconciliation note (spec-vs-filesystem drift documented). Manual historical backfill 2008–2025 completed; consolidation rulebook (`docs/CONSOLIDATION_RULES.md`) governs it. Data-quality audit pass (fabricated-date/URL detector) run; 3 wrong dates corrected. CULTURE content type (`classification='custom'`, `custom_label='CULTURE'`, 🌐) added for media/pop-culture mentions. |

---

## 0. CONTEXT FOR AGENT

You are building a satirical, semi-autonomous incident archive for Yishun, Singapore. The operator reviews and approves all content before publish. Your job is to build the infrastructure that makes this possible. Follow this spec exactly. When in doubt, ask the operator. Do not invent features not listed here.

**Core constraint:** Every published incident must link to a verifiable source. No private individuals unless named in MSM or Reddit. No political content. Ever.

---

## 1. REPOSITORY STRUCTURE

> ⚠️ **First thing you do after `git init`:** Create `.gitignore` with the entries below. Before writing any code. Before adding any keys anywhere.

```
# .gitignore — add this to repo root BEFORE anything else
.env
.env.local
.env.production
.env.*.local
*.env
**/.env
**/secrets.json
```

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
| Stage 1 filter | Groq API | — | llama-3.1-8b-instant model |
| Stage 2 writer | Anthropic API | — | claude-haiku-4-5-20251001 default, claude-sonnet-4-6 for quality tasks |
| Orchestrator | LangGraph | 0.4.0 | Python |
| Image gen | Modal.run | — | SDXL + LoRA yishunagain_v1 (trained, 456.5MB on R2) |
| Scheduling | APScheduler | 3.x | Embedded in FastAPI |
| CSS | Tailwind CSS | 3.x | Pixel art + retro tabloid theme |

---

## 3. DATABASE SCHEMA

Use Supabase. All tables in `public` schema. Enable Row Level Security (RLS) — public reads only, all writes via secret key from agents backend.

### 3.1 `incidents` table

```sql
CREATE TABLE incidents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  published_at    TIMESTAMPTZ,
  incident_date   DATE NOT NULL,
  title           TEXT NOT NULL,
  summary         TEXT NOT NULL,                    -- agent-drafted, operator-approved. Target 500-800 chars, SEO prose.
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
  hype_meter          INTEGER DEFAULT 0 CHECK (hype_meter BETWEEN 0 AND 5),
  -- hype_meter logic:
  -- 0 = EDMW or Reddit only, zero MSM sources
  -- 1 = 1 MSM source
  -- 2-5 = number of independent MSM sources corroborating
  -- Agent computes this automatically during corroboration step
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
  type            TEXT NOT NULL CHECK (type IN ('msm', 'reddit', 'signal', 'reference')),
  -- msm = CNA/ST/Mothership/Stomp etc (quotable, attributable)
  -- reddit = r/singapore, r/singaporeraw (quotable)
  -- signal = EDMW (can now surface as low-confidence incident with 👎 label)
  -- reference = Wikipedia (one-off backfill enrichment only, never scheduled)
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
  -- Jom removed: SSL reliability issues + low Yishun incident relevance (arts/culture focus)
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
  ('HWZ EDMW', 'https://forums.hardwarezone.com.sg/eat-drink-man-woman-16', 'signal', 60, true),
-- Reference source — one-off backfill enrichment only, not scheduled scraping
  ('Wikipedia', 'https://en.wikipedia.org', 'reference', 0, true);
-- Note: Wikipedia type='reference' is never scraped on schedule.
-- Queried only during backfill runs to enrich hero incidents with authoritative facts.
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
  status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'escalated', 'update', 'update_approved', 'update_rejected')),
  -- 'update' = new source report for an existing incident, pending operator review
  update_target_incident_id UUID REFERENCES incidents(id), -- set when status = 'update'
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

### 3.7 `pipeline_state` & `pipeline_run_history` tables (v1.9 — ingestion watermark)

> Added for the §4.9 forward-looking ingestion layer. Prior to v1.9 there was **no** watermark
> / "what's new since last run" mechanism anywhere in the system — every backfill run
> re-scraped the full year range from scratch. These tables give the live ingestion pass a
> persistent per-source watermark so each run processes only genuinely-new items.

```sql
-- One row per ingestion Source (Source.name is the key).
CREATE TABLE pipeline_state (
  source_name          TEXT PRIMARY KEY,          -- matches Source.name, e.g. 'google_news_rss'
  last_run_at          TIMESTAMPTZ,               -- when this source last completed successfully
  watermark            DATE,                      -- max published_at successfully ingested
  last_status          TEXT NOT NULL DEFAULT 'never_run'
                         CHECK (last_status IN ('never_run','ok','degraded','blocked','unavailable')),
  last_reason          TEXT,                      -- failure detail when not 'ok'
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only run history for observability (prune/caps as needed).
CREATE TABLE pipeline_run_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
  degraded      BOOLEAN NOT NULL DEFAULT FALSE,
  total_queued  INTEGER NOT NULL DEFAULT 0,
  report        JSONB NOT NULL               -- the full IngestionReport
);
```

**Watermark write rule (critical):** `watermark` advances **only** on a source's successful
pass, set to the max `published_at` actually ingested — never to `NOW()` (using "now" would skip
items published-but-not-yet-indexed). A source that was blocked or unavailable keeps its old
watermark so the next run re-attempts the same window; **no window is ever skipped because of a
block.** See `docs/INGESTION_DESIGN.md` §6, §8.

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
    "yishun dam", "yishun pond",
    "nee soon",     # Malay name for Yishun — same place, same town
    # NOTE: "sembawang" removed — separate town, not Yishun
]

# Scrape interval is per source, set in sources table
# APScheduler job per source
# Output: raw_content dict → Stage 1 queue
```

**RSS-first approach:** CNA, Mothership have RSS feeds. Use them. Fall back to HTML scraping only if RSS unavailable.

**Implementation notes from build (v1.4→build):**
- AsiaOne: no public RSS — HTML scraper (`/singapore` listing page)
- Lianhe Zaobao: RSS 404 — HTML scraper (`/news/singapore`)
- Berita Harian: RSS 404 — HTML scraper (`/singapura`)
- Shin Min, Tamil Murasu: no public RSS — HTML scrapers on main page
- Jom: **DROPPED** — SSL handshake issues + arts/culture focus, low Yishun incident probability
- Groq Stage 1 model: `llama-3.1-8b-instant` (replaces `llama3-8b-8192` which Groq decommissioned)
- All multilingual scrapers pre-filter in native script, translate via Haiku ONLY on keyword match

**Reddit:** Use Reddit JSON API (no auth required for public subreddits):
```
https://www.reddit.com/r/singapore/search.json?q=yishun&sort=new&limit=25
```

**HWZ EDMW:** HTML scraping only. Search for "yishun" in thread titles. Extract thread title, post count, view count only.

### 4.0b Scrapers Inventory (v1.8 — confirmed against filesystem)

> ⚠️ This table reflects the actual files in `packages/agents/scrapers/` as of June 2026.
> The spec is authoritative — if a file is listed here, it exists. If not listed, build it.

> 🔧 **v1.9 RECONCILIATION NOTE — this table has drifted from the filesystem. Verify against
> real files before trusting it.** Confirmed deltas as of v1.9:
> - `groq_budget.py` is marked "NOT YET BUILT" below but **exists** at `scrapers/groq_budget.py`.
> - `wikipedia_discovery.py` is marked "NOT YET BUILT" but was **never built as a standalone
>   file**; Wikipedia discovery logic lives **inline** in `backfill_agent.py` (`_scrape_wikipedia`).
> - `scrape_agent.py` (named in §4.1) **does not exist**; the per-source `scrape_<source>.py`
>   files below are the real implementation.
> - The "Live pipeline" scrapers in this table poll feeds via **in-process APScheduler, which
>   does not fire under `--min-instances 0`** (see corrected §11.2). The forward-looking live
>   pipeline is superseded by the **§4.9 ingestion architecture**; treat these per-source
>   scrapers as available source adapters to be wired behind the new `Source` interface, not as
>   an independently-working live pipeline.
> When this table and the filesystem disagree, **the filesystem wins** — update the table, don't
> trust it blindly.

| File | Purpose | Pipeline role | Notes |
|---|---|---|---|
| `backfill_agent.py` | Historical backfill via Google News + Wikipedia mode | Backfill only | 64KB. Has `--year`, `--limit`, `--dry-run`, `--year wiki`. Needs `--year-from`/`--year-to`/`--bypass-stage1` refactor. |
| `scrape_cna.py` | CNA RSS scraper | Live pipeline | RSS-first |
| `scrape_mothership.py` | Mothership RSS scraper | Live pipeline | RSS-first |
| `scrape_straitstimes.py` | ST RSS scraper | Live pipeline | RSS-first |
| `scrape_asiaone.py` | AsiaOne HTML scraper | Live pipeline | No public RSS — `/singapore` listing page |
| `scrape_stomp.py` | Stomp HTML scraper | Live pipeline | |
| `scrape_mustsharenews.py` | MustShareNews HTML scraper | Live pipeline | |
| `scrape_theindependent.py` | The Independent SG HTML scraper | Live pipeline | |
| `scrape_yahoo.py` | Yahoo News SG HTML scraper | Live pipeline | |
| `scrape_zaobao.py` | Lianhe Zaobao HTML scraper | Live pipeline | `/news/singapore` — multilingual, keyword pre-filter in Chinese |
| `scrape_beritaharian.py` | Berita Harian HTML scraper | Live pipeline | `/singapura` — Malay, keyword pre-filter |
| `scrape_shinmin.py` | Shin Min Daily News HTML scraper | Live pipeline | Multilingual |
| `scrape_tamilmurasu.py` | Tamil Murasu HTML scraper | Live pipeline | Multilingual |
| `scrape_reddit.py` | Reddit JSON API scraper | Live pipeline | r/singapore + r/singaporeraw |
| `scrape_edmw.py` | HWZ EDMW HTML scraper | Live pipeline + backfill signal (2015+) | Thread titles only. Signal, never source. |
| `scrape_discovery.py` | Source discovery agent | Live pipeline (monthly) | Runs first Monday of month. Finds new source candidates. |
| `wikipedia_discovery.py` | Wikipedia discovery agent | Backfill only | **NOT YET BUILT.** See §4.7. Bypasses Stage 1. |
| `groq_budget.py` | Groq TPD token counter | Backfill utility | **NOT YET BUILT.** See §4.8. |

**Confirmed absent (correctly):** `scrape_jom.py` — dropped in v1.4 (SSL issues + low Yishun relevance). `.pyc` in `__pycache__` is a harmless artifact from before removal.

**Live pipeline scrapers are NOT used for historical backfill.** They poll current feeds and have no historical search capability. Google News (via `backfill_agent.py`) covers historical MSM content across all these sources. Do not attempt to use individual MSM scrapers for backfill.

EDMW revised treatment (three tiers):
- **EDMW only, no MSM corroboration** → publishable but flagged with 👎 "EDMW only — unverified" badge. Hype meter = 0. Lower priority in feed. Operator decides in War Room.
- **EDMW + MSM corroboration** → full incident, EDMW signal count shown as "Forum buzz". Standard hype meter applies.
- **MSM only (no EDMW)** → standard incident, no EDMW reference.

EDMW content is never quoted or attributed directly. The 👎 badge signals to readers that corroboration is thin.

### 4.2 Stage 1 Filter — Groq

**File:** `packages/agents/filters/stage1_filter.py`

```python
# Model: llama-3.1-8b-instant via Groq API
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
Clickbait-native but grounded in fact. Think tabloid front page meets incident report.

TITLE RULES (critical):
- The word "Yishun" MUST appear in every title but NOT always first
- Lead with whatever creates the most tension or curiosity — sometimes "Yishun", sometimes the act, sometimes the subject
- Good: "Yishun man stabs neighbour over curry smell" (Yishun leads — natural hook)
- Good: "Cat found mutilated near Yishun Park pond" (subject leads — more disturbing)
- Good: "Block 651 resident hurls furniture from 12th floor in Yishun" (location leads — specific dread)
- Bad: "Man arrested in Yishun" (generic, no tension)
- Bad: "Stabbing incident reported at Yishun Ave 4" (sterile, bureaucratic)
- Always vivid. Always specific. Never passive voice. Max 90 chars.

SUMMARY RULES (SEO-optimised, 500-800 chars):
- Write 3-5 sentences of rich, keyword-dense prose
- Sentence 1: The hook — what happened, who, where (block-level if known)
- Sentence 2: Context and detail — how it unfolded, what led to it
- Sentence 3: Outcome — arrest, injury, outcome, community reaction
- Sentence 4-5 (if sources allow): Corroborating detail, quotes if available, wider significance
- Naturally include: "Yishun", block number or street name, incident type keywords
- Written for Google — targets long-tail queries like "yishun stabbing 2024", "yishun cat killing"
- Do NOT use bullet points. Flowing prose only.
- Do NOT editorialize beyond dry wit. Facts first.

Given source content, return JSON only:
{
  "title": string (max 90 chars, clickbait-native, Yishun must appear, not always first),
  "summary": string (500-800 chars, SEO prose, 3-5 sentences),
  "classification": "heart" | "clown" | "dagger",
  "severity": integer 1-5,
  "block_number": string | null,
  "area_name": string | null,
  "latitude": float | null,
  "longitude": float | null,
  "slug": string (SEO-friendly, descriptive, max 70 chars),
  // Format: [incident-type]-[location-descriptor]-[month-year]
  // Example: "yishun-stabbing-cooking-smells-jan-2024"
  // Example: "yishun-cat-found-injured-park-aug-2023"
  "seo_title": string (max 60 chars),
  "seo_description": string (max 155 chars),
  "pixel_art_prompt": string (detailed prompt for SDXL pixel art generation),
  "tags": string[],
  "confidence": float (0.0-1.0),
  "chaos_contribution": float (1-5 scale, Daggers weighted 3x, Clowns 1.5x, Hearts -1x),
  "hype_meter": integer 0-5
  // 0 = EDMW/Reddit signal only, no MSM
  // 1 = 1 MSM source confirmed
  // 2-5 = count of independent MSM sources corroborating
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

### 4.5 Incident Consolidation Agent

**File:** `packages/agents/classifiers/consolidation.py`

#### Overview

Multiple news articles about the same real-world event must not create multiple separate incident cards. The consolidation agent identifies duplicates and related incidents before any item reaches the War Room queue.

#### Grouping Rules

**Rule 1 — Same card (developing story):**
Same named entity + same core act, any time window → consolidate into one card.

Examples:
- Kurt Tay charged Nov 2023 + new charges Jan 2024 + sentenced Apr 2026 → **one card**, developing story
- Cat killings Sep 2015 + more cats Jan 2016 + Lee Wai Leong charged → **one card**

The card date always updates to the latest report. The narrative regenerates chronologically.

**Rule 2 — Separate card, flagged as related:**
Different entity + overlapping event → separate card, agent flags as "possible related incident".

Example:
- Kurt Tay intimate video case (main card)
- Telegram group member who shared same video → separate card, flagged as related to Kurt Tay card
- Agent flags: "possible related — shares entity [intimate video] + overlapping date window"
- Operator confirms or rejects the link in War Room

**Rule 3 — Separate card, no link:**
Same entity + different act → separate card, no link.

Examples:
- Kurt Tay divorce → separate card, no link to intimate video case
- Kurt Tay chased by security guard → separate card, no link

#### Consolidation Logic

```python
# Matching criteria (all must pass):
# 1. Entity overlap: same named person, organisation, or location unit (block-level)
# 2. Act overlap: same category of incident (crime type, event type)
# 3. Claude judgment call: Stage 2 agent explicitly asked to check for existing incidents
#    matching entity + act before creating a new card

# Time window:
# - No hard cutoff for same-entity + same-act grouping
# - Court cases and criminal proceedings consolidated regardless of time gap
# - Separate incidents of same type (two different stabbings, different people) NOT consolidated

# Related incident flagging:
# - Different entity + shared victim, location unit, or event → flag as possible related
# - Confidence score attached to flag (0.0–1.0)
# - Confidence >= 0.75 → War Room shows prominent "possible related" banner
# - Confidence < 0.75 → War Room shows subtle "might be related" hint
```

#### Schema Additions for Consolidation

```sql
-- Add to incidents table
ALTER TABLE incidents ADD COLUMN is_developing     BOOLEAN DEFAULT FALSE;
-- TRUE when incident has received updates after initial publication

ALTER TABLE incidents ADD COLUMN update_count      INTEGER DEFAULT 0;
-- Increments each time a new source report is consolidated into this card

ALTER TABLE incidents ADD COLUMN source_timeline   JSONB DEFAULT '[]';
-- Array of {date, headline, source_url, source_name} — chronological log of all reports
-- Example:
-- [
--   {"date": "2023-11-16", "headline": "Kurt Tay jailed for sharing intimate video", "source_url": "...", "source_name": "Straits Times"},
--   {"date": "2024-01-10", "headline": "Kurt Tay faces new charges", "source_url": "...", "source_name": "CNA"},
--   {"date": "2026-04-01", "headline": "Kurt Tay jailed and fined", "source_url": "...", "source_name": "Mothership"}
-- ]

ALTER TABLE incidents ADD COLUMN first_reported_at DATE;
-- Date of earliest source report — separate from incident_date (which is always latest)
```

```sql
-- New table: incident_links
CREATE TABLE incident_links (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  incident_a      UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  incident_b      UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  link_type       TEXT NOT NULL CHECK (link_type IN ('related', 'follow_up', 'same_location')),
  confidence      DECIMAL(3,2) NOT NULL,        -- agent confidence in the link
  agent_reason    TEXT NOT NULL,                -- one-sentence explanation
  confirmed_by_operator BOOLEAN DEFAULT FALSE,  -- FALSE = pending review, TRUE = confirmed
  rejected_by_operator  BOOLEAN DEFAULT FALSE,  -- TRUE = operator dismissed
  UNIQUE(incident_a, incident_b)
);

CREATE INDEX idx_links_incident_a ON incident_links(incident_a);
CREATE INDEX idx_links_incident_b ON incident_links(incident_b);
CREATE INDEX idx_links_pending ON incident_links(confirmed_by_operator, rejected_by_operator) 
  WHERE confirmed_by_operator = FALSE AND rejected_by_operator = FALSE;
```

#### Developing Story — Card Behaviour

When `is_developing = TRUE`:

**Public feed:**
- Card floats to top of incident list regardless of `incident_date`
- **DEVELOPING** badge shown (amber, pixel art style)
- Card date shows latest report date
- Source timeline visible as expandable log: "N reports · First reported [date]"

**Map:**
- Pin reappears in the month/year of latest update
- Historical pins for earlier reports retained but dimmed
- Clicking any pin shows the single consolidated card

**Narrative:**
- Stage 2 agent regenerates summary chronologically on each update
- New narrative reads as a developing story: "Initially reported as X, later confirmed Y, final outcome Z"
- Pixel art image regenerates only if classification or severity changes — not on every update

**Chaos Crystal:**
- Incident counted once regardless of update count
- chaos_contribution does not increase with updates

#### War Room — Update Review Flow

When the consolidation agent identifies a new report matching an existing incident:

```
New report arrives in scraper queue
    ↓
Consolidation agent: match found (Rule 1 — same entity + same act)
    ↓
War Room queue item created with status: 'update'
Badge: "NEW UPDATE — [existing incident title]"
Shows:
  - Existing incident card (read-only preview)
  - New source report (raw + proposed narrative update)
  - Proposed new summary (chronological, updated)
  - Source timeline with new entry appended
    ↓
Operator actions:
  [CONFIRM UPDATE] → merges new source into existing incident, sets is_developing = TRUE
  [REJECT UPDATE]  → discards new source, existing incident unchanged
  [SPLIT INTO NEW] → creates new separate incident card instead
```

When agent flags a related incident (Rule 2):

```
New incident created (separate card)
War Room shows: "POSSIBLE RELATED — [other incident title] (confidence: 0.85)"
Agent reason: "Shares entity [intimate video case] with overlapping date window"
Operator actions:
  [CONFIRM LINK]   → creates incident_links row (confirmed_by_operator = TRUE)
  [DISMISS]        → creates incident_links row (rejected_by_operator = TRUE)
```

#### Public — Related Incidents Display

On incident detail page `/incidents/[slug]`:
- "Related incidents" section at bottom
- Shows only `confirmed_by_operator = TRUE` links
- Each related incident: title + classification icon + date + one-line description
- Bidirectional — if A links to B, B also shows A in its related section

### 4.6 Source Discovery Agent

**File:** `packages/agents/scrapers/scrape_discovery.py`

```python
# Runs: First Monday of every month via APScheduler
# Method: Web search for Yishun news coverage from unknown sources
# Output: Candidate sources logged to sources table with approved_by_operator = FALSE
# Operator sees candidates in War Room under "New Sources" tab
# Nothing is scraped until operator sets approved_by_operator = TRUE
```

---

### 4.7 Wikipedia Discovery Agent (v1.8 — new)

**File:** `packages/agents/scrapers/wikipedia_discovery.py`

**Role:** Primary discovery source for pre-2003 incidents. Secondary cross-reference for all eras. Runs BEFORE any Google News backfill batch. Results bypass Stage 1 (content is already editorially curated — no noise to reject).

**NOT** a live pipeline agent. Backfill only. Run manually per session.

```python
# Entry point: https://en.wikipedia.org/wiki/Yishun
# Strategy:
#   1. Fetch Yishun article — extract all named incidents with dates
#   2. Follow internal wikilinks to case-specific articles (depth: 2 hops max)
#      Do NOT recurse beyond case articles into sub-links
#   3. For each case article:
#      - Extract: date, entity name, act/incident type, outcome
#      - Extract all external citations (footnotes) → these become source_urls
#      - Minimum 1 external citation required — skip if zero
#   4. Package as structured incident candidate dict
#   5. Pass directly to Stage 2 writer (BYPASS Stage 1)
#   6. Results enter War Room queue tagged BACKFILL + WIKIPEDIA

# CLI:
# python -m scrapers.wikipedia_discovery --dry-run
# python -m scrapers.wikipedia_discovery --limit 50
# python -m scrapers.wikipedia_discovery (no args = full sweep)

# Output format (same schema as backfill_agent.py candidates):
# {
#   "title": str,
#   "source_urls": [str],          # from Wikipedia footnotes, min 1
#   "incident_date": str,          # ISO date, best estimate from article
#   "wikipedia_url": str,          # the case article URL, added as reference
#   "source": "wikipedia",
#   "bypass_stage1": True
# }

# Stage 2 prompt modifier for Wikipedia sourced items:
# Add to prompt: "Source is a Wikipedia article. Extract verifiable incident
# details only. Do NOT add facts not in the citations. If the external citations
# are paywalled/dead, still use the citation URL as source_url — the article
# reference is the verification record."
```

**Scope:** Finds landmark/named cases only. Volume expectation: 15–40 incidents across all eras. Does not surface minor/unnamed incidents — that is correct and expected.

**EDMW note:** Wikipedia articles for Yishun incidents predate EDMW's relevance. Do not attempt EDMW cross-reference for Wikipedia-sourced incidents.

---

### 4.8 Groq Budget Middleware (v1.8 — new)

**File:** `packages/agents/scrapers/groq_budget.py`

Tracks cumulative Groq token usage within a single backfill session. Prevents mid-run failures from hitting the 500k TPD ceiling.

```python
# Usage: imported by backfill_agent.py, wraps every Stage 1 call

SOFT_LIMIT = 450_000   # tokens — log warning, continue
HARD_LIMIT = 500_000   # tokens — halt run, write summary, exit cleanly

# Tracks: prompt_tokens + completion_tokens per call
# Source: Groq API response.usage fields

# On SOFT_LIMIT hit:
#   - Log: "WARNING: Groq budget at 450k. Approaching daily limit."
#   - Continue processing

# On HARD_LIMIT hit:
#   - Log: "HALT: Groq daily limit reached. Stopping run."
#   - Write groq_session_usage.json with: tokens_used, items_processed,
#     items_passed, items_rejected, timestamp, year_range
#   - Exit cleanly (no partial writes, no corruption)

# groq_session_usage.json written to: packages/agents/scrapers/groq_session_usage.json
# Overwritten each run — check it after each batch to track daily consumption

# Wikipedia runs: groq_budget is imported but bypass_stage1=True skips
#   all Stage 1 calls, so zero tokens are consumed. Counter stays at 0.
```

---

### 4.9 Forward-Looking Ingestion Architecture (v1.9 — Option B)

> **Full engineering design:** `docs/INGESTION_DESIGN.md`. This section is the spec-level
> summary and the authoritative pointer. The design doc is the detailed contract; if the two
> disagree, reconcile deliberately and bump both.

**Purpose.** The forward-looking ingestion layer autonomously discovers *new* Yishun incidents
on an ongoing basis and queues them for operator review. It is distinct from historical backfill
(`backfill_agent.py`, now complete) and from the per-source live scrapers in §4.0b (which, as
built, never had a working trigger — see §11.2).

**Package:** `packages/agents/ingestion/` (new). Layout in `INGESTION_DESIGN.md` §10.

**The single entrypoint (drift-resistant seam):**
```python
run_ingestion_pass(sources: list[Source], now: datetime, *, dry_run=False) -> IngestionReport
```
Any trigger — Cloud Scheduler (recommended, see §11.2), a CLI command, a manual re-run, or a
test harness — calls this identically. It owns no knowledge of how it was triggered. It is a
pure function of `(sources, now)` + external state, **never raises to its caller**, and writes
nothing when `dry_run=True`.

**Flow per source (see INGESTION_DESIGN.md §2, §4):**
1. read watermark from `pipeline_state` (§3.7)
2. `source.fetch(since=watermark)` → `list[Candidate]`
3. **RecencyFilter** — keep only items strictly newer than watermark (the source's date hints
   are advisory; the orchestrator re-filters regardless, because Google News RSS was proven to
   ignore date operators and return a relevance-ranked multi-year grab-bag)
4. **Deduplicator** — reuse existing `check_duplicate(url)` (corroboration.py) against
   `war_room_queue` + `incidents` by canonical URL
5. **Stage 1 (Groq, budget-guarded via §4.8) → Stage 2 (Claude)** → proposed_* fields
6. write `war_room_queue` rows (`status='pending'`) — terminates at the existing §3.5 contract
7. advance watermark **only on success**, to max `published_at` ingested

**Source interface (pluggability).** Every source implements a common `Source` protocol
(`name`, `enabled`, `fetch(since)`); the first concrete adapter is `GoogleNewsRSSSource`
(smoke-test-proven: **no `after:`/`before:` date operators** — they break the RSS feed — one
`yishun {keyword}` query per keyword, parse `published_parsed`, resolve redirects, raise
`SourceBlockedError` on 429/403/CAPTCHA markers). Future adapters (SerpAPI, Bing, wiring the
existing MSM scrapers behind this interface) drop in with zero orchestrator changes.

**FallbackLadder (no silent failure).** Transient error → one backoff → retry once → skip;
bot-trap (`SourceBlockedError`) → skip immediately, **never retry into a ban**. Any skip marks
the whole pass **DEGRADED**: healthy sources still queue their items, but a `DegradedRunReport`
is surfaced to War Room and the blocked source's watermark is left unchanged so the next run
re-attempts the same window. A zero-queue pass is only "healthy-quiet" if every source was
NORMAL; a zero-queue pass caused by a block is DEGRADED and says so. (INGESTION_DESIGN.md §6–7.)

**Corroboration & budget.** Honours §4.4 (min 1 MSM/Reddit source to auto-publish) and §4.8
(Groq budget middleware wraps every Stage 1 call). No new corroboration logic.

**Explicitly deferred (anti-scope-creep, INGESTION_DESIGN.md §9):** the trigger infrastructure
itself (§11.2 — separate task), fuzzy/semantic dedup (URL-exact only in v1), additional source
adapters, auto-disabling unhealthy sources, and the `people_profiles` entity system.

---

## 5. WAR ROOM CMS

**Path:** `apps/war-room/`

**Access control:** Protected by Cloudflare Access. No public route. Operator authenticates via Cloudflare zero-trust (email OTP or GitHub SSO). Never expose war room URL publicly.

### 5.1 Queue View

```
/war-room/queue
```

Shows all `war_room_queue` entries with `status = 'pending'`, sorted by `created_at DESC`.

**Queue item types and badges:**
- Standard new incident — no badge
- **MILESTONE** badge (amber) — milestone herald item
- **NEW UPDATE** badge (cyan) — update to existing developing story
- **POSSIBLE RELATED** banner — agent flagged link to another incident, pending confirmation

**Default view (Option B — fast review):**
- Confidence score badge — green ≥0.85, yellow ≥0.5, red <0.5
- Classification icon + severity diamonds + hype meter ⚡
- Editable title field (pre-filled with agent draft, 90 char limit)
- Editable summary textarea (pre-filled, 500–800 chars SEO prose)
- Editable classification + severity selectors
- Editable pixel art prompt
- Corroboration count + EDMW signal count (labelled "Forum buzz" — never "source")
- Source links clickable below — collapsed by default

**"View Source" toggle (Option A — on demand):**
- Toggle button on every card to expand raw source
- Auto-expands when confidence < 0.85 — agent is less certain, you should verify
- Side-by-side layout when expanded: raw article (read-only, left) + editable draft (right)
- Collapses after approval or manual dismiss

**Confidence threshold behaviour:**
- Confidence ≥0.85 → B view default, source collapsed
- Confidence <0.85 → A view default, source auto-expanded
- Operator can always toggle either direction manually

**Actions (standard incident):**
- **Approve** → publishes incident, logs training signal (action: 'approve')
- **Edit & Approve** → saves all edited fields, logs training signal (action: 'edit_approve', operator_changes: JSON diff of every changed field — this is the primary training signal)
- **Reject** → dropdown: noise / duplicate / unverified / too thin / legal risk → logs training signal (action: 'reject', reject_reason)

**Actions (NEW UPDATE item):**
- **Confirm Update** → merges new source into existing incident, sets is_developing = TRUE, regenerates narrative
- **Reject Update** → discards new source, existing incident unchanged
- **Split Into New** → creates new separate incident card instead of merging

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

### 6.0 Layout — One Page, No Scroll (v1.7)

**Rule:** The entire site fits in one viewport. No page-level scrolling. Ever.

```
Header (72px fixed top — logo + nav)
┌─────────────────────────────────────┬──────────────────┐
│  MAP AREA (45vh)                    │  CHAOS SIDEBAR   │
│                                     │  (fixed 280px)   │
├─────────────────────────────────────┤                  │
│  FILTER CHIPS (48px)                │  Always visible  │
├─────────────────────────────────────┤  Never collapses │
│  INCIDENT FEED (flex-1, min-h-0)    │                  │
│  Scrolls internally                 │                  │
│  Page does NOT scroll               │                  │
└─────────────────────────────────────┴──────────────────┘
```

```css
html, body { height: 100%; overflow: hidden; }
```

The incident feed uses `flex-1 min-h-0 overflow-y-auto` — fills remaining height, scrolls internally.

### 6.0b Typography Scale (v1.7 — locked)

**Two typefaces only:**
- `Press Start 2P` — logo, section headers, badges, button labels, scores
- `Courier Prime` — all body text, stats, dates, descriptions, summaries

**Size scale:**
| Element | Font | Size |
|---|---|---|
| Logo (YISHUN / AGAIN) | Press Start 2P | 26px |
| Nav links | Press Start 2P | 14px |
| Section headers | Press Start 2P | 11px |
| Chaos score number | Press Start 2P | 48px |
| "/100" | Press Start 2P | 20px |
| Descriptor (QUIET etc) | Press Start 2P | 13px |
| Stat counts | Press Start 2P | 20px |
| Filter chip labels | Press Start 2P | 10px |
| Badges | Press Start 2P | 9px |
| Incident titles | Courier Prime bold | 16px |
| Body text / summaries | Courier Prime | 16px |
| Metadata (date, area) | Courier Prime | 13px |
| Legal disclaimer | Courier Prime | 10px (exception) |

**Rule:** No font size below 10px except legal disclaimer.

### 6.0c Colour Palette (v1.7 — locked)

```css
:root {
  --color-bg:        #183828;   /* deep teal — page background */
  --color-surface:   #0f2018;   /* darker — cards, dropdowns */
  --color-border:    #305830;   /* forest green — all borders */
  --color-amber:     #C07830;   /* primary accent — logo, scores, links */
  --color-sienna:    #803018;   /* active states */
  --color-dim:       #805828;   /* dimmed amber — /100, metadata */
  --color-heart:     #E87070;   /* GOOD VIBES count */
  --color-clown:     #E8C070;   /* ABSURDITIES count */
  --color-dagger:    #E87070;   /* DARK EVENTS count */
  --font-display:    'Press Start 2P', monospace;
  --font-body:       'Courier Prime', 'Courier New', monospace;
}
```

### 6.0d Classification Display Names (v1.7 — locked)

| DB value | Display label | Emoji |
|---|---|---|
| `heart` | GOOD VIBES | ❤️ |
| `clown` | ABSURDITIES | 🤡 |
| `dagger` | DARK EVENTS | 💀 |

**Rule:** Never show raw DB values (`heart`, `clown`, `dagger`) in any UI.

### 6.0e Tooltips (v1.7)

All classification icons, severity diamonds, and hype meter icons must have hover tooltips via `title` attribute:
- ❤️ → "Good Vibes — community wins and feel-good moments"
- 🤡 → "Absurdities — baffling or inexplicably stupid behaviour"
- 💀 → "Dark Events — crime, violence, serious incidents"
- ⚡ → "Hype meter — number of mainstream media sources reporting this"
- ◆ severity → "Severity N/5"

Apply to: IncidentCard, incident detail page, Timeline, map popup.


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
  --font-body: 'Courier Prime', 'Courier New', monospace;         /* typewriter for body */
}
```

**Font:** Import both 'Press Start 2P' and 'Courier Prime' from Google Fonts. Press Start 2P for headers/scores. Courier Prime for all body text.

### 6.1b Map Marker Interaction Flow

```
User lands on homepage
    ↓
[HOVER over incident pin]
    → Teaser popup appears:
      - Incident title (max 2 lines, truncated)
      - Classification icon (❤️ / 🤡 / 🗡️)
      - Severity diamonds (1–5)
      - Hype meter (⚡ 0–5, hidden if 0)
    → Designed as clickbait — just enough to pull the click

[CLICK pin or popup]
    → Navigate to /incidents/[seo-slug]

[MOUSE LEAVES pin without clicking]
    → Popup dismisses, map returns to default state
```

Popup implementation: MapLibre GL JS `mouseenter` / `mouseleave` events on marker layer. Popup rendered as a DOM overlay, not inside the canvas.


### 6.1b Typography Rules (Font Discipline)

**Max 2 typefaces:**
- `Press Start 2P` — headlines, scores, logo only
- `Courier Prime` (Google Font, with `Courier New` as fallback) — all body text, labels, meta

**Max 3 font sizes:**
- Large: 24–28px — Chaos score, logo
- Medium: 11–12px — incident titles, nav items
- Small: 8–10px — meta labels, dates, badges

**Max 2 weights:** 400 regular, 700 bold. No 500, no 600.

**Never use:** System fonts, sans-serif fonts, variable fonts, additional Google Fonts beyond Press Start 2P.

**Chaos Index score display:** Score number and `/100` must be the same colour (amber `#C07830`) so both read clearly against the dark background. Never dim the `/100` separately.



### 6.1c Chaos Panel — Enhanced Depth

The Chaos Panel on the homepage gets additional depth beyond the basic index score.

**Section 1 — Chaos Crystal (existing)**
- Composite score 0–100
- 30D / 90D / 1YR / ALL tabs
- ATB bars for each classification

**Section 2 — Vital Statistics (new)**
Running year-to-date counts displayed as pixel art stat blocks:

```
💀 Deaths this year:        [N]
🩸 Injuries this year:      [N]
❤️ Feel-good moments:       [N]
🤡 Absurd incidents:        [N]
🗡️ Crimes logged:           [N]
```

**Section 3 — Death-Free Counter (new)**
```
[N] days
without a confirmed fatality
Since [date] · [source link of last death]
```

Counter styling:
- Large pixel font number — same style as Chaos Index score
- Amber colour when streak < 30 days
- Green colour when streak >= 30 days
- Special pulsing effect when streak >= 100 days
- Resets to red flash animation when broken

**Section 4 — Year Selector + History Tab (new)**

Year dropdown at top of Chaos Panel:
- Defaults to current year
- Shows all years with data
- Selecting a year filters:
  - All vital statistics to that year
  - Chaos Index score to that year
  - Death-free counter shows that year's longest streak
  - Incident feed filters to that year

History Tab (separate from 30D/90D/1YR/ALL tabs):
- Shows year-by-year Chaos Index ladder
- Bar chart of annual scores — current year vs all previous years
- Each year shows: score, deaths, injuries, feel-good, absurd, crime counts
- Clicking a year row filters the entire panel to that year

**Incident Feed — Year Filter**
When a year is selected in the Chaos Panel:
- Incident feed below filters to show only incidents from that year
- Feed header shows: "Showing incidents from [year] · [N] total"
- Clear/reset button returns to "All time / latest first"


### 6.1b — end

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

**Page structure (top to bottom):**
1. **Title** — SEO-optimised, includes "Yishun" and incident descriptor
2. **AI-generated pixel art image** — below title as supporting visual. Agent reads all sources, generates prompt, LoRA renders image via Modal.run
3. **Merged SEO summary** — all sources consolidated into one keyword-dense prose narrative. Written for Google ranking on long-tail incident queries. No bullet points — structured prose only.
4. **Source links** — all clickable, labelled by outlet name (CNA, Mothership, etc.)
5. **Classification + severity + hype meter** — displayed as visual indicators
6. **Share icon** — copies truncated SEO-friendly slug URL
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

**No separate card image generated.** Share card is rendered via OG meta tags automatically when URL is pasted in Telegram or WhatsApp.

Share icon on incident page copies the SEO slug URL:
```
https://yishunagain.com/incidents/yishun-stabbing-cooking-smells-jan-2024
```

OG meta tags on incident page render the preview:
```html
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{first 155 chars of merged summary}" />
<meta property="og:image" content="{ai_generated_pixel_art_url}" />
<meta property="og:url" content="https://yishunagain.com/incidents/{slug}" />
```

When pasted in Telegram/WhatsApp → platform fetches OG tags → renders card preview automatically.
No Modal.run call needed for share card. Pixel art image (already generated for the incident page) doubles as the share card visual.

UTM tracking still applied via URL params when share icon is tapped:
```
https://yishunagain.com/incidents/{slug}?utm_source=share&utm_medium=og
```

---

## 7. CHAOS INDEX COMPUTATION

**Canonical name:** CHAOS INDEX (previously "Chaos Crystal", "Chaos Meter" — v1.7 final)

**Compute on:** Every new incident published. Store snapshot in `chaos_index_snapshots`.
**Filter by:** Selected year (not rolling window — window tabs removed in v1.7)

```python
def compute_chaos_index(year: int) -> float:
    """
    Weights:
    - dagger (DARK EVENTS):  severity * 3.0
    - clown  (ABSURDITIES):  severity * 1.5
    - heart  (GOOD VIBES):   severity * -1.0 (positive news reduces score)
    
    Normalised to 0–100.
    Max theoretical score (all severity-5 daggers): 100
    Filtered by EXTRACT(YEAR FROM incident_date) = year
    """
    incidents = get_incidents_for_year(year)
    
    raw_score = 0
    for inc in incidents:
        if inc.classification == 'dagger':
            raw_score += inc.severity * 3.0
        elif inc.classification == 'clown':
            raw_score += inc.severity * 1.5
        elif inc.classification == 'heart':
            raw_score -= inc.severity * 1.0
    
    # Normalise: assume max 100 incidents at max weight in year
    normalised = min(100, max(0, (raw_score / 300) * 100))
    return round(normalised, 2)

# Hype meter computation (run during corroboration step)
def compute_hype_meter(source_urls: list, edmw_signal: bool) -> int:
    """
    0 = no MSM sources (EDMW/Reddit only)
    1-5 = count of unique MSM sources in source_urls (capped at 5)
    """
    MSM_SOURCES = ["channelnewsasia", "straitstimes", "mothership", "stomp",
                   "mustsharenews", "theindependent", "zaobao", "shinmin",
                   "beritaharian", "tamilmurasu", "yahoo", "asiaone"]
    msm_count = sum(1 for url in source_urls if any(s in url for s in MSM_SOURCES))
    return min(5, msm_count)

DESCRIPTORS = {
    (0, 20):   "QUIET",
    (20, 40):  "RESTLESS",
    (40, 60):  "TENSE",
    (60, 80):  "VOLATILE",
    (80, 100): "CHAOS"
}
```

**Year rollover (automatic):**
- Default year = current year (new Date().getFullYear())
- On January 1 00:00 SGT: new year becomes default, previous year stats frozen
- No data migration — stats always computed live from incidents table
- chaos_index_snapshots table stores daily snapshots for historical reference

**Removed in v1.7:** 30D / 90D / 1YR / ALL window tabs — score is always per-year

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


## 8b. Scraper Health & Governance System

### Phase A — Visibility (BUILD NOW, before launch)

**New table: `scraper_health`**

```sql
CREATE TABLE scraper_health (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scraped_at      TIMESTAMPTZ DEFAULT NOW(),
  source_name     TEXT NOT NULL,
  source_type     TEXT NOT NULL,
  items_found     INTEGER DEFAULT 0,
  items_passed_s1 INTEGER DEFAULT 0,
  errors          TEXT[],
  duration_ms     INTEGER,
  status          TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
  status_reason   TEXT,
  consecutive_zeros INTEGER DEFAULT 0,
  avg_duration_7d INTEGER
);

CREATE INDEX idx_health_source ON scraper_health(source_name, scraped_at DESC);
CREATE INDEX idx_health_status ON scraper_health(status, scraped_at DESC);
```

**What gets logged:**
Every scraper run (success or failure) logs one row. Agent pipeline calls `log_scraper_run()` after each scraper in `scrape_all()`.

Status rules:
- `ok` — items_found > 0 OR source had no Yishun content (expected)
- `warning` — 0 items for 3+ consecutive runs, or duration > 3x baseline
- `error` — exception thrown, HTTP 4xx/5xx, timeout

**War Room: Health Tab**

New page at `/health` in War Room:

```
PIPELINE HEALTH — [timestamp]

🟢 HEALTHY (N)   🟡 WARNING (N)   🔴 ERROR (N)

Per-scraper traffic light table:
Source | Last Run | Items | Status | Consecutive Zeros | Avg Duration | Action
```

Daily summary card on War Room queue page:
- Total items scraped last 24h
- Passed Stage 1 count
- Queued for review count
- Any red/yellow scrapers flagged prominently

**No autonomous action in Phase A. Visibility only.**

---

### Phase B — Anomaly Detection (BACKLOG — build after 2 weeks of Phase A data)

An agent compares each scraper run to a 7-day rolling baseline.

Flags:
- 0 results for 3+ consecutive runs with confidence score (expected vs blocked)
- Response time +300% above baseline
- HTTP status changes (200 → 403 = likely block, 200 → 301 = URL change)

Adds diagnosis to War Room health report with confidence score. No autonomous action yet.

---

### Phase C — Autonomous Repair (BACKLOG — build after Phase B is trusted)

Small fixes only. Every fix is:
- Logged to `scraper_health` with before/after diff
- Reversible (old code kept as backup)
- Visible in War Room regardless of outcome

Autonomous fix scope:
- RSS URL changed (301 detected) → agent updates URL, reruns, validates
- User-agent blocked → agent rotates user-agent string, reruns, validates

Big changes always escalate to War Room CTA:
- Consistent 403 block → suggests fix, requires operator approval
- Source gone dark → recommends kill, requires operator approval

---

### Phase D — Governance (BACKLOG — build after Phase C is trusted)

Full scraper lifecycle management via War Room:

**New source pipeline:**
1. Discovery agent finds candidate
2. Agent validates: reachable + has historical Yishun content + not duplicate + passes 3 test scrapes
3. Only after all 4 pass → escalates to War Room "New Source" CTA
4. Operator approves → agent builds scraper draft → operator reviews code → deploy

**Kill scraper pipeline:**
1. Agent detects: N consecutive errors OR source has been dark for X days
2. Agent builds case: last successful run, total items contributed, replacement available?
3. Escalates to War Room "Kill Scraper" CTA with full audit trail
4. Operator approves → scraper removed from pipeline

**Tech stack change:**
Agent flags only. Operator prompts human (me) to evaluate and act.

**War Room report format (Phase D):**

```
PIPELINE HEALTH REPORT — [date] [time] SGT

🟢 HEALTHY (N)   🟡 WARNING (N)   🔴 BROKEN (N)

🔴 BROKEN
  → [Source]: [diagnosis]
    Agent suggested fix: [fix]
    [APPROVE FIX] [DISMISS] [KILL SCRAPER]

🟡 WARNING
  → [Source]: [diagnosis]
    Confidence: [score]
    [MARK AS EXPECTED] [INVESTIGATE]

📊 LAST 24H
  Scraped: N | Passed Stage 1: N | Queued: N
  New sources pending review: N
```

**Governance policy — change classification:**

| Change | Phase | Who acts |
|---|---|---|
| RSS URL changed (301 redirect) | C | Agent auto-fixes |
| 0 results 3+ consecutive runs | C | Agent investigates, reports |
| New source discovered | D | Agent validates → operator approves |
| Consistent 403 block | C/D | Agent reports + suggests fix → operator approves |
| Kill existing scraper | D | Agent recommends → operator must approve |
| Add new scraper to pipeline | D | Agent builds draft → operator reviews → deploy |
| Change tech stack component | D | Agent flags → operator prompts human council |



## 8c. Milestone Herald System

### Overview

An agent monitors key thresholds and auto-generates shareable milestone content when triggered. Every milestone includes the date, the triggering incident title, and the source link. Milestone posts are the primary virality flywheel — designed to be screenshot-shared on WhatsApp and Telegram.

---

### Schema Additions

**Add to `incidents` table:**
```sql
ALTER TABLE incidents ADD COLUMN is_milestone     BOOLEAN DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN milestone_type   TEXT;
-- Types: 'streak_broken', 'streak_milestone', 'chaos_record',
--        'chaos_quiet', 'incident_count', 'first_of_year', 'year_chaos_record'
ALTER TABLE incidents ADD COLUMN milestone_value  INTEGER;
-- The number: days, count, or score depending on type
```

**New `milestones` table:**
```sql
CREATE TABLE milestones (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  type            TEXT NOT NULL,
  value           INTEGER NOT NULL,
  incident_id     UUID REFERENCES incidents(id),   -- the milestone incident post
  triggered_by    UUID REFERENCES incidents(id),   -- incident that caused it
  triggered_date  DATE NOT NULL,
  source_url      TEXT NOT NULL,                   -- source link of triggering incident
  notified_at     TIMESTAMPTZ
);
```

**Add to `incidents` table — deaths and injuries tracking:**
```sql
ALTER TABLE incidents ADD COLUMN deaths    INTEGER DEFAULT 0;
ALTER TABLE incidents ADD COLUMN injuries  INTEGER DEFAULT 0;
-- Agent extracts from source text. NULL if not mentioned. 0 if confirmed none.
-- Death counter resets ONLY on confirmed death (deaths >= 1). Never on suspected.
```

---

### Milestone Triggers

| Trigger | Classification | Template |
|---|---|---|
| Death-free streak hits 30 days | ❤️ Heart | "Yishun hits 30 days without a fatality. [date] · [source]" |
| Death-free streak hits 50 days | ❤️ Heart | "Yishun hits 50 days without a fatality. [date] · [source]" |
| Death-free streak hits 100 days | ❤️ Heart | "Yishun hits 100 days without a fatality. We're as shocked as you are. [date] · [source]" |
| Death-free streak hits 200 days | ❤️ Heart | "Yishun hits 200 days without a fatality. [date] · [source]" |
| Death-free streak hits 300 days | ❤️ Heart | "Yishun hits 300 days without a fatality. [date] · [source]" |
| Death-free streak hits 300+ days | ❤️ Heart | "Hell has frozen over. Day [N] of Yishun being suspiciously quiet. [date] · [source]" |
| Death-free streak broken | 🗡️ Dagger | Badge on triggering incident. NOT a separate post. Share card reads: "Yishun's [N]-day streak just ended. [date] · [incident title] · [source]" |
| Chaos Index hits all-time high | 🗡️ Dagger | "Yishun Chaos Index reaches [N] — highest ever recorded. [date] · Triggered by: [incident title] · [source]" |
| Chaos Index drops below 20 | ❤️ Heart | "Yishun quieter than it's been in [N] years. [date] · [source]" |
| 100th / 500th / 1000th incident | 🤡 Clown | "Yishun Again logs its [N]th incident. Still going. [date] · [source]" |
| First incident of new year | Any | "Yishun wastes no time in [year]. [date] · [incident title] · [source]" |
| Year Chaos Index exceeds previous year | 🗡️ Dagger | "Yishun's [year] already worse than all of [year-1]. [date] · Triggered by: [incident title] · [source]" |

---

### Streak Broken — Badge Behaviour

When the death-free counter resets:
- The triggering incident gets `is_milestone = TRUE`, `milestone_type = 'streak_broken'`, `milestone_value = [days broken]`
- The share card for that incident shows a special badge: **"⚡ STREAK BREAKER — ended [N]-day run"**
- No separate milestone post created — the incident IS the milestone
- The milestone's date, incident title, and source link are displayed on the share card

---

### Tone Rules for Herald Agent

The milestone content must match site voice — dry, deadpan, never sensational, never mocking victims:

| Type | Tone | Example |
|---|---|---|
| Streak milestone (good) | Dry surprise | "Yishun hits 100 days without a fatality. We're as shocked as you are." |
| Streak broken | Deadpan, no celebration | "The streak ends at 89 days. Block 412, Yishun Ave 4." |
| Chaos record | Matter-of-fact | "New record. The bar was already low." |
| Incident count | Self-aware absurdity | "Incident #500 logged. Yishun remains committed." |
| First of year | Wry | "Yishun wastes no time in 2026." |
| 300+ days peace | Punny | "Hell has frozen over. Day [N] of Yishun being suspiciously quiet." |

**Never:** sensational, mocking victims, celebrating harm.
**Always:** raised eyebrow, not pointed finger.

---

### Herald Agent Flow

```
Incident published
    ↓
Herald agent checks all milestone thresholds
    ↓
Threshold crossed?
    ↓ YES
Draft milestone content (title, summary, share card text)
Include: date + incident title + source link
    ↓
Send to War Room queue — flagged as MILESTONE (priority badge)
    ↓
Operator approves / edits — one tap
    ↓
Published — share card auto-generated
OG tags optimised for WhatsApp/Telegram viral sharing
```

---

### War Room — Milestone Queue Treatment

Milestone items in War Room queue display:
- **MILESTONE** badge in amber
- Milestone type label (e.g. "STREAK — 100 days")
- Triggered by: incident title + source link + date
- Pre-drafted content editable before approval
- Share card preview shown before approval

---

### Death-Free Counter — Data Rules

- Counter increments daily at midnight SGT
- Resets ONLY on `deaths >= 1` confirmed in published incident
- Never resets on `injuries` alone
- Never resets on suspected/unconfirmed deaths
- Source must explicitly confirm fatality before agent sets `deaths = 1`
- Counter display: "N days without a confirmed fatality · Since [date] · [source of last death]"


## 9. ART PIPELINE

### 9.1 Style Lock

**Status: TRAINED AND DEPLOYED ✅**

LoRA trained on 112 pixel art images (16-bit JRPG aesthetic, mixed characters and backgrounds).
- **Trigger word:** `yishunpixel`
- **Weights:** `https://assets.yishunagain.com/lora/yishunagain_v1.safetensors` (456.5 MB)
- **Base model:** `stabilityai/stable-diffusion-xl-base-1.0`
- **Training:** 1500 steps, lr=1e-4, network_dim=32, network_alpha=16, A10G GPU on Modal.run
- **Generation time:** ~12 seconds on A10G (30 diffusion steps)
- **Output size:** 1024×1024 → resized to 1200×630 for OG share card dimensions

**Known issue:** `avr_loss=nan` during training — kohya_ss logging quirk, does not affect output quality. Generation confirmed working via test image.

**Iteration plan (post-launch):**
- Add HDB-specific training images (void decks, corridors, hawker centres) for more hyperlocal outputs
- Retrain LoRA v2 with Singapore-specific environment prompts
- Operator approval required before any retraining

### 9.2 Image Generation Call

**File:** `packages/agents/art/generate_pixel_art.py`

```python
# Prompt construction:
PROMPT_TEMPLATE = "yishunpixel, 16-bit pixel art, {scene_description}, HDB void deck, Singapore, {mood}, isometric, JRPG style, masterpiece"
NEGATIVE_PROMPT = "photorealistic, 3d render, photograph, blurry, people faces, realistic"

# LoRA scale: 0.85 (confirmed working)
# cross_attention_kwargs={"scale": 0.85} — diffusers 0.26.x API

# Output: uploaded to R2 at pixel-art/{slug}.png
# Public URL: https://assets.yishunagain.com/pixel-art/{slug}.png

# Generation triggered: after operator approves incident in War Room
# Fire-and-forget: does not block War Room UI
# On failure: incident publishes with placeholder, pixel_art_url set to None
```

**Classification → mood mapping:**
```python
MOOD_MAP = {
    "dagger": "dark atmospheric lighting, crime scene, night time, ominous",
    "clown":  "bright comedic lighting, chaotic scene, absurd elements",
    "heart":  "warm golden lighting, community gathering, cheerful atmosphere"
}
```

---

## 10. ENVIRONMENT VARIABLES

> ⚠️ **CRITICAL SECURITY RULES — READ BEFORE TOUCHING ANY KEY**
> - **Never commit `.env` files to GitHub.** Add `.env`, `.env.local`, `.env.production` to `.gitignore` before writing a single key anywhere.
> - **`SUPABASE_SECRET_KEY` is your master key.** It bypasses all Row Level Security. If leaked, anyone can read, write, and delete your entire database. It goes in Google Cloud Run environment variables ONLY.
> - **`ANTHROPIC_API_KEY` and `GROQ_API_KEY`** go in Cloud Run env vars only. Never in frontend code.
> - **`NEXT_PUBLIC_*` variables** are the only ones safe to expose — they are intentionally public and embedded in the frontend bundle.
> - **Rotate any key immediately** if you accidentally commit it. Assume it is compromised the moment it touches a public repo.

```bash
# Supabase (new key system — publishable replaces anon, secret replaces service_role)
# SUPABASE_PUBLISHABLE_KEY → safe for frontend (respects RLS). Use as NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY in Next.js.
# SUPABASE_SECRET_KEY → server/agents only. Never expose to frontend. Never commit to GitHub.
SUPABASE_URL=
SUPABASE_PUBLISHABLE_KEY=           # public reads
SUPABASE_SECRET_KEY=   # MASTER KEY — never expose to frontend, never commit to GitHub, never in .env files in repo. Cloud Run env vars only.

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


## 10b. SECURITY REQUIREMENTS

### 10b.1 API Route Proxy Pattern (No Exposed Endpoints)

**Rule:** All Supabase and external API calls go through Next.js API routes. Never call Supabase directly from frontend components.

```javascript
// ❌ WRONG — never do this in a React component
const { data } = await supabase.from('incidents').select('*')

// ✅ CORRECT — call your own API route instead
const res = await fetch('/api/incidents')
const data = await res.json()
```

API routes live in `apps/web/pages/api/` or `apps/web/app/api/`. They use `SUPABASE_SECRET_KEY` server-side. The frontend never sees the key.

Only `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and `NEXT_PUBLIC_MAPLIBRE_STYLE` are exposed to the browser. Everything else stays server-side.

### 10b.2 Security Headers — `next.config.js`

Add this exact block to `apps/web/next.config.js`. It is pre-configured for the Yishun Again stack — Supabase, MapLibre, OpenFreeMap, Cloudflare, Modal.run.

```javascript
// apps/web/next.config.js
const securityHeaders = [
  {
    key: 'X-DNS-Prefetch-Control',
    value: 'on'
  },
  {
    key: 'Strict-Transport-Security',
    value: 'max-age=63072000; includeSubDomains; preload'
  },
  {
    key: 'X-Frame-Options',
    value: 'DENY'
  },
  {
    key: 'X-Content-Type-Options',
    value: 'nosniff'
  },
  {
    key: 'Referrer-Policy',
    value: 'strict-origin-when-cross-origin'
  },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=()'
  },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      // MapLibre GL JS + WebGL
      "script-src 'self' 'unsafe-eval'",
      // MapLibre inline styles + pixel art CSS
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      // Google Fonts
      "font-src 'self' https://fonts.gstatic.com",
      // OpenFreeMap tiles + Cloudflare R2 assets
      "img-src 'self' data: blob: https://tiles.openfreemap.org https://assets.yishunagain.com",
      // MapLibre tiles
      "connect-src 'self' https://*.supabase.co https://tiles.openfreemap.org https://assets.yishunagain.com",
      // MapLibre WebGL workers
      "worker-src blob:",
      // Cloudflare Stream (Phase 2)
      "media-src 'self' https://videodelivery.net",
      "frame-ancestors 'none'",
    ].join('; ')
  }
]

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: securityHeaders,
      },
    ]
  },
  // Disable x-powered-by header (hides Next.js version)
  poweredByHeader: false,
}

module.exports = nextConfig
```

### 10b.3 API Route Rate Limiting

Add rate limiting to all API routes using `upstash/ratelimit` (free tier) or simple in-memory limiting for Phase 1:

```javascript
// apps/web/lib/rateLimit.js
const rateLimitMap = new Map()

export function rateLimit(ip, limit = 60, windowMs = 60000) {
  const now = Date.now()
  const windowStart = now - windowMs
  const requests = rateLimitMap.get(ip) || []
  const recentRequests = requests.filter(time => time > windowStart)
  
  if (recentRequests.length >= limit) {
    return { success: false, remaining: 0 }
  }
  
  recentRequests.push(now)
  rateLimitMap.set(ip, recentRequests)
  return { success: true, remaining: limit - recentRequests.length }
}

// Usage in any API route:
// const { success } = rateLimit(req.headers['x-forwarded-for'] || 'unknown')
// if (!success) return res.status(429).json({ error: 'Too many requests' })
```

### 10b.4 Input Sanitisation

All query params on API routes must be sanitised before hitting the database:

```javascript
// Slug param — alphanumeric + hyphens only
const slug = req.query.slug?.replace(/[^a-z0-9-]/g, '') || ''

// ID param — UUID format only
const id = req.query.id?.match(/^[0-9a-f-]{36}$/) ? req.query.id : null

// Never pass raw user input to Supabase queries
```

### 10b.5 Cloudflare WAF Rules

Configure these in Cloudflare dashboard → Security → WAF → Custom Rules:

| Rule | Action |
|---|---|
| Block requests to `/api/*` from known bot ASNs | Block |
| Rate limit `/api/*` to 100 req/min per IP | Block |
| Block requests with SQLi patterns in query params | Block |
| Block requests to `warroom.yishunagain.com` not from Cloudflare Access | Block |
| Challenge traffic from high-risk countries to `/api/*` | Managed Challenge |

### 10b.6 General Security Checklist

- [ ] HTTPS enforced via Cloudflare (HTTP → HTTPS redirect ON)
- [ ] HSTS enabled (`Strict-Transport-Security` header — see 10b.2)
- [ ] `X-Frame-Options: DENY` — prevents clickjacking
- [ ] `X-Content-Type-Options: nosniff` — prevents MIME sniffing
- [ ] `poweredByHeader: false` — hides Next.js version
- [ ] No sensitive data in URL params or query strings
- [ ] No `console.log` of API keys or tokens in production
- [ ] Supabase RLS policies active on all tables
- [ ] War Room protected by Cloudflare Access (zero-trust)
- [ ] `.env` files in `.gitignore` — verified before first push
- [ ] Dependency audit: `npm audit` before deploy


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

> 🔧 **v1.9 TRIGGER CORRECTION — the documented scheduling model does not work as built.**
>
> The deploy above uses `--min-instances 0`, which scales the container to **zero between HTTP
> requests**. The live-pipeline scheduling described in §4.1/§4.6 relies on **in-process
> APScheduler timers "embedded in FastAPI"** (§2 tech-stack table). These are mutually
> incompatible: when the instance scales to zero, all in-process timers are killed. **Under this
> deployment, no scheduled scrape can fire autonomously.** This was never reconciled in v1.4–1.8.
>
> **Required fix (separate deployment task — see `docs/INGESTION_DESIGN.md` §1):** replace
> in-process scheduling with **Cloud Scheduler → authenticated HTTP push → a `/run/ingest`
> endpoint** on this service. Cloud Scheduler (managed cron) issues a request on a schedule;
> Cloud Run wakes, runs **one** `run_ingestion_pass()` (§4.9), writes to `war_room_queue`, and
> scales back to zero. This is the standard serverless-cron pattern, costs negligibly, and
> resolves the `min-instances 0` contradiction.
>
> The same pattern applies to the existing weekly `lifecycle.py` 180-day timeout sweep (§13b)
> and the monthly source-discovery run (§4.6): all should be Cloud Scheduler → HTTP endpoints,
> not in-process timers.
>
> **The §4.9 ingestion layer is deliberately trigger-agnostic** — `run_ingestion_pass()` can be
> called by Cloud Scheduler, a CLI, or a test harness identically. This keeps the (rot-prone)
> trigger infrastructure decoupled from the (verifiable) ingestion logic. Implementing the Cloud
> Scheduler resources is a deployment task; it is **blocking for live autonomy** but not for the
> ingestion code or for a manual/CLI launch.

### 11.3 Cloudflare Setup

**Status: CONFIGURED ✅**

1. ✅ Domain on Cloudflare (nameservers)
2. ✅ R2 bucket: `yishun-assets` → custom domain `assets.yishunagain.com` → **live, 200 OK**
3. ✅ Cloudflare Access: protecting `warroom.yishunagain.com` (operator email only)
4. WAF rules: block non-SG traffic from War Room subdomain (optional hardening, post-launch)
5. Page Rules: cache all `/incidents/*` pages aggressively (post-launch)

**War Room middleware:** `apps/war-room/middleware.ts` — checks `cf-access-authenticated-user-email` header in production, bypasses in dev, `/api/health` always exempt. See `docs/WAR_ROOM_DEPLOY.md` for full setup guide.

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
5. **War Room is never accessible without Cloudflare Access authentication.** No bypass route. No API endpoint without secret key.
6. **`utm_events` stores no IP addresses, no cookies, no persistent user identifiers.**

---

## 13b. DEVELOPING STORY LIFECYCLE (v1.6)

### States
```
STATIC → DEVELOPING → CONCLUDED
```

- **STATIC:** Single source, update_count = 0. Never gets DEVELOPING badge.
- **DEVELOPING:** update_count >= 1 AND latest_source_role NOT IN ('verdict', 'timeout'). Floats to top of public feed. Shows DEVELOPING badge (amber, Press Start 2P 9px).
- **CONCLUDED:** latest_source_role = 'verdict' OR 'timeout'. Shows normally in feed. Source timeline still visible on detail page.

### Source Role Values
| Role | Meaning | Triggers |
|---|---|---|
| `initial` | First report of incident | Always on first insert |
| `update` | New development, case ongoing | Agent detects continuation |
| `verdict` | Court outcome or confirmed resolution | Triggers CONCLUDED state |
| `correction` | Earlier report was wrong | Replaces earlier entry |
| `follow_up` | Tangential development, same entity | Related but not core |
| `timeout` | Auto-concluded after 180 days no updates | Weekly cron |

### Role Assignment
- Agent proposes role with confidence score
- War Room shows role dropdown on every update review
- Operator confirms or corrects
- Corrections logged to training_signals (agent_role_proposed, operator_role_confirmed)

### 180-Day Timeout
- Weekly cron (Monday 00:00 SGT) — `packages/agents/classifiers/lifecycle.py`
- Auto-concludes developing stories with no updates in 180 days
- Sets: is_developing=FALSE, latest_source_role='timeout', conclusion_type='timeout'
- Creates War Room AUTO-CONCLUDED notification for operator review
- Operator can CONFIRM CLOSE or REOPEN

### Chaos Index
- Counts incident once regardless of update count
- Map: pin reappears in month of latest update when is_developing=TRUE

---

## 13c. PATTERN DETECTION (v1.6)

**File:** `packages/agents/classifiers/pattern_detection.py`

Runs after every incident publish + daily batch at 06:00 SGT.

Three pattern types:
- **Entity:** Same named entity, 3+ separate incidents, 365-day window
- **Crime type:** Same classification + severity, 3+ incidents, 90-day window, same area
- **Location:** 5+ incidents, same area_name, 90-day window

Deduplication: skips if same pattern_value alerted in last 30 days.

War Room: PATTERN ALERT (orange badge). Actions: [LINK INCIDENTS] [NOTE FOR PROFILE] [DISMISS]

---

## 13d. AUTONOMY GRADUATION SYSTEM (v1.6)

**File:** `packages/agents/classifiers/autonomy_tracker.py`

Eight tracked signal categories with graduation thresholds:

| Category | Min Samples | Max Error Rate | Unlocks |
|---|---|---|---|
| entity_dedup | 20 | 5% | Auto-dismiss same-entity-different-act links |
| location_dedup | 20 | 5% | Auto-dismiss coincidental location links |
| temporal_dedup | 15 | 8% | Auto-dismiss timeframe-only links |
| entity_extraction | 25 | 3% | Trust agent entity matching |
| confidence_threshold | 30 | 10% | Auto-approve high-confidence links |
| role_assignment | 20 | 5% | Auto-assign source roles |
| classification | 50 | 8% | Auto-classify without confirmation |
| severity | 50 | 10% | Auto-assign severity |

**Dismiss reason taxonomy (6 categories):**
- SAME_ENTITY_DIFFERENT_ACT → autonomy_signal: entity_dedup
- LOCATION_COINCIDENCE → autonomy_signal: location_dedup
- TEMPORAL_COINCIDENCE → autonomy_signal: temporal_dedup
- WRONG_ENTITY_MATCH → autonomy_signal: entity_extraction
- INSUFFICIENT_EVIDENCE → autonomy_signal: confidence_threshold
- OTHER → autonomy_signal: other

War Room analytics page shows graduation status per category. Operator dismiss includes mandatory category selection + optional free text (max 200 chars).

**Recalibration:** After 20 corrections of same type → writes calibration_log.json → Stage 2 Sonnet prompt injects top 3 known mistakes as negative examples.

---

## 14. BUILD ORDER (Phase 1)

```
Step 1:  ✅ Supabase schema — all tables, indexes, RLS policies
Step 2:  ✅ Cloudflare R2 + domain (assets.yishunagain.com live)
Step 3:  ✅ FastAPI on Cloud Run — health check live
Step 4:  ✅ Stage 1 filter (Groq llama-3.1-8b-instant)
Step 5:  ✅ Stage 2 writer (Claude Haiku + Sonnet)
Step 6:  ✅ All 14 scrapers built and tested
Step 7:  ✅ War Room CMS — 4 pages + /health tab
Step 8:  ✅ Pipeline dry run confirmed
Step 8a: ✅ Scraper health Phase A
Step 8b: ✅ Schema additions (deaths, injuries, milestones)
Step 8c: ✅ Milestone Herald Agent
Step 9:  ✅ LangGraph orchestrator — 6-node graph
Step 10: ✅ Next.js frontend — map, Chaos Panel, feed, detail pages
Step 11: ✅ Frontend wired to Supabase — live data confirmed
Step 12: ✅ Share card + UTM logging
Step 13: ✅ Art pipeline — LoRA trained + deployed, generation ~12s
Step 14: ✅ SEO — sitemap, robots.txt, JSON-LD
Step 15: ✅ Cloudflare Access — War Room protected
Step 16: ⏳ Historical backfill + hero incidents
         ✅ Hero incidents SQL written (migration 005) — 8 incidents
         ⚠️  Hero incidents NOT in DB — database was wiped clean. Re-insert before backfill.
         ✅ Backfill agent built (backfill_agent.py) — intra-batch dedup, geocoding, link validator
         ✅ Intra-batch dedup — dry-run tested, confirmed working
         ⏳ wikipedia_discovery.py — NOT BUILT (see §4.7)
         ⏳ groq_budget.py — NOT BUILT (see §4.8)
         ⏳ backfill_agent.py refactor — --year-from/--year-to/--bypass-stage1 not yet added
         ⏳ Wikipedia sweep (Step 16 Run 1)
         ⏳ Google News 2003–2012 (Step 16 Run 2)
         ⏳ Google News 2013–2022 (Step 16 Run 3)
         ⏳ Google News 2023–2025 (Step 16 Run 4)
         ⏳ War Room review of queued backfill items
Step 17: ⏳ Launch ignition sequence

v1.6 refactor: ✅ Lifecycle agent, pattern detection, autonomy tracker,
               dismiss taxonomy, recalibration system
v1.7 refactor: ✅ UI overhaul, death counter removed, classification
               display renames, Chaos Index canonical name
v1.8 refactor: ⏳ Backfill scope expansion, Wikipedia discovery agent,
               Groq budget middleware, backfill_agent.py year-range refactor
```

## 14d. TECH DEBT LOG (v1.8)

| Item | Severity | Status | Notes |
|---|---|---|---|
| Pin geocoding precision | Medium | Backlogged | Block 349 + Block 323 overlap on map — OneMap returns street centroid not block |
| `revalidate = 0` on homepage | Medium | ✅ Fixed (commit c709b32) | Changed to 60 in apps/web/app/page.tsx |
| `items_passed_s1` always 0 in scraper_health | Low | Backlogged | Stage 1 counts not passed back per-source |
| `avr_loss=nan` in LoRA training | Low | Monitor | Logging quirk, generation works |
| Wikipedia scraper untested live | Medium | ⏳ Pending | `--year wiki` mode exists in backfill_agent.py but quota killed dry run before it ran. Test before first Wikipedia sweep. |
| Backfill intra-batch dedup untested live | Medium | ⏳ Pending | Dry-run confirmed working. Needs live test WITH hero incidents in DB. |
| REVALIDATE_SECRET not in Vercel | Medium | ✅ Fixed | Added to Vercel env vars |
| Map tiles style clashes with dark palette | Medium | Tomorrow | Replacing map entirely |
| Groq free tier (500k TPD) | Medium | Managed | groq_budget.py (to be built) enforces 450k soft / 500k hard ceiling |
| Hero incidents not in DB | High | ⚠️ Blocking backfill | DB wiped clean. Re-insert migration 005 before any backfill run. |
| backfill_agent.py single-year only | High | ⚠️ Blocks year-range runs | --year YYYY needs refactor to --year-from/--year-to |
| wikipedia_discovery.py not built | High | ⚠️ Blocks Wikipedia sweep | Build §4.7 or verify --year wiki mode in backfill_agent.py is functional |
| groq_budget.py not built | High | ⚠️ Blocks Google News runs | Build §4.8 before any Google News batch |
| scrape_jom.cpython-311.pyc in pycache | Low | Cosmetic | Artifact from before Jom scraper was dropped. Harmless. |
| HWZ historical date filter untested | Low | Backlogged | Uncertain if HWZ search supports date range filtering. Test during 2015 batch. Do not block other runs waiting for this. |
| Art direction unresolved | Medium | Paused | CivitAI model selection pending. Does not block backfill. |
| warroom.yishunagain.com DNS | Medium | ⏳ Pending | War Room subdomain DNS not yet configured. |

---

## 14c. Step 16 — Historical Backfill Specification (v1.8)

### Scope

**Full backfill target:** 1980–2025. Yishun Town was established ~1983. Pre-1983 incidents are astronomically rare and handled only if Wikipedia surfaces them.

**Source strategy by era:**

| Era | Primary source | Secondary | EDMW signal |
|---|---|---|---|
| 1980–2002 | Wikipedia discovery agent only | Wikipedia citations → ST/CNA/BBC as `source_urls` | No |
| 2003–2014 | Google News via `backfill_agent.py` | Wikipedia cross-ref via consolidation agent | No |
| 2015–2025 | Google News via `backfill_agent.py` | Wikipedia cross-ref | Yes — if HWZ thread found, link it |
| 2026–today | Live pipeline (existing) | Reddit scouring (live) | Yes |

**Reddit is NOT used for historical backfill.** Pushshift is dead. Reddit's native API is lossy for pre-2015 content. Reddit remains in the live pipeline only.

**NLB NewspaperSG is NOT used.** No public API. Post-1989 content requires on-site library terminal access. Not automatable. Drop it.

---

### Pre-Backfill Checklist — MUST complete before any run

```
☐ 1. Re-insert hero incidents — run migration 005 SQL
      DB was wiped clean. Hero incidents MUST be in `incidents` table before
      any backfill run. The consolidation agent checks incoming articles against
      existing published incidents. Without hero incidents, the consolidation
      agent has nothing to match against — you will get duplicate cards for
      Kurt Tay, the cat killings, and the 1992 murders.

☐ 2. Inspect backfill_agent.py Wikipedia mode
      Run: python -m scrapers.backfill_agent --year wiki --dry-run --limit 5
      Confirm Wikipedia mode fetches real content and outputs structured candidates.
      If mode is a stub or broken → build wikipedia_discovery.py (§4.7) instead.

☐ 3. Verify intra-batch dedup with hero incidents in DB
      Run: python -m scrapers.backfill_agent --dry-run --limit 50 --year-from 2015 --year-to 2015
      The Nov 2015 Yishun flat murder should appear as ONE consolidated card,
      not 4 separate cards. Dedup was built and dry-run tested but never ran
      with hero incidents present in the DB.

☐ 4. Confirm groq_budget.py is built and wired into backfill_agent.py
      Every Google News run must have the token ceiling enforced.
```

---

### Run Order

```
Step 0:  Pre-backfill checklist above — all 4 items green before proceeding
Step 1:  Wikipedia sweep (zero Groq cost, all eras, bypass Stage 1)
Step 2:  Google News 2003–2012 (sparse era, one run)
Step 3:  Google News 2013–2022 (higher volume, monitor token counter)
Step 4:  Google News 2023–2025 (overlaps old hardcoded range — dedup critical)
```

---

### Backfill Agent — CLI Reference (v1.8)

**File:** `packages/agents/scrapers/backfill_agent.py`

```bash
# Wikipedia sweep (bypasses Stage 1, zero Groq cost)
python -m scrapers.backfill_agent --year wiki --limit 100

# Google News — year range
python -m scrapers.backfill_agent --year-from 2003 --year-to 2012 --limit 500
python -m scrapers.backfill_agent --year-from 2013 --year-to 2022 --limit 500
python -m scrapers.backfill_agent --year-from 2023 --year-to 2025 --limit 500

# Dry run (no writes to DB, prints summary only)
python -m scrapers.backfill_agent --year-from 2015 --year-to 2015 --dry-run --limit 50

# Flags
# --year wiki         → Wikipedia mode. Bypasses Stage 1. Uses internal Wikipedia
#                       crawl logic (or wikipedia_discovery.py if standalone).
# --year-from YYYY    → Start year for Google News range (replaces --year single)
# --year-to YYYY      → End year for Google News range
# --limit N           → Max articles to process per run (default 500)
# --bypass-stage1     → Skip Stage 1 entirely, send direct to Stage 2
#                       (used automatically when --year wiki)
# --dry-run           → No DB writes. Print pipeline output only.
```

**Refactor required (v1.8):**
- Replace `--year YYYY` (single year) with `--year-from` / `--year-to` (range)
- Add `--bypass-stage1` flag
- Wire in `groq_budget.py` — all Stage 1 calls must go through budget wrapper
- Keep `--year wiki` and `--dry-run` unchanged

---

### Groq TPD Ceiling

```
Daily limit:       500,000 tokens (Groq free tier)
Soft stop:         450,000 tokens — log warning, continue
Hard stop:         500,000 tokens — halt cleanly, write groq_session_usage.json
Avg cost/article:  ~700 tokens (Stage 1 prompt + completion)
Max articles/day:  ~640 (at hard stop)
After 60% rejection: ~256 incidents reach Stage 2 per day

Wikipedia runs consume ZERO Groq tokens (Stage 1 bypassed).
Stage 2 (Claude Haiku/Sonnet) runs on Anthropic API — separate budget, not Groq.
```

---

### Google News Search URL

```python
# Base URL (unchanged from v1.7):
BASE_URL = (
    "https://news.google.com/search"
    "?q=yishun+{keyword}"
    "&hl=en-SG&gl=SG&ceid=SG:en"
    "&tbm=nws"
    "&tbs=cdr:1,cd_min:{year_from}-01-01,cd_max:{year_to}-12-31"
)

# Loop: year range × YISHUN_KEYWORDS
# Dedup by URL before Stage 1
# Rate limit: 1 request/second
# Max per run: --limit (default 500)
```

---

### EDMW in Backfill (2015 onward)

EDMW scraper (`scrape_edmw.py`) is a live pipeline agent with no reliable historical archive. During Google News backfill runs for 2015+, if the scraper finds an HWZ thread matching a candidate incident by keyword + approximate date, add the thread URL as an `edmw_signal` reference (never as `source_url`). This is best-effort — HWZ's search index depth is unknown and untested for historical content.

HWZ is lowest priority in backfill. Do not block any run waiting for EDMW signal resolution.

---

### Auto-Approve Tiers (unchanged from built version)

| Confidence | Action |
|---|---|
| ≥ 0.70 | Auto-publish (no War Room review) |
| 0.50–0.69 | Queue for War Room review |
| < 0.50 | Silent reject (logged but not queued) |

BACKFILL tag applied to all backfill queue items. Bulk approve available for ≥0.85.

---

### Hero Incidents — SQL (migration 005)

These 8 incidents bypass the pipeline entirely. `is_published = TRUE`. Run migration 005 before any backfill. See §14b for full incident list.

**Critical:** Hero incidents must be in the DB before any Google News run so the consolidation agent can correctly merge incoming articles into existing cards rather than creating duplicates.

---

## 14b. Hero Incidents — Pre-Written for Launch

These 8 incidents are hand-written before automated backfill runs. Showcase anchors — highest quality content on the map at launch. They bypass the automated pipeline and are published directly via War Room.

| # | Incident | Date | Classification | Severity |
|---|---|---|---|---|
| 1 | Yishun Cat Killings — 20+ cats killed across estate, Lee Wai Leong charged | Sep 2015–Jan 2016 | 🗡️ Dagger | 4/5 |
| 2 | Yishun Triple Murders — Wang Zhijian stabs 3 women at Block 349 Yishun Ave 11 | Sep 2008 | 🗡️ Dagger | 5/5 |
| 3 | Yishun Taxi Driver Murders — Two cabbies stabbed in secluded Yishun areas, killers hanged | Apr–Dec 1992 | 🗡️ Dagger | 5/5 |
| 4 | Yishun Infant Murder — Mohamed Aliff kills 9-month-old baby in van, life imprisonment | Nov 2019 | 🗡️ Dagger | 5/5 |
| 5 | Kurt Tay void deck fight — Yishun wrestling champ accepts stranger's challenge | Jul 2022 | 🤡 Clown | 2/5 |
| 6 | Kurt Tay jailed — Shared intimate video without consent, fined and jailed | 2023 | 🗡️ Dagger | 3/5 |
| 7 | Yishun noise murder — Koh Ah Hwee stabs Vietnamese woman outside Block 323 in noise row | Sep 2025 | 🗡️ Dagger | 5/5 |
| 8 | Japanese YouTuber visits "dangerous" Yishun, finds it cozy and nice instead | 2023 | 🤡 Clown | 1/5 |

Key sources per incident: ST, CNA, Mothership, BBC, Wikipedia, AsiaOne. All hero incidents require minimum 2 verified source links before publish.

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
