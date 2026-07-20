# Autonomy — the daily unattended pass

**Status:** Live · **Companion to:** `LEARNING_LOOP.md`, `INGESTION_DESIGN.md` · **Runbook:** §7 below

How Yishun Again went from "operator runs the pipeline by hand and approves every
card" to "a scheduled fleet runs itself once a day and only interrupts the
operator when something actually needs a person."

---

## 1. What runs, when

One Cloud Scheduler job at **14:58 SGT daily** POSTs to `/orchestrator/daily` on
the `yishun-agents` Cloud Run service. That single endpoint runs eight steps in
a fixed order (`packages/agents/ops/daily.py`):

| # | Agent | Module | Requirement |
|---|---|---|---|
| 1 | Ingestion pass | `ingestion/orchestrator.py` | — |
| 2 | Auto-publish + review email | `ops/auto_publish.py` | #3, #4 |
| 3 | Integrity (dupes, hallucinations) | `ops/integrity.py` | #10 |
| 4 | Supervisor (scraper fleet) | `ops/supervisor.py` | #9 |
| 5 | Learning monitor (deltas) | `ops/learning_monitor.py` | #5 |
| 6 | Backend health + cost guard | `ops/backend_health.py` | #12 |
| 7 | Maintenance digest | `ops/maintenance.py` | #11 |
| 8 | Monthly report (1st only) | `ops/monthly_report.py` | #13 |

**The order is load-bearing.** Integrity runs *after* publish so it audits what
actually went live. Supervisor runs *after* ingestion so it grades this pass.
Maintenance runs *last* because it reads what every other step logged — run it
earlier and it reports yesterday's news.

**Failure is isolated per step.** A crash in step 3 does not cost you steps 4–8;
the monitoring agents matter most precisely when something has broken. Failed
steps are recorded in the run report with a truncated traceback.

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

What the threshold does **not** override — the four hardcoded legal guardrails
(`CLAUDE.md`, "Never Remove") and the preconditions the human approve route
already enforces with a 422. A row failing any of these is **not rejected**; it
stays `pending` for the operator. The failure mode is always "a human looks at
it", never "it disappears".

| Gate | Skip reason logged | Why |
|---|---|---|
| ≥ 1 source URL | `no_source_url` | Guardrail #1, also a DB CHECK |
| No `type='signal'` URL | `no_approved_source_after_filter` | Guardrail #2 — EDMW is never a quoted source |
| Not political | `political_marker` | Guardrail #4. Stage 2 forces confidence 0, so this is unreachable; asserted as defence in depth |
| Real `incident_date` | `no_real_date`, `date_fallback` | QA H3 — never stamp "today" |
| Operator-approved domain | `unapproved_source_domain` | A URL from an unknown domain is not a *verifiable* source, which is guardrail #1's actual point |
| Not a sentinel row | `notification_row` | Pattern alerts and lifecycle notices are operator prompts, not incidents |
| `status = 'pending'` | `not_pending` | `update` rows merge into a live incident — a different write path whose failure mode is corrupting an existing story. Auto-merge is a separate decision, not yet taken |

Two more safety properties:

- **Blast radius cap.** `AUTO_PUBLISH_MAX_PER_RUN` (25) bounds one pass. Excess
  stays pending and the cap is logged as an anomaly.
- **Rollback on a half-write.** If the incident inserts but the queue row fails
  to close, the incident is immediately unpublished. Otherwise the next pass
  would see a still-`pending` row and publish a second copy (QA H2).

Every auto-publish writes a `training_signals` row with `action='auto_approve'`,
`decided_by='agent'`. That flag is what keeps the agent from grading its own
homework — see §4.

---

## 3. What lands in your inbox, and what does not

Alerting nobody reads is worse than none, because the one message that mattered
gets filtered with the rest. Every alert is therefore deduped and throttled, and
**silence means healthy**.

| Kind | Sent when | Throttle |
|---|---|---|
| `review_queue` | Cards below the threshold are waiting (req #4) | Once per day |
| `anomaly` | Supervisor finds a *serious* fleet problem (req #9); integrity finds something needing a human (req #10) | 60 min |
| `maintenance` | Something broke, with a plain-English suggested fix (req #11) | Once per day |
| `health` | A backend component is down, or the cost guard tripped (req #12) | 60 min |
| `monthly_report` | 1st of the month (req #13) | Never throttled |

A single flaky source is **logged, not emailed**. "Serious" is defined in
`ops/supervisor.py` and requires breadth or persistence, not a one-off.

Every attempted send is a row in `notifications` **before** the send, so a
provider outage loses the delivery, not the alert. With no `RESEND_API_KEY`
configured, alerts are recorded with status `disabled` and remain visible in the
War Room — the pipeline never blocks on email.

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
| `learning` | Operator agreement rose ≥ 2 points vs the previous window |
| `stagnant` | Movement inside the ±2-point noise floor |
| `regressing` | Agreement fell ≥ 5 points, **or** confidence rose > 3 points while agreement fell — the over-confidence signature |
| `insufficient_data` | Fewer than 20 operator decisions in either window |

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

Laplace-smoothed, so one rejection cannot send a new domain to zero and the
thresholds have to be *earned* — roughly 5 clean approvals to clear 0.700. Full
recompute rather than incremental counters: idempotent, self-healing after a
missed run, and immune to the double-count-on-retry bug incremental counters
invite. Agent auto-approvals are excluded, or a domain could bootstrap its own
reputation and then use it to clear the bar.

---

## 5. Exit conditions (req #8)

No scraping path can run unbounded. Every limit below is enforced in code:

| Limit | Value | Where |
|---|---|---|
| Whole-pass deadline | 1500 s | `orchestrator.py` — checked **before each source's fetch**, and **before dedup** in the candidate loop |
| Circuit breaker | 5 consecutive same-class API failures | `orchestrator.py` |
| Blocked source | **Zero** retries | `fallback.py` — never retry into a ban |
| Unavailable source | Exactly 1 retry, 30 s backoff, **skipped** when the deadline is near | `fallback.py` |
| Stage 1 RPM wait | 90 s cap per call | `stage1_quota.py` |
| Stage 1 daily budget | RPD ceiling; halts the pass | `budget.py` |
| Google News resolutions | 120 per fetch | `google_news_rss.py` |
| Auto-publish per run | 25 | `auto_publish.py` |

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
  resolving cut that fetch from **909 s to 59 s**; the remainder is the mandatory
  politeness delay between keyword queries. Before this fix a daily pass hit its
  deadline inside Google News and `reddit` and `edmw` never ran at all.

**Every block is logged**, never silent: an `agent_events` row (`source_blocked`
/ `source_unavailable`), plus `pipeline_state.last_reason` and
`consecutive_failures`. That is what the supervisor and maintenance agents read.

---

## 6. Cost (req #12)

**The scheduling model is the cost control.** Cloud Run scales to zero. An
in-process APScheduler would need `min-instances=1` **and** CPU-always-allocated
to fire reliably — roughly **$15–25/month** to execute ~15 minutes of daily work.
One Cloud Scheduler ping (3 jobs are free) keeps the service at zero instances
for the other 23 h 45 m.

`ENABLE_INPROCESS_SCHEDULER` is therefore **false** in production. The nine
per-source "health check" jobs that used to re-scrape each site just to log a
count are gone — they duplicated the ingestion pass and each one needed that
scheduler running.

Expected steady state: Cloud Run a few cents/month, Cloud Scheduler free,
Gemini Stage 1 within the free tier (~50–100 calls/day against 1,500 RPD),
Anthropic Stage 2 the only real cost and proportional to genuinely novel Yishun
stories — typically single digits per day.

`ops/backend_health.py` estimates each day's spend from actual usage and alerts
above `COST_ALERT_USD_PER_DAY` (default $2.00). It also flags the structural
risk: **more than ~2 passes in 24 h**, which is how a runaway scheduler would
announce itself.

> **Known gap — ephemeral filesystem.** `ingestion/stage1_daily_usage.json` and
> `classifiers/calibration_log.json` live on Cloud Run's ephemeral disk and reset
> on every container replacement. With one pass a day this is close to harmless
> (the container is replaced between passes anyway, and the RPD ceiling is far
> above one pass's usage), but the budget file cannot enforce a *cross-pass*
> ceiling in production. Moving it to Supabase is tracked as follow-up.

---

## 7. Runbook

### Deploy

```bash
gcloud run deploy yishun-agents \
  --source packages/agents --region asia-southeast1 --platform managed \
  --no-allow-unauthenticated --timeout=3600 --memory=1Gi --cpu=1 \
  --min-instances=0 --max-instances=2
```

`--timeout=3600` matters: a pass runs 5–20 minutes, far past the 300 s default.
`--min-instances=0` is the cost control. `--no-allow-unauthenticated` keeps the
ops endpoints off the public internet; the scheduler authenticates with OIDC.

### Turn email on

The pipeline runs without it — alerts are recorded and visible in War Room. To
start sending:

```bash
printf '%s' 'YOUR_RESEND_KEY' | gcloud secrets create resend-api-key \
  --data-file=- --replication-policy=automatic --project=yishun-again
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-secrets RESEND_API_KEY=resend-api-key:latest
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" \
  https://<service-url>/notify/test          # prove delivery works
```

### Operate

```bash
# Fleet status — recent runs, errors, anomalies, stuck runs
curl -H "X-Ops-Token: $OPS_TOKEN" https://<service-url>/agents/status

# Full chain, writing nothing
curl -X POST -H "X-Ops-Token: $OPS_TOKEN" "https://<service-url>/orchestrator/daily?dry_run=true"

# Turn one agent off without a redeploy
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars AGENT_DISABLED=auto_publish

# Stop all autonomous publishing immediately (drafts still queue for review)
gcloud run services update yishun-agents --region asia-southeast1 \
  --update-env-vars AUTO_PUBLISH_CONFIDENCE=2.0
```

`AUTO_PUBLISH_CONFIDENCE=2.0` is the panic switch: unreachable, so everything
routes to the War Room, and nothing else about the pass changes.

### Where to look when something is wrong

| Question | Where |
|---|---|
| What did the fleet do last night? | `GET /agents/status`, or `agent_runs` |
| Why did nothing publish? | `agent_runs.stats.skip_reasons` for `auto_publish` |
| Which source is broken? | `pipeline_state.last_reason`, War Room → HEALTH |
| Is the model improving? | `learning_snapshots`, newest row |
| Did an alert actually send? | `notifications.status` |
| What happened last month? | War Room → REPORTS |

---

## 8. What is still human-only

Auto-publish handles new incidents above the confidence bar. Everything below
remains a person's job, by design:

- **`update` rows** — merging new reporting into a published story
- **Pattern alerts and lifecycle conclusions** — sentinel rows that ask a question
- **Corrections to published incidents** — the integrity agent reports, and only
  auto-fixes `corroboration_count` drift and unprocessed queue duplicates. It
  never rewrites a published incident's text, dates, or sources
- **Rejections** — nothing is auto-rejected. A draft that fails a gate waits
- **Anything the operator unpublishes** — which is the correction signal the
  whole learning loop is built to consume
