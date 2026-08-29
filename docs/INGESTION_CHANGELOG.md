# Ingestion & Data-Quality Changelog

Companion to TechSpec v1.9. Records the schema additions, data corrections, and
architectural decisions made during the June 2026 consolidation + ingestion-design session
and every session appended since, so the next agent/operator has an honest trail rather
than inferring intent from the DB. Append-only: later sessions correct earlier entries in
place with a dated note rather than rewriting them.

---

## Schema additions (v1.9)

- **`pipeline_state`** (new) — per-source ingestion watermark store. TechSpec §3.7.
- **`pipeline_run_history`** (new) — append-only ingestion run log. TechSpec §3.7.
- **`training_signals`** (extend §3.4) — Learning Loop Phase 1: one row per War Room operator
  decision (approve/reject/re-source/link/correct), shaped so a future LoRA job (Phase 3) can
  consume it directly. See `docs/LEARNING_LOOP.md` §2.1.
- **`source_reputation`** (new) — Learning Loop Phase 1: per-domain trust accumulator, read back
  each run to weight candidate confidence. See `docs/LEARNING_LOOP.md` §2.2.
- **CULTURE content type** — not a schema change; a convention on existing columns:
  `classification='custom'` + `custom_label='CULTURE'`, severity 1, hype_meter 0, excluded
  from the Chaos Index, rendered with a 🌐 "YISHUN ON THE MAP" pin (violet/indigo accent).
  Frontend support added in `apps/web/lib/utils.ts` and `apps/war-room/lib/utils.ts`.

## Architecture decisions

- **Forward-looking ingestion = Option B (trigger-agnostic).** Full design in
  `docs/INGESTION_DESIGN.md` (APPROVED); spec summary in §4.9. Single `run_ingestion_pass()` seam
  decouples rot-prone trigger infra from verifiable ingestion logic.
- **Source model (Q1 = 1b): SG MSM primary, aggregator corroboration.** The direct Singapore
  MSM scrapers are the primary spine behind the `Source` interface; a wider discovery net
  cross-checks and catches misses. ("The main sauce is always Singapore MSM.") Rejected the
  earlier "Google-News-primary" framing.
  > **Amended 2026-08-02.** The decision stands; the corroboration channel changed. Google News
  > RSS was removed and replaced by the publishers' own news sitemaps
  > (`ingestion/sources/news_sitemap.py`) and WordPress search feeds
  > (`ingestion/sources/wp_search.py`) — same reach, publisher-canonical URLs. See the
  > 2026-08-02 section.
- **Dateless candidates (Q2 = 2b): route to War Room, not dropped.** The operator's due-diligence
  (e.g. googling a weak item to find its real source) is recorded as training signal. War Room is
  the sourcing-model's training-data generator, not merely an approval gate.
- **Three-phase scope (Q3 reframed): Cold / Warm / Forward.** Cold Start (1980–2023) = the
  hand-built archive as a prototype the **Historical agent** enriches & discovers against (find
  more proof, enrich existing stories, discover items under existing umbrellas). Warm Start
  (2024–Jun 2026) = litmus-test window the **Futurist agent** scrapes and the operator validates.
  Forward (Jun 2026 →) = daily live pipeline. "Cold start" is NOT a first-run lookback window.
- **Cadence (Q4): daily, cloud docker.** Failed runs / bot traps / recommended after-actions
  reported to War Room (FallbackLadder → DegradedRunReport). Trigger: Cloud Scheduler → HTTP.
- **Learning Loop (`docs/LEARNING_LOOP.md`).** Phase 1 contextual learning IS built (read
  accumulated signal back into prompts/scoring; frozen models). Phase 2 graduated autonomy and
  Phase 3 LoRA fine-tuning are designed/roadmapped, NOT built. **The agent accumulates DATA in
  Supabase; it never modifies its own weights or code.** Human-in-the-loop is permanent;
  crime/named-individual content never auto-publishes. The Learning Loop is the road to the North
  Star (minimal-intervention autonomy = minimal review *volume*, undiminished human *authority*).
- **Two agents.** Historical agent (Cold/Warm enrichment & discovery against the archive) and
  Futurist agent (Forward daily live pipeline; the subject of the Learning Loop). Distinct;
  the Historical agent is not a mode of `run_ingestion_pass()`.
- **Historical scraping abandoned as structurally impossible.** Google News RSS has no historical
  archive (date operators break the feed); GDELT/Yahoo dead. The 2008–2025 archive was built **by
  hand** with court-verified consolidation, governed by `docs/CONSOLIDATION_RULES.md`.
- **Google News RSS smoke test (home IP) passed.** No bot traps at gentle cadence; sub-second
  responses; recent items reliably present but mixed into a relevance-ranked multi-year grab-bag —
  hence the orchestrator must re-filter by watermark, never trust feed order.
  > **Superseded 2026-08-02.** The smoke test measured the right things and still passed; what it
  > never tested was the SHAPE of the URLs the feed emits. `news.google.com/rss/articles/<blob>`
  > wrappers are not article URLs, and that — not bot traps — is what removed the source.

## Data corrections (audit pass, this session)

A fabricated-date / fabricated-URL detector swept all 53 cards. Findings: **zero fabricated
URLs remained**, zero lifecycle mismatches. Three wrong `incident_date` values were corrected
(all were placeholder/stamp artifacts from first-generation backfill, fixed to the real event
date per CONSOLIDATION_RULES rule 2):

- Yishun MRT foreign-worker death: `1990-01-01` → `2006-12-05`
- Murder of Liang Shan Shan (schoolgirl): `2026-06-11` → `1989-10-02`
- JI/al-Qaeda Yishun MRT plot: `2026-06-11` → `2001-12-01`

Earlier in the same session, additional first-gen corrections were applied and committed
(Wang Zhijian fabricated execution dates; Koh Ah Hwee fabricated ST/CNA URLs + wrong dates;
Ghib Ojisan duplicate with a hallucinated 2023 date + fake Reddit URL; Kurt Tay fabricated
Reddit URL; taxi-driver-murders and infant-murder wrong incident_dates).

> **2026-07-30 — see `docs/PIPELINE_CHANGES_2026-07-30.md`** for the cost +
> classification programme: batched grouping (replacing pairwise judging +
> union-find), batched consolidation, the locality veto, the Haiku write model
> with a source-proportional length cap, the groundedness and casualty
> cross-checks, and the `max_tokens` truncation guard. Several items in the list
> below were closed by earlier work and are struck through with the evidence.

## Known deferred items (debt, named not hidden)

- **Forward pipeline BUILT (steps 1–10 complete).** One forward pipeline: `run_ingestion_pass()`
  replaces the retired LangGraph `run_graph()` and the deleted `pipeline.py`. Herald preserved;
  Learning Loop Phase-1 live (and provably cannot override system-prompt guardrails — verified).
  Orchestration is hand-rolled — `ingestion/orchestrator.py` for the pass, `ops/daily.py` for the
  chain. `langgraph` has been removed from `requirements.txt` (2026-08-24 — it was never imported).
- ~~**TRIGGER is the gating item for live autonomy.**~~ ✅ **CLOSED (verified
  2026-07-30).** Cloud Scheduler fires `POST /orchestrator/daily` at 14:58 SGT.
  `baseline_report.py` shows 88 `agent_runs` across 7 agents in a 14-day window,
  including `daily_orchestrator`. APScheduler remains dead under
  `--min-instances 0`, as designed — that is the reason Cloud Scheduler exists,
  not an outstanding gap.
  *(2026-08-29: the single-job in-process APScheduler was removed from `main.py`
  entirely, and APScheduler dropped as a dependency — it was off in prod and
  fully redundant with `POST /orchestrator/daily`. `ENABLE_INPROCESS_SCHEDULER`
  no longer exists.)*
- ~~**MSM adapter coverage — only CNA + Google News RSS exist.**~~ ✅ **CLOSED
  (verified 2026-07-30).** `get_enabled_sources()` returned **15** live sources —
  confirmed by a live pass that session. RSS-dated MSM: CNA, Mothership, Straits
  Times, MustShareNews, The Independent, Yahoo. HTML-scraped MSM: AsiaOne, Stomp,
  Zaobao, Shin Min, Berita Harian, Tamil Murasu. Plus Google News RSS
  (corroboration) and Reddit + EDMW (signal).
  > **Count restated 2026-08-02: 25 sources.** Google News RSS was removed and 11 discovery
  > adapters added (9 news-sitemap + 2 WordPress-search), giving 12 MSM scrapers + 11 discovery
  > + 2 signal. All 14 original scrapers are untouched. See the 2026-08-02 section.
- ~~**MSM adapters swallow errors.**~~ ✅ **CLOSED.** Scrapers now **raise**
  `ScraperError` / `ScraperBlocked` on a source-level failure; the adapters
  translate those to `SourceBlockedError` / `SourceUnavailableError`. An empty
  result therefore means "no Yishun news", not "something broke quietly" — Stomp
  sat silently dead for weeks under the old behaviour.
- **Kurt Tay duplicate** — `kurt-tay-intimate-video-case-2023-2026` (older, placeholder date)
  still live alongside the verified draft `yishun-kurt-tay-intimate-image-conviction-2026`.
  Operator to resolve (keep draft, delete old) in War Room.
- **2 Kurt Tay draft cards** + **6 `incident_links`** await operator confirmation in War Room.
- ~~**Next.js 14.2.3 security patch** on War Room — outstanding (pre-launch).~~ ✅ **CLOSED
  (verified 2026-08-02).** Both apps are on `next@^16.2.12` with `react@^19.2.8`. Anything still
  describing this repo as "Next.js 14" is stale.
- ~~**Repo hygiene** — duplicate `YishunAgain_TechSpec_v1_8.md` at root (delete), reconcile the
  two `CONSOLIDATION_RULES.md` copies (canonical is now `docs/`), `.gitignore` the ~12 loose
  `.log/.txt/.json` backfill artifacts in `packages/agents/`.~~ ✅ **CLOSED (verified
  2026-08-02).** The only Markdown at the repo root is `CLAUDE.md` and `README.md` — no duplicate
  spec, no second `CONSOLIDATION_RULES.md`; `docs/` is the single home for both. `.gitignore`
  covers the backfill artifacts (`packages/agents/*.log`, `backfill_*.txt`, the usage/export
  JSON) and none are left loose in `packages/agents/`.
- ~~**CLAUDE.md** still references the non-existent `YishunAgain_TechSpec_v1.4.md` — repoint to
  `docs/YishunAgain_TechSpec_v1_9.md`.~~ ✅ **CLOSED (verified 2026-08-02).** CLAUDE.md points at
  `docs/YishunAgain_TechSpec_v1_9.md` in both places.
- **North–South Line** title en-dash was normalised to a hyphen during the audit; revert to the
  en-dash if typographic correctness is preferred (cosmetic).

**Track A is closed.** A3 (batched judging), A4 (cluster size-cap decision and
the numeric locality veto) and A5–A9 all landed. **A2 — consolidation prompt
caching — was measured and deliberately NOT implemented** (2026-08-02); the
reasoning is recorded in full at `consolidation/rules.py`, next to
`MAX_JUDGEMENTS_PER_CANDIDATE`. In short, four independent blockers:

1. The comparison pool is filtered and ranked **per candidate** by keyword
   overlap, so there is no byte-identical prefix. Caching is an exact prefix
   match. Sending the unfiltered pool would fix that but changes behaviour,
   which A2 forbade.
2. The filtered prompt measures **3,889 tokens** against Haiku 4.5's **4,096**
   minimum cacheable prefix — the highest minimum of any current model. Below
   it, a `cache_control` marker is silently ignored.
3. The pass averages **3.0 candidates** (2/2/4/6/1 over five passes) and
   consolidation now makes one batched call each. Break-even on a 5-minute-TTL
   cache is ~2 calls, so the saving would be marginal even if 1 and 2 were
   solved.
4. A2's premise — "~87 Haiku calls in 3 minutes" — described pairwise judging.
   A3 already collapsed that to one call per candidate.

---

## June-2026 feed + data-integrity + QA session

A working session covering the public feed, the consolidation pipeline, the War Room,
and a full-codebase QA sweep. Honest trail of what changed and what's still open.

### Schema / migrations
- **008** — `incidents.latest_source_role` CHECK expanded to include `sentencing`,
  `appeal`, `appeal_dismissed` (multi-stage legal stories).
- **009** — `training_signals.action` CHECK expanded to include `unpublish` (the War
  Room unpublish route writes it; before 009 those inserts were silently rejected by
  Postgres and swallowed by supabase-js, so unpublish signals were lost).

### Pipeline
- **Consolidation now dedups against the pending `war_room_queue`, not just published
  incidents** (`consolidation/check.py`). Same-event reports arriving across passes
  before approval collapse to one row via `action='skip'` (orchestrator already drops
  skips). New `QUEUE_FETCH_LIMIT` in `consolidation/rules.py`.
- **Google News URL resolver added** (`scrapers/_gnews_helpers.py`). Modern
  `/rss/articles/CBMi…` links are resolved via Google's `batchexecute` RPC; fully
  exception-guarded (degrades to the raw URL — never breaks a pass).
  > **Corrected 2026-08-02.** This bullet claimed the resolver "restores the `Candidate.url`
  > canonical-URL contract and the cheap URL-exact dedup gate". It did not, and could not: the
  > exception guard degrades to the RAW URL, i.e. it emits the wrapper — which is the contract
  > breach, not the fix. Google rotates the RPC, so the degraded path is not rare. The resolver
  > is still live and still worth having, but only on the HISTORICAL backfill path
  > (`scrapers/backfill_agent.py`); the live discovery source that depended on it is gone.
  > Guard: `test_gnews_resolve.py` (cases 6–10 are all "degrades safely").
- **War Room `confirm-update` date corruption fixed** — it stamped `new Date()` as both
  the merged timeline date and `incident_date`, floating merged stories to the top of
  the feed dated "today". Now uses the candidate's real article date, falling back to
  the incident's existing date (never the future). (PR pending merge at session end.)

### Frontend (feed / incident display)
- **DEVELOPING badge + banner removed.** `is_developing` still drives feed sort + the
  report-count line.
- **Lightning (⚡) = corroboration**, `max(0, corroboration_count − 1)`, derived live in
  card / map popup / detail. Legacy `hype_meter` no longer read.
- **Story timeline collapses same-date nodes**; renders only with 2+ distinct dates.
- **"Time to verdict"** computed from the last verdict/sentencing/appeal entry in
  `source_timeline` (helpers `lastVerdictEntry` / `verdictNoun`), never `incident_date`.
- **War Room draft 404 fixed** — operator-only preview route
  (`apps/war-room/app/incidents/[slug]`) renders drafts via the secret key; the list
  routes Live → public View, Draft → internal Preview.

### Data corrections (live DB, via one-off scripts)
- Resolved every `news.google.com` source URL → real publisher + stamped the real
  article date across published incidents.
- Reconsolidated duplicate 2026 incidents into canonicals (re-queued to War Room as
  `update` candidates); feed 45 → 26 published 2026 rows, Chaos 94 → 51.
- Sourced 10 unsourced heritage cards (operator-supplied references).
- **Pending (`cleanup_corrupted.py --apply`):** recompute `corroboration_count =
  len(source_urls)` (13 stale rows) and repair 8 incidents whose `incident_date` was
  forced to `2026-06-23` by the merge bug.

### QA
- Full-codebase functional QA produced **`docs/QA_BACKLOG.md`** — 39 ranked issues
  (4 Critical, 8 High, 16 Medium, 11 Low) with a fix for each. Headline: 3 of the 4
  "hardcoded — never remove" legal guardrails were not actually enforced in code
  (QA C1/C2/C4), and UTM analytics inserts are blocked by RLS (QA C3).
  > **All three guardrail findings are now closed** (verified against the code 2026-08-02):
  > **C4/#1** by migration `010_qa_hardening.sql` — `CHECK (cardinality(source_urls) >= 1)`;
  > **C2/#2** in `ingestion/orchestrator.py` — a signal candidate gets `source_urls=[]` via
  > `source_allowlist.is_signal_source()`, never a bare `== 'edmw'`;
  > **C1/#4** in `filters/stage2_writer.py::_classify` — `political: true` forces
  > `confidence = 0.0`, and since 2026-08-02 that check runs BEFORE field validation (see below).
  > Guards: `test_stage2_guardrails.py`, `test_political_alert.py`, `test_source_allowlist.py`.

---

## 2026-08-02 — discovery rebuild, geography fix, health recalibration

One session, all of it downstream of the same discovery: the fleet kept reporting zeros and
nothing was broken. The window was too narrow, the keyword list was wrong, and the one
source with the widest reach was emitting URLs that are not articles.

### Ingestion sources

- **`ingestion/sources/google_news_rss.py` DELETED**, and `GoogleNewsRSSSource` is gone
  from `get_enabled_sources()`. It was the dominant discovery channel and the wrong one.
  Its entries link to `news.google.com/rss/articles/CBMi<blob>` **wrappers**, which do not
  HTTP-redirect — decoding one needs a reverse-engineered `batchexecute` RPC that Google
  rotates — so when the resolver failed it stored the wrapper as `Candidate.url`. That
  breaks three things at once:
  1. `Candidate.url` is contractually the canonical article URL because **dedupe reads it**
     (`dedup.is_duplicate` matches on URL). A wrapper matches nothing, so the pipeline
     cannot see that it already holds the story.
  2. The wrapper lands in `war_room_queue.source_url` and in `source_urls` — a published
     incident citing an opaque Google redirect instead of the outlet that reported it.
  3. `source_allowlist` could not classify `news.google.com`, so the row was flagged
     `unapproved_source_domain` and held back from auto-publish anyway.

  All three fired in production on **2026-08-01**: two queue rows proposing "updates" to an
  incident already held, each citing an unresolved wrapper, each a duplicate of a Stomp
  article the Stomp scraper had ingested cleanly the day before.

- **Replaced by two discovery adapter families, both emitting the PUBLISHER's own URL.**
  That is the whole point — an item found by discovery is now indistinguishable downstream
  from one the publisher's own scraper found, so the overlap dedupes instead of
  manufacturing update proposals.
  - **`ingestion/sources/news_sitemap.py` — `NewsSitemapSource`, 9 outlets:**
    `cna_sitemap`, `straits_times_sitemap`, `yahoo_sitemap`, `asiaone_sitemap`,
    `stomp_sitemap`, `zaobao_sitemap`, `berita_harian_sitemap`, `tamil_murasu_sitemap`,
    `the_independent_sitemap`. A publisher's Google-News sitemap is a static XML file it
    maintains for search engines: canonical URL, real publication date, usually a headline.
    No redirects, no RPC, no third party. It is also a far wider window than the front-page
    feeds — measured 2026-08-02, Straits Times served **462 sitemap entries against 44 in
    its RSS**; Zaobao 366, Yahoo 204, Tamil Murasu 113. The ST feed carried zero Yishun
    items that morning; its sitemap carried the Yishun heritage-trail story. Sitemaps carry
    no body, so the article is fetched for the 0–3 entries a day that match the keywords —
    **after** the recency check, never before (`MAX_ARTICLE_FETCHES = 15` is the safety
    valve). Not represented: **Mothership** (no news sitemap — `/sitemap.xml` just
    re-serves `/feed/`) and **Shin Min** (serves no robots.txt and no sitemap at all).
  - **`ingestion/sources/wp_search.py` — `WordPressSearchSource`, 2 sites:**
    `mustsharenews_search` and `the_independent_search`, over `?s=yishun&feed=rss2`.
    WordPress answers that with a real RSS feed of search results — dated entries, canonical
    permalinks, summaries in the body — i.e. a keyword-scoped archive query against the
    publisher's own site. Verified live 2026-08-02: 10 dated entries each. **Mothership is
    NOT covered** — it is WordPress-shaped but ignores `?s=`, returning byte-identical
    output for any term, so there is nothing to search.

- **`get_enabled_sources()` now returns 25 sources:** 12 MSM scrapers + 9 news-sitemap +
  2 WP-search + 2 signal (Reddit, EDMW). **All 14 original scrapers are unchanged** — the
  discovery adapters were added alongside them, not in place of them. The only source
  removed was `GoogleNewsRSSSource`.
  Guard: `test_ingestion_sources.py` (42 checks) — it pins the registry to exactly
  scrapers + discovery, asserts no source name contains "google", and runs every source's
  own URL through `is_redirect_domain()`, so the wrapper channel cannot be re-added quietly.

### Guardrails

- **`classifiers/source_allowlist.py` gains a third rule: `REDIRECT_DOMAINS`.** A frozenset
  of wrapper hosts (`news.google.com`, `google.com`, `feedproxy.google.com`, `t.co`,
  `bit.ly`, `apple.news`, and friends) plus `is_redirect_domain()`. `classify()` now returns
  `'redirect' | 'signal' | 'approved' | 'unapproved'` and checks **redirect FIRST, without
  consulting the `sources` table** — a wrapper host is disqualified on its own terms and
  must stay disqualified even if someone adds it to `sources` by mistake. Matching is
  suffix-aware like the approved list. `check_source_urls()` returns a new
  **`dropped_redirect`** key; redirectors are removed unconditionally, exactly like signal
  URLs, on the same reasoning — a citation must point at the outlet that did the reporting.
  - Dropping can empty `kept`, and that is deliberately not special-cased: a candidate whose
    only citation was a wrapper has no verifiable source, lands in the queue as unverified,
    and `ops/auto_publish.py` holds it as `no_approved_source_after_filter`.
  - **`consolidation/queue_row.py`** now also substitutes a real publisher URL for a
    redirector `source_url` (the row's headline link, and what dedupe matches on), falling
    back to the raw value only so the row is never malformed, and flagging it in
    `raw_content._source_allowlist.redirect_source_url` either way.
  - The rule is enforced where a URL becomes a citation rather than trusted to hold at every
    call site, because the historical backfill and source-discovery paths still touch Google
    News. Guard: `test_source_allowlist.py` (48 checks).

- **Legal guardrail #4 is now evaluated BEFORE field validation** in
  `filters/stage2_writer.py::_classify`. It used to sit below the field coercion, and
  `result["classification"].lower()` threw `AttributeError` whenever the model returned
  `"classification": null` — which is what it tends to do on a political story, because it
  is being told to reject rather than categorise. The guardrail was therefore **unreachable
  for a subset of the very content it exists to catch**: the candidate died on an exception,
  so confidence was never forced to 0, the `[POLITICAL CONTENT DETECTED — REJECT]` marker
  was never prepended, and the operator email and `agent_events` warning row never fired.
  Observed live 2026-08-02 on an MP-resignation article surfaced by the new WordPress search
  source. Political rows now also get a placeholder classification so the reject path can
  complete and alert. Guard: `test_stage2_guardrails.py` (31 checks).

### Keyword scope (the geography fix)

- **`scrapers.YISHUN_KEYWORDS` is now exactly**
  `["yishun", "khatib", "chong pang", "northpoint", "khoo teck puat"]`.
  - **`"sembawang"` REMOVED.** Sembawang is its own URA planning area with its own town
    centre — it is not Yishun and never was. It had been in the list since the first commit
    (`e71d976`, 2026-06-06) while **every TechSpec from v1.5 onward carried the line
    `# NOTE: "sembawang" removed — separate town, not Yishun`**. The spec said it was
    removed; the code never removed it; nothing tested it, so the disagreement stood for two
    months. The cost was real — e.g. the Sembawang Air Base knife-plot arrest (2026-07-28)
    reached the queue for the operator to reject by hand.
  - **`"khatib"` and `"chong pang"` ADDED** — both are subzones inside the Yishun planning
    area, and neither contains the substring "yishun", so neither was matched before.
    Matching is plain case-insensitive substring, so the bare `"yishun"` entry already
    covers "Yishun Ring Road", "Yishun Ave 6", "Yishun MRT" and the like.
  - **`"nee soon"` deliberately excluded from the English list.** The subzone is Yishun, but
    in news copy the phrase is overwhelmingly the CONSTITUENCY (Nee Soon GRC): measured
    against The Independent's search feed on 2026-08-02 its only hit was an article about an
    MP — content guardrail #4 has to reject as political anyway. Every genuine Yishun story
    in that sample already matched on "yishun". It is **retained in the Malay list**, where
    it is a place-name.
  - The scope rule, now pinned by a test: a keyword qualifies only if it names the Yishun
    planning area or something inside it. Adjacent towns do not, however close — no
    Woodlands, Admiralty, Canberra or Sembawang Hills either.
    Guard: **`test_yishun_geography.py`** (37 checks), new this session.

### Observability

- **`ingestion/health.py`: `ZERO_STREAK_WARNING` raised 3 → 30.** `items_found` counts
  candidates that survived the Yishun keyword filter, **not** articles the source served, so
  0 is the normal case — Tamil Murasu or Berita Harian can go a month without a Yishun
  story. At 3, essentially the whole fleet sat at `warning` permanently: on 2026-08-02, **9
  of the then-15 sources** were warning and every one read "0 items for 3 consecutive runs",
  which was reasonably read as a mass scraper failure when nothing had failed. 30 daily
  passes is a month of genuine silence — worth a look, without being the resting state.
  This is a **display signal only**: real failures already surface as `status='error'` (the
  fetch raised), and outage **alerting** derives from `pipeline_run_history` in
  `ops/supervisor.py`, never from `scraper_health`. Guard: `test_scraper_health.py`
  (34 checks).
