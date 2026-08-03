# CONSOLIDATION RULES (v1 — Wang Zhijian prototype)

These rules govern how incidents are corroborated, de-duplicated, dated, and
concluded. Every rule here is a CONSTRAINT that reduces invention. When in
doubt, do less: state what is verified, omit what is not.

They were written for the hand-built 2008–2025 archive and still bind the
operator. The forward pipeline's own consolidation code sits in
`packages/agents/consolidation/`: `rules.py` (thresholds, keyword and locality
extraction) and `check.py` (ONE batched Haiku judgement per candidate against
the recent published + unprocessed-queue pool, returning new / update / skip).
That code decides WHETHER a candidate is a fresh story; the rules below
constrain what the resulting row may claim. Most are operator-enforced — where
a rule has a machine check, it is named.

## DATE RULES
1. Never trust an existing timeline. Verify every date against primary
   sources before writing it. (A prior pass hallucinated execution dates —
   claimed 2010, when court records show the man was alive and appealing in
   2014.) The corrections were applied to the live archive and are listed in
   `docs/INGESTION_CHANGELOG.md` § "Data corrections"; the seed file
   `packages/db/migrations/005_hero_incidents.sql` was never rewritten and
   still carries the pre-correction 2010 execution date. Read it as history,
   never as a source of truth.
2. `incident_date` = when the event actually happened, never when it was
   sentenced or reported. Each legal-process step (charge, trial, verdict,
   appeal) becomes its own `source_timeline` entry on its own date. The role
   vocabulary is fixed by migration 008: `initial`, `update`, `verdict`,
   `sentencing`, `appeal`, `appeal_dismissed`, `correction`, `follow_up`,
   `timeout`. The frontend reads it — "time to verdict" is computed from the
   last verdict/sentencing/appeal entry in `source_timeline`, never from
   `incident_date` (`apps/web/lib/utils.ts`, `VERDICT_ROLES` /
   `lastVerdictEntry`) — so a wrong role is visible on the card.
   Machine check: a candidate whose source date could not be parsed carries
   `raw_content._date_fallback`, and `ops/auto_publish.py` refuses it with
   reason `date_fallback`. Nothing is ever dated "today" to get it published.
3. Court judgments at elitigation.sg are the gold source for any Singapore
   crime that went to trial — reachable and authoritative. Check there first
   for any case with a verdict. A court or government domain must also be
   approved in the `sources` table to clear the allowlist: an unapproved
   citation is KEPT (stripping it could break guardrail #1) but recorded in
   `raw_content._source_allowlist.unapproved`, and the row is held back from
   auto-publish with reason `unapproved_source_domain`.

## CONCLUSION RULES
4. Only accept a conclusion (verdict, sentence, execution, acquittal,
   release) that is EXPLICITLY stated in a source. Never infer it.
5. If the final outcome is not in any reachable source, do NOT fabricate it.
   End the timeline at the last verified fact, and state the limitation
   plainly in the summary text (e.g. "final outcome not publicly reported").

## LIFECYCLE STATE (two states only — keep it simple)
6. CONCLUDED: a verdict, sentence, or clear end-of-legal-road is reported.
   `is_developing = FALSE`, `conclusion_type = 'verdict'`.
7. DEVELOPING: a recent case actively moving through the courts, where new
   reports are expected. `is_developing = TRUE`, `conclusion_type = NULL`.
   (Do not add further states or tags yet. Revisit only when data volume
   genuinely justifies finer distinctions — and prefer letting the pattern
   emerge from the data over inventing categories up front.)
   The column accepts two non-editorial values besides `'verdict'`
   (migration 003 CHECK: `'verdict' | 'timeout' | 'operator'`). `'timeout'` is
   written only by `classifiers/lifecycle.py` when a developing story has had
   no new source for `TIMEOUT_DAYS = 180`; it also sets
   `latest_source_role = 'timeout'` and queues a War Room notification the
   operator confirms (`confirm-close`) or reverses (`reopen`). A timeout
   asserts nothing about the outcome — that is rule 5 expressed in the schema,
   not a third lifecycle state. Auto-conclude is the only agent that edits a
   published incident unattended and is gated OFF by `LIFECYCLE_AUTO_CONCLUDE`
   in `ops/daily.py` (`docs/AUTONOMY.md` §5d).

## SOURCE TIERING
8. `source_urls` stores only authoritative sources: court records and
   established mainstream news. Reference wikis are research aids only — use
   them to spider into deeper links, never store them as citations.
   `seed_backfill.py` types wikipedia.org, grokipedia.com, wiki.sg and
   fandom.com as `reference`; `scrapers/backfill_agent.py` excludes every
   `source_type='reference'` URL when it merges a story's citations, and hands
   Stage 2 `source_urls: []` for a Wikipedia item so a wiki URL cannot return
   as a citation inside the draft. (Wikipedia is seeded in `sources` as
   `type='reference'`, `scrape_interval_minutes=0` — enrichment only, never on
   a scraping schedule. The allowlist does not drop it; the exclusion is by
   `source_type` upstream, so the rule has to be respected by hand wherever a
   citation is written directly.) Four hero rows seeded by migration 005 still
   carry a reference URL in `source_urls`: three cite en.wikipedia.org — the
   taxi-driver-murders row cites nothing else — and the cat-killings row cites
   wiki.sg. They pre-date this rule and are the reason for it.

8a. A REDIRECTOR or aggregator URL is never a citation and never the row's
   headline link. `classifiers/source_allowlist.py` holds `REDIRECT_DOMAINS`
   (news.google.com and its subdomains, google.com, feedproxy.google.com,
   t.co, bit.ly, apple.news, and the other shorteners); `is_redirect_domain()`
   matches suffix-aware, and `classify()` tests it FIRST, without consulting
   the `sources` table, so the rule cannot be defeated by adding the host to
   `sources`. `check_source_urls()` removes them unconditionally — no operator
   discretion, exactly like signal — and reports them under `dropped_redirect`.
   `consolidation/queue_row.py` applies the same rule to the row's headline
   link: when the candidate's own URL is a redirector it substitutes the first
   surviving publisher URL from `kept`, records the original at
   `raw_content._source_allowlist.redirect_source_url`, and logs a warning. If
   no publisher URL survives, the raw value is kept so the row is not
   malformed — the flag is then the operator's only cue, since nothing in the
   War Room renders `_source_allowlist` yet.
   WHY: `news.google.com/rss/articles/<blob>` wrappers do not HTTP-redirect
   (decoding one needs a reverse-engineered `batchexecute` RPC that Google
   rotates), so when resolution failed the WRAPPER was stored as the
   candidate's URL. Two live rows on 2026-08-01 showed all three consequences
   at once: dedupe is URL-exact (`ingestion/dedup.py`), so a wrapper made an
   already-held story look novel; the citation pointed at an opaque redirect
   instead of the outlet that did the reporting; and the row tripped
   `unapproved_source_domain`. An earlier War Room merge bug had already
   written wrapper URLs into `source_timeline` (`cleanup_corrupted.py` exists
   to unpick that). The source that produced them,
   `ingestion/sources/google_news_rss.py`, was deleted on 2026-08-02 and
   replaced by adapters that emit publishers' own URLs (`news_sitemap.py`,
   `wp_search.py`) — this rule is the net under that, because the historical
   backfill and source-discovery paths still touch Google News.
   Guard: `test_source_allowlist.py`. Never weaken.

8b. SIGNAL sources (EDMW/HWZ and Reddit) are never citations — legal guardrail
   #2, unconditional. `check_source_urls()` removes them (`dropped_signal`);
   `is_signal_source()` deliberately tests the declared type under both
   vocabulary spellings AND resolves the URL's domain against `sources`,
   because a bare `== 'edmw'` comparison silently breached the guardrail once.
   A signal candidate reaches Stage 2 with `source_urls: []` and carries only
   `edmw_signal_count`, so it stays in the queue as unverified until an
   operator attaches an MSM source: guardrail #1 requires at least one URL, and
   migration 010 enforces it in the database
   (`CHECK (cardinality(source_urls) >= 1)`). Never weaken.

(8a and 8b are lettered so the rule numbers older docs cite — rule 2 in
`INGESTION_CHANGELOG.md`, rule 11 in `INGESTION_DESIGN.md` — keep pointing at
the same text.)

## PATTERN / PHENOMENON LINKING
9. When multiple incidents share a signature (same act-type, same location,
   same period), create ONE umbrella "phenomenon" card and chain individual
   sourced incidents to it via `incident_links`. The phenomenon card is the
   bigger-picture hub; the individual cards are the evidence. When a NEW
   incident later matches an existing phenomenon's signature, chain it to the
   existing hub rather than creating an orphan card.
   The hub is an EDITORIAL construct built by hand: `incident_links.link_type`
   allows only `'related' | 'follow_up' | 'same_location'` (migration 002),
   there is no phenomenon link type, and the automated pipeline never proposes
   a hub — `IngestionReport.phenomenon_count` is structurally 0
   (`ingestion/orchestrator.py`), and `consolidation/check.py` emits only
   `RelatedLink`s. The War Room's LINK PATTERN action on a pattern alert writes
   all-pairs links between the alerted incidents (`link-pattern/route.ts`),
   which is a mesh, not a hub.
10. An individual card REQUIRES its own source. Never manufacture individual
    cards from an aggregate count (e.g. "35 cats killed") — that count lives
    in the phenomenon card's summary, not as 35 fabricated cards. The chain
    grows only as genuinely sourced individual reports appear.
11. A link is NOT an assertion of sameness. If a source — especially a court
    — explicitly separates an individual from the pattern, the link may still
    exist for context, BUT `agent_reason` must record the distinction. NEVER
    publish that a named person is part of a pattern when a source says
    otherwise. (Highest-priority guardrail — this is a defamation risk.)
    Example: the sentencing judge in the Yishun cat case stated Lee Wai Leong
    should NOT be conflated with the broader series; his card may link to the
    phenomenon for context, but the reason must record that he is the one
    prosecuted case, explicitly distinct from the unsolved pattern.
    `incident_links.agent_reason` is NOT NULL, so every link carries a reason,
    but nothing checks what it says — this rule is operator-enforced. The
    LINK PATTERN route writes a generic reason ("Pattern detected: <type> /
    <value>") and confirms the links immediately, so a case needing this
    distinction must be linked by hand or the reason edited afterwards. Only
    `confirmed_by_operator = TRUE` links are publicly readable (RLS
    `anon_read_confirmed_links`), and the incident page renders the linked card
    alone, never `agent_reason` — so the assertion a reader actually sees is
    the one in the summary text. Write that sentence as carefully as the link.

## META-RULE
12. These rules exist to keep you grounded, not creative. If a rule ever
   seems to require you to guess, infer, or fill a gap, that is the signal
   to STOP and record only what is verified. Saying "outcome not confirmed"
   is always preferable to inventing a plausible-sounding fact.
