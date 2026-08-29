# Autonomy — the daily unattended pass

**Status:** Live · **Companion to:** `LEARNING_LOOP.md`, `INGESTION_DESIGN.md` · **Runbook:** §7 below

How Yishun Again went from "operator runs the pipeline by hand and approves every
card" to "a scheduled fleet runs itself once a day and only interrupts the
operator when something actually needs a person."

---

## 1. What runs, when

One Cloud Scheduler job runs **twice daily at 02:58 and 14:58 SGT** and POSTs to
`/orchestrator/daily` on the `yishun-agents` Cloud Run service. That single
endpoint runs twelve steps in a fixed order (`packages/agents/ops/daily.py`):

| # | Agent | Module | Cadence | Requirement |
|---|---|---|---|---|
| 1 | Recalibration | `classifiers/recalibration.py` | daily | §5.5 |
| 2 | Ingestion pass | `ingestion/orchestrator.py` | daily | — |
| 3 | Auto-publish + review alert | `ops/auto_publish.py` | daily | #3, #4 |
| 4 | Integrity (dupes, hallucinations) | `ops/integrity.py` | daily | #10 |
| 5 | Supervisor (scraper fleet) | `ops/supervisor.py` | daily | #9 |
| 6 | Learning monitor (deltas) | `ops/learning_monitor.py` | daily | #5 |
| 7 | Backend health + cost guard | `ops/backend_health.py` | daily | #12 |
| 8 | Pattern detection | `classifiers/pattern_detection.py` | daily | §5.3 |
| 9 | Lifecycle auto-conclude | `classifiers/lifecycle.py` | **Mondays, opt-in — §5d** | §5.2 |
| 10 | Source discovery | `scrapers/scrape_discovery.py` | first Monday | §4.5 |
| 11 | Maintenance digest | `ops/maintenance.py` | daily | #11 |
| 12 | Monthly report | `ops/monthly_report.py` | the 1st | #13 |

**The order is load-bearing.** Integrity runs *after* publish so it audits what
actually went live. Supervisor runs *after* ingestion so it grades this pass.
Pattern detection runs after publish so today's incidents are in the pool it
scans. Maintenance runs *last* because it reads what every other step logged —
run it earlier and it reports yesterday's news.

**Recalibration runs first, and that is not a filing decision.** It writes
`calibration_log.json`; `filters/stage2_writer._load_calibration_hints` reads
that file *while drafting*, so the two are producer and consumer inside one
pass. The file sits on Cloud Run's ephemeral disk (§6) and the container is
replaced between passes, so hints written after ingestion are destroyed before
any reader exists. Grouped with the other monitors — the obvious-looking place —
the calibration loop looks wired and is a permanent no-op.

**Failure is isolated per step.** A crash in step 4 does not cost you steps
5–12; the monitoring agents matter most precisely when something has broken.
Failed steps are recorded in the run report with a truncated traceback.

**`?dry_run=true` skips every cadence-gated step** (1, 8, 9, 10, 12) rather than
running it. None of those five agents has a read-only mode — they would conclude
incidents, insert pattern alerts, write source rows and upsert a monthly report
for real — and threading an untested `dry_run` through five modules to support
one debugging flag is a worse trade than being explicit. The report says which
steps were skipped and why.

### How steps 1, 8, 9 and 10 got here (2026-07-30)

They existed for months and had **never executed in production even once**.
`main.py` registered them on the in-process APScheduler (since removed), which was
off in production for the reasons in §6 — so pattern alerts were never raised, no story
was ever auto-concluded, no source was ever discovered, and Stage 2's
calibration hints read a file nothing had ever written. Each module was correct
and fully tested; nothing invoked it.

The fix is not "turn the scheduler on" — that costs $15–25/month for
~15 minutes of work a day. It is that the cadence now lives in `ops/daily.py`
and **only** there, reached by the one entry point Cloud Scheduler actually
calls. `main.py`'s `_JOBS` is a single entry. Two places defining one schedule is
what let these drift into being dead code, and there is now one.

`cadence_plan()` is pure and unit-tested (`test_daily_cadence.py`), and every
pass records what it decided in `agent_runs.stats.cadence` — so "why didn't
lifecycle run on Monday?" has an answer in the database rather than requiring
someone to re-derive it from a cron expression.

---

## 2. Auto-publish: the gate, and what it does not override

A draft is published with **no human review** when `agent_confidence >= 0.95`
(`AUTO_PUBLISH_CONFIDENCE`).

> **Deliberate override, recorded.** `LEARNING_LOOP.md` §0 states that crime
> (`dagger`) content and anything naming a living individual never graduate to
> auto-publish at any maturity. The operator has overridden that: the gate is a
> literal confidence threshold with **no classification carve-out**. This is an
> editorial decision by the site's owner, taken knowingly. It is recorded here
> rather than silently implemented, and it is reversible in one env var.

What the threshold does **not** override — the five hardcoded legal guardrails
(`CLAUDE.md`, "Never Remove") and the preconditions the human approve route
already enforces with a 422. A row failing any of these is **not rejected**; it
stays `pending` for the operator. The failure mode is always "a human looks at
it", never "it disappears".

The full set, in the order `check_eligibility` applies them:

| Gate | Skip reason logged | Why |
|---|---|---|
| `status = 'pending'` | `not_pending` | `update` rows merge into a live incident — a different write path (§2b) whose failure mode is corrupting an existing story. Auto-merge is a separate decision, gated behind `AUTO_MERGE_ENABLED` (default OFF); with it off, update rows are held here exactly as before |
| Not a sentinel row | `notification_row` | Pattern alerts and lifecycle notices are operator prompts, not incidents |
| A confidence, at or above the bar | `no_confidence`, `below_threshold` | The threshold itself |
| Title and summary present | `missing_title`, `missing_summary` | The human approve route's own 422 preconditions |
| Not political | `political_marker` | Guardrail #4. Stage 2 forces confidence 0, so this is unreachable; asserted as defence in depth |
| ≥ 1 source URL | `no_source_url` | Guardrail #1, also a DB CHECK (migration 010) |
| Something survives the allowlist | `no_approved_source_after_filter` | Guardrail #2 — a `type='signal'` URL (EDMW/HWZ, Reddit) is stripped unconditionally, and so is a redirect wrapper (`source_allowlist.REDIRECT_DOMAINS`). If that empties the list there is no verifiable source left |
| Operator-approved domain | `unapproved_source_domain` | A URL from an unknown domain is not a *verifiable* source, which is guardrail #1's actual point |
| The allowlist was readable at all | `allowlist_check_failed` | Cannot verify ⇒ cannot claim verifiable |
| Real `incident_date` | `no_real_date`, `date_fallback` | QA H3 — never stamp "today" |
| No ungrounded specifics | `ungrounded_specifics` | Stage 2's deterministic groundedness post-check found a number or proper noun that appears in no source, and one regeneration did not clear it. A factual defect in *this* row, so there is no trust curve — it never clears automatically |
| Casualty figures match the source | `casualty_mismatch` | `filters/casualty_check` — a wrong death count is the most damaging factual error this archive can publish |
| Cluster not oversized, or the grouper has earned it | `oversized_cluster_unproven` | §5b — the one hold that lifts itself |

> **Guardrail #5 is deliberately not in that table.** Image suppression is not an
> eligibility gate: it happens inside `_generate_art`, which returns
> `pixel_art_url: null` and `image_status='suppressed'` for a `suicide` /
> `self-harm` incident (`art/suppression.py`, which fails closed). The card still
> publishes and the frontend placeholder handles the missing image — a gate here
> would withhold the story, which is not what the guardrail asks for. Note that in
> *this* path art generation is itself opt-in: `ART_GENERATION_ENABLED` defaults
> to false, and an unconfigured deployment publishes with `pixel_art_url` null and
> `image_status='pending'` rather than logging a failure per incident.

Two more safety properties:

- **Blast radius cap.** `AUTO_PUBLISH_MAX_PER_RUN` (25) bounds one pass. Excess
  stays pending and the cap is logged as an anomaly.
- **Rollback on a half-write.** If the incident inserts but the queue row fails
  to close, the incident is immediately unpublished. Otherwise the next pass
  would see a still-`pending` row and publish a second copy (QA H2).

**Guardrail #4 fails loud (landed 2026-07-30).** `political: true` forces
`confidence = 0.0`, which on its own meant the incident silently never published
and no notification was raised — under unattended operation the story was lost
without trace, and a zeroed row was indistinguishable from any other
low-confidence one. Stage 2 now also prepends the operator-visible
`[POLITICAL CONTENT DETECTED — REJECT]` marker to the summary and sets a distinct
`_political_flagged` state — a state, not just a number, is what lets the caller
tell the two apart. The orchestrator's `_alert_political` then emits an operator
notification via `ops/notify.py` subject to the dedup ledger (kind `anomaly`,
deduped on the article URL) and writes an `agent_events` row at level `warning`;
it fires *before* the consolidation skip-check, so a political item that also
duplicates an existing row is still reported rather than being the quietest case.
The guardrail was not weakened — it was made audible. Guard:
`test_political_alert.py`.

**And it was still unreachable for some political stories until 2026-08-02.**
The `political` read sat *below* the classify response's field validation, and
`result["classification"].lower()` throws `AttributeError` on
`"classification": null` — which is exactly what the model returns on a political
story, because it is being told to reject rather than categorise. The candidate
died on an exception: no forced confidence 0, no reject marker, no email, no
`agent_events` row. Observed live on an MP-resignation article surfaced by the
WordPress search source. Guardrail #4 is now evaluated **first** in `_classify`,
before any field coercion can raise, and a political row with an unusable
category is given a placeholder so the reject path can finish and alert. Guard:
`test_stage2_guardrails.py`.

> Worth knowing when reading those alerts: this classifier over-triggers on
> ordinary news that merely mentions an MP or the People's Association. The
> prompt already says such stories are NOT political, but a smaller model still
> fires. The notification is what makes that visible rather than silent.

Every auto-publish writes a `training_signals` row with `action='auto_approve'`,
`decided_by='agent'`. That flag is what keeps the agent from grading its own
homework — see §4.

### 2b. Auto-merge: applying an update unattended (`AUTO_MERGE_ENABLED`, default OFF)

An `update` row merges a new source into an **already-published** incident
(appends to `source_urls`/`source_timeline`, bumps `update_count`, recomputes the
dates). That is a live-incident mutation, and a wrong merge is near-invisible —
one extra URL in a source list — and hits the source-integrity constraint. So it
is a **separate, opt-in decision**, off by default. With `AUTO_MERGE_ENABLED`
unset, every update row is held for the operator, unchanged.

When on, a merge auto-applies only when **both** confidences clear **and** the
appended source is verifiable. `check_update_eligibility` (same fail-open
direction — a miss is *held*, never rejected):

| Gate | Skip reason | Why |
|---|---|---|
| It is an update row with a target | `not_update`, `no_update_target` | Nothing to merge into otherwise |
| Draft confidence ≥ `AUTO_MERGE_CONFIDENCE` (0.95) | `below_threshold` | Write quality |
| Same-event confidence ≥ `AUTO_MERGE_MATCH_CONFIDENCE` (0.95) | `no_match_confidence`, `match_below_threshold` | The wrong-merge axis — the strict one. `_match_confidence` is the consolidation grouper's certainty this candidate updates *that* incident, persisted by `consolidation/queue_row.py`. Missing ⇒ held: a row written before it was persisted is never merged blind |
| Appended source survives the allowlist | `source_not_verifiable`, `unapproved_source_domain`, `allowlist_check_failed` | `confirm-update` trusts the operator here; the autonomous path has no operator, so it **re-runs** the guard. A signal/redirect URL disqualifies the merge; an unapproved domain holds it |

Two confidences, not one, because they answer different questions: `agent_confidence`
is "is the draft well-written", `_match_confidence` is "is this the same event".
A merge needs both, and the match one is what the earlier evaluation flagged as
the real risk.

Safety properties mirror auto-publish:

- **Undo net.** Before mutating, `_apply_merge` snapshots the pre-merge incident
  state into `raw_content._undo_snapshot`; the War Room queue's "Recently merged
  updates" panel offers a one-click Undo (`/api/queue/[id]/revert-update`), which
  restores the snapshot. **Do not enable auto-merge without migration 018
  applied** — the snapshot vocabulary and the `auto_update` / `update_reverted`
  training actions live there.
- **Claim before mutate.** The queue row is CAS-claimed `update → update_approved`
  before the incident is touched; a race with an operator confirm/reject loses the
  claim harmlessly. An incident-update failure releases the claim back to `update`.
- **Blast radius cap.** `AUTO_MERGE_MAX_PER_RUN` (25) bounds one pass.
- **Summary refresh is separately gated.** By default the auto path only does the
  mechanical merge and leaves the prose alone. When `AUTO_ENRICH_SUMMARY` is on
  (its own flag, off by default), it *also* applies the ingestion-time enriched
  summary — the existing summary refreshed with the new development
  (`consolidation/enrich.py`) — but ONLY when that enrichment passed the same
  deterministic groundedness check Stage 2 uses, so model-written prose that
  invented a specific never reaches a live incident unattended. The merge
  snapshot captures the pre-merge summary, so `revert-update` restores it if a
  refresh was wrong.

**Update-summary enrichment (§2c).** Every `update` row — auto or manual — now
carries a refreshed summary generated at ingestion: `consolidation/enrich.py`
merges the target incident's existing summary with the new development into one
summary that PRESERVES existing detail and WEAVES IN the new report, screened by
`find_ungrounded`. The War Room `UpdateCard` pre-fills its box with it (`✨
AI-refreshed — review & confirm`, or `⚠ UNGROUNDED — verify` when the gate
flagged it) so the operator reviews a merged refresh instead of a blank box; the
autonomous apply is the `AUTO_ENRICH_SUMMARY` path above. It fails SAFE — no
client, a model failure, or an empty/ungrounded result leaves the existing
summary untouched. This replaces the earlier behaviour where the box pre-filled
with the new source's terse *standalone* draft, which wholesale-replaced the full
summary on confirm (the mechanism that corrupted a live incident via a Reddit
merge). Guard: `test_summary_enrichment.py`.

Every auto-merge writes `training_signals` `action='auto_update'`,
`decided_by='agent'`; every operator undo writes `action='update_reverted'` — the
false-merge correction the learning loop reads. The merge math lives once in
`_compute_merge`, a pure mirror of `applyUpdate()` in
`apps/war-room/lib/utils.ts` (change one, change the other). Guards:
`test_auto_merge_eligibility.py`, `apps/war-room/lib/utils.updateMerge.test.ts`.

---

## 3. What you get alerted on, and what does not

Alerting nobody reads is worse than none, because the one message that mattered
gets filtered with the rest. Every alert is therefore deduped and throttled, and
**silence means healthy**.

Two mechanisms do the work, and they are not the same thing. The **window** is
per kind (`notify.DEFAULT_THROTTLE_MINUTES`, overridable per call); the **dedup
key** is what the window is measured against, and most callers put the calendar
date in it, which is what actually makes an alert once-a-day rather than
once-a-pass.

| Kind | Sent when | Window | Dedup key |
|---|---|---|---|
| `review_queue` | Cards below the threshold are waiting (req #4) | 180 min | `review_queue:<date>` — one alert a day at one pass a day |
| `anomaly` | Supervisor finds a *serious* fleet problem (req #9); integrity finds something needing a human (req #10); guardrail #4 flags political content | 60 min default — supervisor overrides to 1440 | `supervisor:<broken sources signature>` — sent only when that signature differs from the last one actually emailed (see below), `integrity:<date>`, `political:<url>` |
| `maintenance` | Something broke, with a plain-English suggested fix (req #11) | 1440 min | `maintenance:<date>` |
| `health` | A backend component is down, or the cost guard tripped (req #12) | 60 min | `health:<components down>` |
| `monthly_report` | 1st of the month (req #13) | Never throttled | one a month by construction |

A single flaky source is **logged, not alerted on**. "Serious" is defined in
`ops/supervisor.py` and is exactly four shapes, all of which mean the archive has
stopped updating and will not fix itself: ≥ 3 sources anomalous in one pass;
*every* registered source failing (only counted with ≥ 3 sources registered, or
"every" means nothing); one source anomalous 3 days running; an `agent_runs` row
stuck in `running` for 90 minutes. Everything else is a warning in the activity
log.

> **One of those four triggers was dead until 2026-07-30.** The supervisor's
> "source fetched 0 items N passes running" check read
> `scraper_health.consecutive_zeros`, and that column is only written by
> `scrapers.log_scraper_run`, which is only reached from `scrapers.scrape_all` —
> which nothing calls. The live pipeline is `run_ingestion_pass` over
> `ingestion/sources/`. So the check could not fire however broken a source got,
> which is precisely the failure it exists to catch. It now derives the streak
> from `pipeline_run_history` (`report.per_source[].fetched`), written by
> `state_store.record_run` at the end of every real pass. No new table, no new
> writes.
>
> **`scraper_health` has a live writer again** (`ingestion/health.py`, one row per
> fetched source per pass) — and the supervisor still does not alert off it. That
> is deliberate, not an oversight: an append-only table that stops being appended
> to is indistinguishable from a healthy quiet one, so the check that exists to
> catch silent death must not be able to go silent the same way. `scraper_health`
> is now a *reporting* surface — the War Room health views and
> `ops/maintenance.py`'s error digest, both of which carry real per-source rows
> again. Base new alerts on run history.
>
> **The zero-streak threshold is ONE number for every source, not one per tier
> (2026-08-29).** `report.per_source[].fetched` is POST-Yishun-keyword-filter for
> every source, primary tier included, not just discovery — every
> `scrapers.scrape_*` module calls `content_matches_keywords` (or
> `content_matches_lang` for the Malay/Tamil scrapers) before returning anything,
> and `ingestion/sources/legacy.py` says so outright. An earlier pass at this gave
> discovery-tier sources (`news_sitemap.py`/`wp_search.py`, ids suffixed
> `_sitemap`/`_search`) a 30-pass leash while leaving primary sources at 5 —
> fixing half the bug on the wrong premise (that primary `fetched` counted the
> raw listing page) and leaving primary sources firing as false anomalies on
> ordinary Yishun silence. `ops/supervisor.py` now imports
> `ZERO_STREAK_ANOMALY` straight from `ingestion/health.py`'s
> `ZERO_STREAK_WARNING` (30) — same quantity, same reasoning, one constant so the
> two can't drift apart again — and applies it to every source.

> **The email dedup key is now a SIGNATURE comparison, not a fixed key
> (2026-08-29).** It used to embed the sorted broken-source list, so any churn
> in *which* sources were anomalous (one crossing in or out) changed the key and
> the once-a-day throttle never engaged — the same standing fleet problem mailed
> twice in one day with "slightly different" source lists. `ops/supervisor.py`
> now computes an `_alert_signature()` (the anomalous sources, plus a
> pseudo-member each for `all_sources_failing`/`agent_stuck` since those carry
> no `source` of their own) and reads back the signature from the last
> `operator_notified` event it wrote (`_previous_alert_signature()`, via
> `agent_events`, 14-day lookback). Unchanged signature -> logged, not re-mailed,
> even though `is_serious()` still returns the same reasons. Changed signature
> (a source newly broken, or one recovering) -> mailed, keyed on the signature
> itself rather than the date, so a second, different problem breaking later the
> same day still gets its own alert instead of being blocked by the first one's
> date-scoped key.

Every attempted send is a row in `notifications` **before** the send, so a
provider outage loses the delivery, not the alert. With no `TELEGRAM_BOT_TOKEN`
configured, alerts are recorded with status `disabled` and remain visible in the
War Room — the pipeline never blocks on Telegram.

---

## 4. Is the model actually learning? (req #5)

`ops/learning_monitor.py` writes a `learning_snapshots` row daily and compares
each 30-day window against the one before it.

**Agreement rate is the score, not confidence.** Mean confidence is what the
model *claims*; agreement rate is what the operator *confirmed*. A model can
drift to high confidence while getting worse, and only the delta between claim
and confirmation catches it. `approve_with_edits` deliberately does **not** count
as agreement — an edit is a correction.

| Verdict | Meaning |
|---|---|
| `learning` | Operator agreement rose ≥ 2 points vs the previous window (`LEARNING_DELTA`) |
| `stagnant` | Movement between −5 and +2 points — past the noise floor in neither direction |
| `regressing` | Agreement fell ≥ 5 points (`REGRESSION_DELTA`), **or** confidence rose > 3 points while agreement fell — the over-confidence signature, checked first because a flat rate hides it entirely |
| `insufficient_data` | Fewer than 20 operator decisions (`LEARNING_MIN_SAMPLES`) in either window, or the unmarked-bulk case below |

`auto_publish_reverted` (auto-published incidents a human later unpublished) is
the sharpest calibration signal available: it is the operator saying "0.95 was
wrong". Rising ⇒ raise `AUTO_PUBLISH_CONFIDENCE`.

> **`stagnant` is not automatically a fault.** Once autonomy is on, the agent
> handles the easy cards itself and only hard ones reach the operator. A flat
> agreement rate against a harder review set is the loop working as designed.

### Why the metric refuses to answer sometimes (QA A11)

The very first real snapshot read `learning`, agreement `0.414 → 0.782` (**+36.8pp**) over
101 samples. It was wrong, and the way it was wrong is worth keeping in mind.

**A bulk approval is one click over N cards, not N verdicts.** `backfill-bulk` wrote a
`training_signals` row per card with no `operator_changes` — which the metric read as "the
operator agreed with the model, unchanged." 91 of 130 rows had exactly that shape, and
`dagger` scored **100% agreement over 53 samples**. So a window heavy in backfill always
outscores a window of genuine review, and the delta measured *workflow composition*, not
model quality. That is the self-flattering-metric failure mode this whole section exists
to avoid, and it appeared on the first reading.

Two changes close it:

1. **`backfill-bulk` now marks its rows** `operator_changes: {bulk: true}`, and
   `_window_metrics` excludes them from the agreement maths (counted in
   `per_category._meta.bulk_excluded`).
2. **Pre-marker rows cannot be fixed retroactively**, so the metric refuses to guess.
   When ≥75% of decisions are unchanged approvals **and** nothing in the window is
   identifiably bulk, the window is equally consistent with "the model is excellent" and
   "someone bulk-approved a backfill" — so the verdict returns `insufficient_data` with
   the reason, rather than reporting the flattering interpretation as fact.

The threshold is deliberately high (`UNMARKED_BULK_SUSPICION = 0.75`): a genuinely good
model *should* produce mostly clean approvals, so this must only fire when the reading is
ambiguous. It is self-clearing — once marked bulk rows appear, or the clean share returns
to normal, the verdict speaks again.

> Against the live archive today this correctly returns **`insufficient_data`**, not the
> +36.8% it would have claimed. `auto_publish_reverted` remains the signal to weight most
> heavily, because it cannot be gamed this way: it only moves when a human unpublishes
> something the agent chose.

### The loop was open until now

`ingestion/learning.py` has always *read* `source_reputation` and nudged
candidate confidence by ±0.10 from `trust_score`. **Nothing in the repository
ever wrote that table.** Every domain resolved to the 0.500 default, both
thresholds (0.700 / 0.300) were unreachable, and the nudge was a permanent
no-op — the loop drawn closed in `LEARNING_LOOP.md` §5 was open in code.

`learning_monitor.rebuild_source_reputation()` now supplies the write side,
recomputing per-domain trust from operator verdicts each day:

```
trust = (approvals + 1) / (approvals + rejections + 2)
```

Laplace-smoothed, so one rejection cannot send a new domain to zero. Smoothing
alone is **not** the safety here, though: it reads 0.750 after two clean
approvals, which already clears the 0.700 boost threshold on almost no evidence —
and a +0.10 nudge can push a 0.86 draft over a 0.95 autonomy gate. So a domain is
pinned to the neutral 0.500 default until it has ≥ 10 verdicts on record
(`REPUTATION_MIN_OBSERVATIONS`, default 10). Rejections are structurally scarcer
than approvals right now (a rejected draft has no `incident_id` to trace a domain
through), so early data skews positive by construction and neutral-until-proven
is the conservative direction.

Full recompute rather than incremental counters: idempotent, self-healing after a
missed run, and immune to the double-count-on-retry bug incremental counters
invite. Agent auto-approvals (`decided_by='agent'`) are excluded, or a domain
could bootstrap its own reputation and then use it to clear the bar. Note this
tally is *not* the agreement metric: for reputation, `approve_with_edits` counts
as an approval — the operator kept the source, they only changed the prose — and
an `operator_added_source` counts as an approval for the domain the human went
and found, which is that domain's strongest possible endorsement.

---

## 5. Exit conditions (req #8)

No scraping path can run unbounded. Every limit below is enforced in code:

| Limit | Value | Where |
|---|---|---|
| Whole-pass deadline | 1500 s (`INGESTION_MAX_SECONDS`, passed in by `ops/daily.py`; `run_ingestion_pass`'s own default is 1200) | `orchestrator.py` — checked **before each source's fetch**, and **before dedup** in the candidate loop |
| Circuit breaker | 5 consecutive same-class API failures (`circuit_breaker_n`) | `orchestrator.py` |
| Blocked source | **Zero** retries | `fallback.py` — never retry into a ban |
| Unavailable source | Exactly 1 retry, 30 s backoff, **skipped** when the deadline is near | `fallback.py` |
| Stage 1 RPM wait | 90 s cap per call (`MAX_WAIT_SECONDS`) | `stage1_quota.py` |
| Stage 1 daily budget | RPD ceiling (`STAGE1_RPD`, 1500); halts the pass | `budget.py`, `stage1_quota.py` |
| Article fetches per news sitemap | 15 (`MAX_ARTICLE_FETCHES`) | `sources/news_sitemap.py` |
| Auto-publish per run | 25 (`AUTO_PUBLISH_MAX_PER_RUN`) | `auto_publish.py` |

Three of these were fixed while wiring up autonomy, and each was a real hang or
overrun, not a hypothetical:

- **`Stage1RpmThrottle.wait_if_needed` could loop forever.** With
  `STAGE1_RPM=0`, `len(window) < 0` is never true, so it slept in 0.1 s steps
  indefinitely — a genuine infinite loop reachable from an env-var typo. RPM is
  now floored at 1 and the wait is capped.
- **The deadline could not stop a pass between sources.** It was checked only
  inside the candidate loop, *after* dedup — so a pass of all-duplicates never
  reached the check at all, and a pass out of budget kept starting new fetches.
- **Google News burned the entire pass budget.** `_resolve_redirect` (one HTTP
  round-trip each) ran on all ~650 feed entries before the recency filter
  discarded ~600 of them. Filtering on the RSS entry's own date *before*
  resolving cut that fetch from **909 s to 59 s**. Before that fix a daily pass
  hit its deadline inside Google News and `reddit` and `edmw` never ran at all.
  That source was **removed entirely on 2026-08-02** for an unrelated reason (its
  wrapper URLs were unresolvable and were being stored as citations — see
  `ingestion/sources/news_sitemap.py`), but the lesson outlived it and is now
  carried by its replacements: `news_sitemap` applies recency to a sitemap entry
  *before* fetching the article body, and `wp_search` needs no fetch at all.
  **Filter on the cheap field first; never spend a round-trip to learn something
  you already have.**

**The pass deadline is shared across the whole fleet, and the fleet got bigger on
2026-08-02.** `get_enabled_sources()` now returns **25** sources — 12 MSM
scrapers, 9 news-sitemap adapters, 2 WordPress-search adapters and 2 signal
sources — where it returned 15 before. The 1500 s budget did not change, so each
source's share of it did: roughly 60 s each rather than 100 s. That matters
because the deadline check `break`s out of the source loop, and **`get_enabled_sources()`
order decides who is starved** — the two signal sources are last in the list, so
they are the first to be skipped. A starved source is not lost (its watermark is
left untouched and it is retried in full next pass, and nothing writes a
`scraper_health` row for a source that was never fetched, so it cannot walk toward
a false zero-streak) but it does not contribute that day. If `pass_deadline`
anomalies start appearing, raise `INGESTION_MAX_SECONDS` — the Cloud Run request
timeout is 3600 s, so there is real headroom — before assuming a source is broken.

**Every block is logged**, never silent: an `agent_events` row (`source_blocked`
/ `source_unavailable`), plus `pipeline_state.last_reason` and
`consecutive_failures`. That is what the supervisor and maintenance agents read.

**Plus one `scraper_health` row per fetched source, per pass**
(`ingestion/health.py`, called from the orchestrator's per-source loop) —
items found, items past Stage 1, duration, zero-streak, status. Both War Room
health views and `ops/maintenance.py`'s digest read it. The supervisor's
zero-streak **alert** does not — that derives from `pipeline_run_history`; see
the note under §3's alert table.

> **The failure this replaced is the one to watch for.** The table's writer used
> to be `scrapers.log_scraper_run`, reachable only through `scrapers.scrape_all`
> — which lost its last caller when ingestion moved to the `ingestion/sources/`
> adapters. Nothing errored. The table simply stopped growing while the
> supervisor kept grading it, so a source dead for months still reported a green
> dot with full confidence. An observability table with no live writer is worse
> than none: it answers, and the answer is a fossil.
>
> Two guards now. Rows are written from the path that actually runs — and the
> supervisor no longer depends on that being true, because its zero-streak alert
> derives from `pipeline_run_history` instead. Replacing a missing writer does not
> remove the coupling that caused the outage; removing the coupling does.
>
> If you ever move the writer again, keep the key `source.name` — the stable id,
> the one `pipeline_state` uses. The old writer used display names (`Stomp`, `The
> Straits Times`) while the supervisor cross-references the two tables by this
> key, so two spellings of one source count it **twice** toward the "≥ 3 sources
> anomalous" alert threshold: one broken source could alert as if it were three.

**`ZERO_STREAK_WARNING` in `ingestion/health.py` was raised 3 → 30 on
2026-08-02.** `items_found` counts candidates that survived the Yishun keyword
filter, not articles the source served, so zero is the *normal* reading — one
outlet publishing nothing about one town for three days is unremarkable, and
Tamil Murasu or Berita Harian can go a month. At 3 nearly the whole fleet sat at
`warning` permanently (9 of 15 sources on 2026-08-02, every one of them reading
"0 items for 3 consecutive runs"), and a dashboard whose warning state is its
resting state was read — reasonably — as a mass scraper failure when nothing had
failed. This is a **display** threshold only: real failures surface as
`status='error'`, and outage alerting comes from `pipeline_run_history` as above.

### 5b. Exit conditions that open, not just close: oversized merges

Every limit above is a ceiling that stays put. This one is a **hold the agent can
lift by being right**, and it is the template for future ones.

A cluster larger than `CLUSTER_MAX_SIZE` (6) used to be *shredded* — written as
one queue row per member. That made sense when grouping was pairwise judgements
fused by union-find: a group of 8 could be a transitive blob no single decision
ever saw whole (A~B and B~C merged A, B **and** C with nothing comparing A to C).
Batched grouping removed union-find, so a group of 8 is now one call that saw all
8 at once — and the shred had become a net negative, burning N Stage 2 drafts to
produce either one single-source row or several near-duplicates. The live archive
holds 7-, 9-, 10- and 12-source incidents; a cap of 6 shredded every one.

Now: the cluster is written **intact, as one row**, flagged
`raw_content._oversized_cluster = N`. `auto_publish.check_eligibility` holds a
flagged row (`oversized_cluster_unproven`) — but only until the grouper has
earned merges that size:

```
trust = (approvals + 1) / (approvals + rejections + 2)     # over flagged rows
trusted = (approvals + rejections) >= OVERSIZED_MERGE_MIN_SAMPLES   # default 5
          and trust >= OVERSIZED_MERGE_TRUST                        # default 0.80
```

| Record | Trust | Auto-publishes? |
|---|---|---|
| 0-0 (cold start) | 0.50 | no — below the sample floor |
| 2-0 | 0.75 | no — clears the rate, not the floor |
| 5-0 | 0.86 | **yes — earned, gate lifts itself** |
| 5-1 | 0.75 | no — one rejection **re-arms** it |
| 14-1 | 0.88 | yes — a long record outweighs one bad call |

Same formula as `learning_monitor.rebuild_source_reputation`, deliberately
stricter settings: smoothing alone reads 0.75 after **two** approvals, and two
data points is not a track record for a decision that can conflate several real
events into one public record. The sample floor is the load-bearing half.

Nothing is flipped by hand in either direction, and no other gate is weakened —
a trusted oversized row still has to clear confidence, both source guardrails and
the date check. An unreadable history returns trust 0.0, which holds: *absence of
evidence is not evidence of good judgement.*

### 5c. Model output caps, and what to do when one is hit

Every model call in the pipeline asks for JSON. A reply that stops at
`max_tokens` is cut off mid-object, so `_parse_json` fails with *"No JSON object
in model response"* — **the identical message a model returning prose produces.**
`stop_reason` was read nowhere in the repo, so the two were indistinguishable,
and the trivially fixable fault looked like the hard one.

`filters/model_call.py` closes that. Every JSON-parsing call now goes through
`create_with_headroom`, which raises a named `TruncatedResponse` rather than
returning half an object.

**Measured headroom** on the largest real inputs in the archive — nothing is
close to its cap today; the guard is for when the inputs grow:

| Call | Cap | Observed | Spare | Env var |
|---|---:|---:|---:|---|
| `stage2._write_draft` | 2048 | 763 | 63% | `STAGE2_WRITE_MAX_TOKENS` |
| `stage2._classify` | 512 | 167 | 67% | `STAGE2_CLASSIFY_MAX_TOKENS` |
| `consolidation._judge_batch` | 1024 | 128 | 88% | `CONSOLIDATION_BATCH_MAX_TOKENS` |
| `clustering._make_grouper` | 1024 | 132 | 87% | `CLUSTER_GROUPER_MAX_TOKENS` |
| `consolidation._judge_pair` | 400 | — | — | `CONSOLIDATION_PAIR_MAX_TOKENS` |

`_judge_pair` is no longer on the ingestion path — consolidation batches (§6) —
but it is not dead: `ops/integrity.py` still calls it one pair at a time for the
duplicate re-scan, so its cap and its guard both still matter.

**Recovery, in order:**

1. **Automatic.** One retry at double the cap. A truncation means exactly one
   thing — the reply did not fit — and a second call costs far less than dropping
   a candidate the scrapers and Stage 1 already paid for. Exactly one retry: if
   double also truncates, the cap is not the problem and looping would just burn
   tokens.
2. **Recorded.** A recovered `_write_draft` sets `raw_content._write_truncation_retry`.
   It does **not** block publishing (the retry produced a complete draft) — it is
   there so a *recurring* retry is visible before it becomes a failure.
3. **Loud.** A second truncation raises `TruncatedResponse` naming the call, the
   cap and the env var. The orchestrator's existing circuit breaker sees it like
   any other write error, so it lands in `agent_events` and the pass notes.
4. **Operator lever.** Raise that call's env var on Cloud Run and re-run. No
   redeploy — which is the point of the env vars existing at all.

The grouper deserves special attention: a truncated partition is not an *exact*
partition, so `group_candidates` correctly falls back to all-singletons. That is
safe, but it means **a pass would silently stop merging entirely** while looking
healthy. The guard converts that into a visible retry.

### 5d. Lifecycle auto-conclude is wired but OFF (`LIFECYCLE_AUTO_CONCLUDE`)

Step 9 is the only agent in the chain that **edits an already-published
incident** with no human in the loop. On a Monday it finds developing stories
with no `source_timeline` activity for 180 days and sets `is_developing=false`,
`latest_source_role='timeout'`, `conclusion_type='timeout'`, then queues a
sentinel row so the operator can confirm or reopen.

That is a defensible default — a "developing" story nobody has reported on since
January is not developing — but it is an **editorial** judgement about live
content, and it is the operator's to make, not this document's. So the step is
wired into the chain and gated off:

```bash
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars LIFECYCLE_AUTO_CONCLUDE=true
```

Off (the default) the step is skipped with that reason recorded in
`agent_runs.stats.cadence`. Nothing else about the chain changes, and no other
step depends on it having run.

Two things worth knowing before flipping it:

- **The public change is small but real.** `is_developing` no longer drives a
  DEVELOPING badge (removed in the June-2026 feed pass) — it drives the
  report-count line. The stronger effect is archival: `concluded_at` and
  `conclusion_type` become part of the record.
- **It is reversible per incident, not in bulk.** The sentinel row's REOPEN
  action restores one story. There is no undo for a batch, so the first run
  after enabling is the one to watch — `POST /lifecycle/run` by hand first and
  read the result before letting the Monday step do it unattended.

`POST /lifecycle/run` deliberately ignores this flag: calling it by hand *is*
the operator decision the flag exists to require.

---

## 6. Cost (req #12)

**The scheduling model is the cost control.** Cloud Run scales to zero. Any
in-process scheduler would need `min-instances=1` **and** CPU-always-allocated
to fire reliably — roughly **$15–25/month** to execute ~15 minutes of daily work.
One Cloud Scheduler ping (3 jobs are free) keeps the service at zero instances
for the other 23 h 45 m.

There is therefore **no in-process scheduler** — the optional single-job
APScheduler behind `ENABLE_INPROCESS_SCHEDULER` was removed 2026-08-29; the daily
chain fires only from Cloud Scheduler's `POST /orchestrator/daily`. The nine
per-source "health check" jobs that used to re-scrape each site just to log a
count are gone too — they duplicated the ingestion pass and each one needed that
scheduler running.

Expected steady state: Cloud Run a few cents/month, Cloud Scheduler free, Gemini
Stage 1 on the free tier and therefore priced at **$0** in the estimate
(`STAGE1_USD_PER_CALL`, overridable if a billing key is ever attached) while
still being the thing that caps throughput at `STAGE1_RPD`=1500 — Anthropic is
the only real cost.

**Every figure `ops/backend_health.py` produces is an estimate, not billing
data.** It prices a queued draft as one classify call plus one write call
(`STAGE2_USD_PER_DRAFT`, ~$0.031 at the list prices in `_USD_PER_MTOK`) against
deliberately generous assumed token shapes — a cost guard that under-estimates is
a cost guard that never fires. In the same spirit the write call is still priced
at **Sonnet** rates while `STAGE2_WRITE_MODEL` defaults to Haiku, so the estimate
runs high on purpose. Dry runs are counted, because `dry_run` suppresses database
writes, not model calls: a dry run costs exactly as much as a real one.

**The 2026-08-02 fleet expansion (15 → 25 sources) does not move spend the way it
looks like it should.** Neither term in the estimate is per-source: Stage 1 calls
come from each pass's `novel` count (candidates that survived the keyword filter,
recency and dedup) and Stage 2 drafts from `total_queued`. Ten more sources over
the same town on the same day mostly produce *more copies of the same stories*,
which recency and dedup drop before Stage 1 ever sees them. What does grow is
per-pass wall-clock — see the deadline note in §5 — and the discovery adapters'
own HTTP cost, which is free. Watch the `passes`/`stage2_drafts` numbers in
`agent_runs.stats` after a fleet change rather than assuming a multiplier.

### Consolidation was the cost driver — now one call per candidate

The largest single line item was **not** Stage 2 writing but consolidation
dedup. For each candidate, `consolidation/check.py` ran one Haiku judgement per
existing record sharing ≥1 keyword — and with `MIN_KEYWORD_OVERLAP=1` a single
common 4-letter word ("road", "fire", "block") qualifies. Against a 50-published
+ 50-queued pool (`CANDIDATE_FETCH_LIMIT` + `QUEUE_FETCH_LIMIT`) that fanned out
to as many as ~100 calls per candidate, and it grew with the archive: one live
pass spent ~87 Haiku calls in 3 minutes, more than Stage 1 and Stage 2 combined.

It is now **one batched judgement** (`_judge_batch`): every eligible record goes
into a single call, and the model returns a `match_index` into that list. Cost
is `O(candidates)` — flat in archive size, one Haiku call per candidate that
reaches consolidation, regardless of whether the pool holds 5 records or 55.

Two consequences worth holding onto:

- **The keyword ranking survives, but it no longer gates anything.** Records are
  still sorted by overlap so the likeliest match sits first in the prompt; the
  long tail that the old per-candidate cap discarded is now judged too rather
  than silently dropped.
- **`MAX_JUDGEMENTS_PER_CANDIDATE` and `EARLY_EXIT_CONFIDENCE` are dead.**
  `check.py` deliberately no longer imports them — both existed only to bound a
  call count that no longer exists. They stay *defined* in
  `consolidation/rules.py` so an existing env override does not become an error
  mid-rollback. Do not reintroduce them as if they were live knobs.

The failure mode changed shape with the cost: a failed batch loses the whole
comparison for that candidate where a failed pair used to lose one. It fails in
the same direction — treated as `new`, so the worst case is a duplicate row an
operator can merge, never a silently dropped story — and `ops/integrity.py`
re-scans for duplicates every pass as the backstop.

`ops/backend_health.py` estimates each day's spend from actual usage and alerts
above `COST_ALERT_USD_PER_DAY` (default $2.00). It also flags two structural
risks, which are `degraded` rather than `down` because they predict a bill rather
than being one: **more than 2 passes in 24 h** (`RUNAWAY_PASSES`), which is how a
runaway scheduler would announce itself, and **`min-instances` > 0**, read from
`CLOUD_RUN_MIN_INSTANCES` because Cloud Run does not expose its own setting to
the container — so the deploy has to mirror it there, and an unset value
under-reports rather than inventing a risk.

### The recency watermark was the other recurring cost

Making consolidation flat in archive size does nothing if the *same candidates*
come back every day, and they did. `pipeline_state.watermark` only advanced for a
candidate that got WRITTEN, so a Stage 1 rejection or a consolidation
duplicate-skip — neither of which writes a row, and neither of which
`dedup.is_duplicate` can see, because it reads only
`war_room_queue.source_url` and `incidents.source_urls` — left the watermark
where it was. Those articles were re-fetched, re-Stage-1'd and re-drafted on
every pass until an unrelated candidate dragged the watermark forward.

The watermark now advances on **decisions**, not writes. `ingestion/watermark.py`
holds the rule and `PIPELINE_CHANGES_2026-07-30.md` §9 the reasoning; the two
things not to undo are the **retry floor** (only decided dates strictly below the
earliest unresolved date advance, so a transient error is never deleted from the
future by its successful siblings) and the **same-day grace** (never advance onto
the pass's own date — the source is still publishing, and `RecencyFilter`'s `<=`
would drop the rest of the day unseen). Each pass now reports how many candidates
consolidation dropped as duplicates of rows already awaiting review; under the
bug that number was recurring spend.

> **Known gap — consolidation calls are not in the cost estimate (A12).**
> `estimate_daily_cost` derives Stage 1 calls from each pass's per-source `novel`
> count and Stage 2 drafts from `total_queued`; consolidation judgements are
> counted nowhere. The estimate therefore under-counts by one Haiku call per
> candidate that reached consolidation — bounded and predictable now that the
> fan-out is gone, but real, and it is the one direction a cost guard should not
> be wrong in. Fixing it properly means threading a judgement counter through
> `IngestionReport`; tracked, not yet done.

> **Known gap — ephemeral filesystem.** `ingestion/stage1_daily_usage.json` and
> `classifiers/calibration_log.json` live on Cloud Run's ephemeral disk and reset
> on every container replacement. For the budget file this is close to harmless
> (the container is replaced between passes anyway, and the RPD ceiling is far
> above one pass's usage), though it means no *cross-pass* ceiling can be
> enforced. For `calibration_log.json` it is now load-bearing: it is why
> recalibration is step 1 rather than sitting with the other monitors (§1).
> Moving both to Supabase is tracked as follow-up, and would let the calibration
> step move anywhere in the chain.

### Pattern detection is now a standing daily cost (and a coverage limit)

Step 8 extracts named entities with one Haiku call per incident over a 365-day
pool, capped at `PATTERN_MAX_EXTRACTIONS` (100). Its `_entity_cache` is a
module-level dict, so on paper each incident is extracted once — but **with
min-instances=0 the process lifetime is one pass**, so the cache is cold every
day and the same incidents are re-extracted every day. Budget it as a standing
~100 Haiku calls/day, not as a one-off that amortises away. Like consolidation
above, these calls are **not** in `backend_health`'s estimate.

The sharper consequence is coverage, not cost. The incident pool is ordered
newest-first, so once it exceeds the cap the *same* newest 100 are examined
daily and everything older is **never** examined — entity patterns involving the
tail cannot be found, and "0 entity patterns" would read identically to a clean
sweep. `run()` therefore returns `entities_uncovered` and logs an explicit
INCOMPLETE warning naming the numbers. If that count is non-zero and entity
detection matters, raise `PATTERN_MAX_EXTRACTIONS` or give extraction a
persistent store; the deterministic crime-type and location checks are unaffected
either way, as they use no model at all.

---

## 7. Runbook

### Deploy

```bash
gcloud run deploy yishun-agents \
  --source packages/agents --region asia-southeast1 --platform managed \
  --allow-unauthenticated --timeout=3600 --memory=1Gi --cpu=1 \
  --min-instances=0 --max-instances=2
```

`--timeout=3600` matters: the ingestion step **alone** is bounded at
`INGESTION_MAX_SECONDS` = 1500 s, with eleven more steps queued behind it, so the
300 s default cannot hold a pass. `--min-instances=0` is the cost control.
**`--allow-unauthenticated` is deliberate and must stay.** The ops endpoints are
protected by `OPS_TOKEN` (`X-Ops-Token`, `hmac.compare_digest`, on every route
except `/health`), not by Cloud Run IAM. This said `--no-allow-unauthenticated`
until 2026-08-04, and that is the single reason the art pipeline never produced
an image: the flag REWRITES the service IAM policy and drops `allUsers`, and the
War Room runs on **Vercel**, has no GCP identity, and sends only `X-Ops-Token` —
so every `/art/generate` call died at the edge with `403 … Empty Authorization
header value` and never reached FastAPI. The scheduler was unaffected (it
authenticates with OIDC as `yishun-scheduler@…`, still bound as an invoker),
which is exactly why the daily chain kept working and hid the failure for weeks.

Cloud Scheduler stops waiting at its 1800 s attempt deadline while the Cloud Run
request runs on to 3600 s, so a retry — or an impatient manual trigger — would
start a *second* pass over the same queue rows: double the model spend, and two
workers racing to publish the same draft. `daily._already_running` is what makes
overlap impossible in code, and it covers the manual trigger too, which no
scheduler setting can. It queries `agent_runs` for a `daily_orchestrator` row
still `running`, bounded to a 60-minute look-back so an orphaned row cannot wedge
the pass permanently, and fails **open** so an unreadable `agent_runs` table
cannot turn a logging outage into an ingestion outage.

### Turn Telegram alerts on

The pipeline runs without it — alerts are recorded and visible in War Room. Both
`TELEGRAM_BOT_TOKEN` *and* `TELEGRAM_CHAT_ID` are required for a real send; with
either missing, `notify()` records the alert with status `disabled` and returns.

**One-time setup (operator, not Cloud Run):**
1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts. It
   returns a bot token (`123456789:AAF...`).
2. Start a chat with your new bot — send it any message. A bot cannot message a
   user who has never messaged it first; this is the one-time handshake.
3. Get your chat id: `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and
   read `result[0].message.chat.id` from the response.

**Wire it into Cloud Run:**
```bash
printf '%s' 'YOUR_BOT_TOKEN' | gcloud secrets create telegram-bot-token \
  --data-file=- --replication-policy=automatic --project=yishun-again
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-secrets TELEGRAM_BOT_TOKEN=telegram-bot-token:latest \
  --update-env-vars TELEGRAM_CHAT_ID=<your chat id>
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" \
  https://<service-url>/notify/test          # prove delivery works
```
`TELEGRAM_CHAT_ID` is a plain numeric id (no `@`, no delimiter collision), so it
can go straight in `--update-env-vars` — unlike the old `OPERATOR_EMAIL` this
replaced, which needed a YAML file to dodge gcloud's `@`-as-delimiter parsing.

`NOTIFY_ENABLED=false` mutes sending without removing the token — alerts are
still recorded.

### Operate

```bash
# Fleet status — recent runs, errors, anomalies, stuck runs
curl -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/agents/status

# Full chain, writing nothing
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" "https://<service-url>/orchestrator/daily?dry_run=true"

# Turn one agent off without a redeploy. Works for every step in the §1 table,
# including the cadence-gated ones: AGENT_DISABLED=lifecycle,pattern_detection
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars AGENT_DISABLED=auto_publish

# Stop all autonomous publishing immediately (drafts still queue for review)
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars AUTO_PUBLISH_CONFIDENCE=2.0

# Run one cadence-gated agent by hand, off its schedule
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/pattern/run
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/recalibration/run
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/lifecycle/run
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/discovery/run
```

`AUTO_PUBLISH_CONFIDENCE=2.0` is the panic switch: unreachable, so everything
routes to the War Room, and nothing else about the pass changes.

### Enable / roll back auto-merge (§2b)

Auto-merge (auto-applying an `update` row into a live incident) is **off by
default** and is the only autonomous path that mutates an already-published
incident, so enabling it is a deliberate operator action with a clean rollback.

**Preconditions — both must be true, or enabling does nothing useful:**
- **Migration 018 applied.** Without it the `_undo_snapshot` write path is fine
  but the training-signal insert (`action='auto_update'`) is rejected, and a
  later undo (`status='update_reverted'`) is rejected too — you would have merges
  you cannot cleanly undo. Verify: `war_room_queue_status_check` lists
  `update_reverted`.
- **The agents backend is deployed with the auto-merge code** (`_compute_merge`,
  `check_update_eligibility`, and `_match_confidence` persistence in
  `consolidation/queue_row.py`). Until that deploy lands, `_match_confidence` is
  never written, so every candidate is held as `no_match_confidence` and nothing
  merges regardless of the flag.

**Dry-run first — see what WOULD merge, changing nothing:**
```bash
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" "https://<service-url>/orchestrator/daily?dry_run=true"
# then read the auto_publish run: agent_runs.stats.merged + stats.reasons,
# and agent_events level=info action=would_merge for the specific rows.
```
`dry_run` still evaluates the gate but never writes — a would-merge that surprises
you is caught here, not on the live incident.

**Enable:**
```bash
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars AUTO_MERGE_ENABLED=true
# Optional, all have safe defaults:
#   AUTO_MERGE_CONFIDENCE=0.95        draft-quality bar
#   AUTO_MERGE_MATCH_CONFIDENCE=0.95  same-event bar (the strict, wrong-merge axis)
#   AUTO_MERGE_MAX_PER_RUN=25         blast-radius cap per pass
```
Takes effect on the next scheduled pass (02:58 / 14:58 SGT) — no redeploy.

**Watch the first few passes:**
| Signal | Where |
|---|---|
| How many merged, and why others held | `agent_runs.stats.merged`, `stats.reasons` for `auto_publish` |
| Each applied merge | `agent_events` level=success action=`auto_merged` (incident id + match conf) |
| The reversible record | War Room → QUEUE → "Recently merged updates" panel |
| The learning signal | `training_signals` where `action='auto_update'`, `decided_by='agent'` |

**Roll back — three levers, coarsest last:**
1. **Undo one bad merge** (the common case) — War Room QUEUE → "Recently merged
   updates" → **Undo**, or `POST /api/queue/{id}/revert-update`. Restores the
   pre-merge snapshot exactly (`source_urls`, timeline, dates, `update_count`,
   summary) and records `action='update_reverted'`. The incident stays published;
   only the wrongly-attached source is removed.
2. **Throttle without a flag flip** — raise the bar so fewer (or no) merges
   auto-apply while the rest route to review:
   ```bash
   gcloud run services update yishun-agents --region asia-southeast1 \
     --update-env-vars AUTO_MERGE_MATCH_CONFIDENCE=2.0   # unreachable = all held
   ```
   This is the merge analog of the `AUTO_PUBLISH_CONFIDENCE=2.0` panic switch.
3. **Stop auto-merge entirely** — `AUTO_MERGE_ENABLED=false` (or unset). Every
   `update` row goes back to the operator, exactly as before the feature existed.
   ```bash
   gcloud run services update yishun-agents --region asia-southeast1 \
     --update-env-vars AUTO_MERGE_ENABLED=false
   ```
   `AGENT_DISABLED=auto_publish` also stops it, but that halts new-incident
   auto-publish too — prefer the flag when you only want to stop merges.

Merges already applied before a rollback are **not** reverted by any of the three
— use lever 1 on each. They are safe to leave: a merge is an added source on a
real incident, not a new publish.

### Where to look when something is wrong

| Question | Where |
|---|---|
| What did the fleet do last night? | `GET /agents/status`, or `agent_runs` |
| Why did nothing publish? | `agent_runs.stats.skip_reasons` for `auto_publish` |
| …and `skip_reasons` is empty? | `stats.skipped_all` — auto-publish refuses to run at all if `training_signals.decided_by` is missing (migration 011). It will not take an action it cannot log, so it publishes nothing and mails `maintenance` instead |
| Which source is broken? | `pipeline_state.last_reason`, War Room → HEALTH |
| Why didn't lifecycle / discovery run? | `agent_runs.stats.cadence` on the newest `daily_orchestrator` row |
| Is the model improving? | `learning_snapshots`, newest row |
| Did an alert actually send? | `notifications.status` |
| What happened last month? | War Room → REPORTS |

---

## 8. What is still human-only

Auto-publish handles new incidents above the confidence bar. Everything below
remains a person's job, by design:

- **`update` rows** — merging new reporting into a published story. Human-only
  by default, but this is the one item on this list with an opt-in autonomous
  path: `AUTO_MERGE_ENABLED` (§2b, §7 runbook) auto-applies a high-confidence
  merge. Off unless deliberately enabled; every applied merge stays reversible
- **Acting on pattern alerts and lifecycle conclusions** — since 2026-07-30 the
  agents that *raise* these actually run (§1), but what they produce is a
  sentinel row asking a question. `check_eligibility` skips them
  (`notification_row`), so no alert can ever reach the public site — the
  editorial call on every one is the operator's
- **Approving a discovered source** — discovery files candidates
  `approved_by_operator=false, is_active=false`; until a human flips those, the
  domain is neither scraped nor citable
- **Corrections to published incidents** — the integrity agent reports, and only
  auto-fixes `corroboration_count` drift and unprocessed queue duplicates. It
  never rewrites a published incident's text, dates, or sources
- **Rejections** — nothing is auto-rejected. A draft that fails a gate waits
- **Anything the operator unpublishes** — which is the correction signal the
  whole learning loop is built to consume
