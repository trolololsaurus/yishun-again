# Cost + Classification Programme — 2026-07-30

**Status, as at 2026-07-30:** Landed, tests green (23/23 files then), **not
deployed at the time of writing**. Everything dated below is a snapshot of that
day; the suite has since grown to **30 files, all passing**, and later changes to
this code are flagged inline as forward notes rather than folded into the record.
**Companion to:** `AUTONOMY.md` (§5b, §5c are new), `INGESTION_CHANGELOG.md`, `QA_BACKLOG.md`.

Everything below was measured against the live archive, not assumed. Where a
number appears, the command that produced it is named. Where a spec in the
original brief turned out to be wrong about the codebase, that is recorded too —
those are the entries most likely to be re-attempted by a future agent.

---

## 0. Baseline (measure against this)

`packages/agents/tools/baseline_report.py` (new, read-only). Re-run it after any
change to grouping or consolidation:

```bash
cd packages/agents && ./.venv/Scripts/python.exe tools/baseline_report.py
```

Captured 2026-07-30, 60-day window:

| Metric | Value |
|---|---|
| **Published incidents with `cardinality(source_urls) = 1`** | **75.6%** (96/127) |
| Guardrail #1 breaches (0 source URLs) | 0 |
| Source-count distribution | 1:96 · 2:13 · 3:8 · 4:3 · 5:2 · 6:1 · 7:1 · 9:1 · 10:1 · 12:1 |
| `war_room_queue` | approved 148 · update_approved 39 · rejected 30 · pending 3 |
| Clustering events ever logged | 3 (1 `shadow_cluster`, 2 `cluster_write`) |

> **The single-source percentage is the programme's success measure.** Cost
> falling while that number *rises* means money was saved by breaking grouping.
> Always read the two together.

---

## 1. Batched grouping replaced pairwise judging + union-find

**The correctness argument, which matters more than the cost one.** Pairwise
judging fed union-find, and union-find merges *transitively*: A~B confirmed and
B~C confirmed merged A, B **and** C with nothing ever comparing A to C. That is
the mechanism behind the beehive / car-crash / fatal-fall blob the shadow run
caught — no individual judgement was wrong; the blob was assembled *between*
them. One grouping call has no union-find and contrasts every member at once, so
a group can only be formed by a decision that actually saw all of its members.

`test_clustering.py` proves the mechanism directly (`OLD pairwise+union-find
chains A~B and B~C into one 3-member blob` vs `NEW batched grouper keeps the
un-compared member out`). **Do not delete that test** — it is the regression
guard for the whole change.

### What changed

| Location | Before | After |
|---|---|---|
| `ingestion/clustering.py` | `cluster_with_confirmation(candidates, judge)` | **`group_candidates(candidates, grouper)`** |
| `ingestion/orchestrator.py` | `_make_merge_judge()` (pairwise) | **`_make_grouper()`** (one call, strict JSON) |
| `consolidation/check.py` | up to 12 pairwise `_judge_pair` calls | **one `_judge_batch` call** |

- The keyword pass is now **only an input filter** — it decides who is *offered*
  to the grouper (bounding prompt size). It no longer decides who merges.
- `cluster_candidates()` (keyword-only) remains the fallback when Anthropic is
  unconfigured. `cluster_with_confirmation()` is retained but unused.
- **Degrade-to-split is absolute.** An errored, unparseable, or non-partition
  grouper response yields all-singletons — never a merge. `_validate_index_groups`
  rejects anything that is not an *exact* partition of `range(n)`: a missing
  index, a duplicate, an out-of-range value, a non-int, `True` (bool is an int
  subclass), or an empty group.

### Measured

Live shadow run (`CLUSTER_BEFORE_WRITE=shadow` set process-locally, `dry_run=True`):

```
[shadow-cluster] 4 candidate(s) -> 1 cluster(s); 1 multi-member,
                 ~3 Sonnet draft(s) would be saved (grouper: 4 offered / 1 merged / 0 error(s))
NEW batched grouper: [4] AsiaOne / Lianhe Zaobao / Mothership / Straits Times — Yishun condo car-park fire
OLD pairwise:        [4] (identical)
DIFF: IDENTICAL grouping on all 4 candidate(s).
NEW Haiku calls: 1     OLD Haiku calls: 3
```

Consolidation, measured per pool size:

| Pool | OLD calls | OLD input tok | NEW calls | NEW input tok | Δ |
|---:|---:|---:|---:|---:|---:|
| 12 | 12 | 7,956 | **1** | 1,778 | −78% |
| 30 | 12 | 7,956 | **1** | 3,506 | −56% |
| 50 | 12 | 7,956 | **1** | 5,426 | −32% |
| 100 | 12 | 7,956 | **1** | 10,226 | **+29%** |

> **Crossover at ~85 records.** Above that the batch costs *more* input tokens
> than the old capped fan-out, because the old cap simply discarded everything
> past the top 12 rather than judging it. Live pools are **median 40, max 51**
> (published pool capped at 50, queue pool held only 3), so production is
> comfortably in the savings zone. If `CANDIDATE_FETCH_LIMIT` or
> `QUEUE_FETCH_LIMIT` is ever raised, re-check this.

### Retired but still defined

`CLUSTER_MAX_JUDGES`, `MAX_JUDGEMENTS_PER_CANDIDATE` and `EARLY_EXIT_CONFIDENCE`
are **no longer read on any live path**. The two consolidation constants are not
imported by `check.py` at all; `CLUSTER_MAX_JUDGES` survives only as the default
argument of `cluster_with_confirmation`, which is itself retained but unused. All
three remain defined (and env-settable) so an existing deployment config is not
an error. `test_consolidation_cost.py` asserts of the two consolidation constants
both that they still exist *and* that `check.py` no longer imports them — so a
future agent cannot quietly reintroduce a cap and think it is doing something.

---

## 2. Oversized clusters: shred → one row + an earned gate

**Before:** a cluster larger than `CLUSTER_MAX_SIZE` (6) was *shredded* into one
queue row per member. Defensible under union-find, where a group of 8 could be a
transitive blob no single decision saw whole.

**Now that union-find is gone, the shred was a net negative.** It burned N Sonnet
drafts to produce either one single-source row (when consolidation caught the
siblings) or several near-duplicates (when it didn't) — manufacturing exactly the
single-source incidents clustering exists to eliminate. The live archive holds
**7-, 9-, 10- and 12-source incidents**; a cap of 6 would have shredded every one.

**After:** the cluster is written **intact, as one row**, flagged
`raw_content._oversized_cluster = N`. `auto_publish` holds it
(`oversized_cluster_unproven`) — **but only until the grouper has earned it.**

See `AUTONOMY.md` §5b for the full rule. Summary:

```
trusted = (approvals + rejections) >= OVERSIZED_MERGE_MIN_SAMPLES   # 5
          and (approvals+1)/(approvals+rejections+2) >= OVERSIZED_MERGE_TRUST  # 0.80
```

| Record | Trust | Auto-publishes? |
|---|---:|---|
| 0-0 | 0.50 | no — below the sample floor |
| 2-0 | 0.75 | no — clears the rate, not the floor |
| 5-0 | 0.86 | **yes — earned, gate lifts itself** |
| 5-1 | 0.75 | no — one rejection **re-arms** it |
| 14-1 | 0.88 | yes |

> **Why 0.80 and not `learning_monitor`'s 0.70.** Laplace smoothing alone clears
> 0.70 after **two** approvals (`(2+1)/(2+0+2) = 0.75`). Two data points is not a
> track record for a decision that can conflate several real events into one
> public record. **`AUTONOMY.md`'s older claim that 0.700 takes "about 5 clean
> approvals" is wrong for the zero-rejection case** — it takes 2. The sample
> floor is the load-bearing half of this gate.

---

## 3. Numeric locality veto

`extract_keywords` is `re.findall(r"[a-z]{4,}")` — **digits can never become
keywords.** So "Block 512" vs "Block 900", the single most discriminating fact
between two Yishun car-park fires, was completely invisible to grouping: the two
stories look lexically identical.

- `consolidation/rules.py::extract_locality_tokens(text)` → namespaced tokens
  (`{"blk:512", "st:81", "ave:4"}`). **`extract_keywords` is untouched** —
  consolidation ranking, clustering edges and the stop-word set all depend on its
  exact output.
- `consolidation/rules.py::locality_conflict(a, b)` → conflict requires both
  sides to carry a token in the **same namespace** and for those to be disjoint.
  Deliberately *not* a conflict: either side empty, overlapping tokens
  (`blk:512` vs `blk:512 + st:81` is one place described in more detail), or
  different namespaces (`blk:512` vs `st:81` — a block sits on a street).
- `clustering._veto_group` applies it **after** the grouper, overruling it. A
  group containing a provable contradiction is split to singletons wholesale
  rather than partially repaired.

Deterministic, offline, no model call.

---

## 4. Stage 2 write model → Haiku, with two safety changes

### The eval that justified it

`tools/dump_l2_eval_set.py` builds `fixtures/l2_eval_set.json` (frozen, 30 real
inputs: 15 single-source from the queue, 15 clustered with 3–9 **re-fetched**
sources). `tools/eval_l2_write.py` runs both models over identical inputs.

> **Why the multi-source half is re-fetched, not read.** No `war_room_queue` row
> anywhere carries `source_articles` — clustering has only ever written 2 rows,
> so per-source article text does not exist in storage. The only way to build a
> realistic clustered input is to re-fetch a published incident's own sources
> (`seed_backfill.fetch_article`, which has the Wayback fallback that
> Cloudflare-blocked outlets need).

Gate metric — ungrounded specifics, artifact-filtered (bare years and
sentence-initial phrases removed, symmetrically for both models):

| half | model | n | ungrounded | drafts affected |
|---|---|---:|---:|---|
| single | sonnet | 14 | 5 | 5/14 |
| single | **haiku** | 15 | **5** | **4/15** |
| multi | sonnet | 15 | 4 | 3/15 |
| multi | **haiku** | 15 | **4** | **2/15** |

Format compliance was **100% for both models** on slug format, title-contains-
Yishun, title ≤120, `seo_title` ≤60, `seo_description` ≤155. Haiku ran ~24%
shorter.

### Change 1 — summary length is now arithmetic

The "~1600 char" ceiling was prose the models ignored: **Sonnet exceeded it on
10 of 29 eval drafts** (worst 2765). Now computed per call and interpolated as a
hard number:

```
budget = clamp(SUMMARY_FLOOR, SUMMARY_HARD_CEILING, STAGE2_SUMMARY_RATIO x non-signal source chars)
       = clamp(400, 1600, 0.75 x chars)
```

**RATIO was derived, not guessed** — measured across 30 real approved summaries
against their own source bodies. The distribution is strongly **bimodal**:

| half | n | min | p25 | median | p75 | max |
|---|---:|---:|---:|---:|---:|---:|
| single-source | 15 | 0.359 | 0.494 | **0.612** | 0.754 | 0.929 |
| multi-source | 15 | 0.032 | 0.086 | **0.104** | 0.138 | 0.265 |

A ~6× gap, because a merged cluster carries far more text than any summary needs.
**The single-source end is what binds** — using multi's 0.104 would cap a
1343-char single source at 139 chars. 0.75 is single-source p75: it clears 75% of
real approved summaries untruncated, while any cluster above ~2.1k source chars
still saturates 1600 (every multi item in the sample was ≥3633).

Effect: Sonnet's over-1600 breaches **10 → 2 of 30**; multi mean 1759 → 1363.

### Change 2 — deterministic groundedness post-check

`find_ungrounded(summary, source_text, date_str)` — numbers and capitalised
multi-word proper nouns in the summary appearing nowhere in the non-signal source
text. On failure: **regenerate ONCE**; still failing → flag
`raw_content._groundedness.flagged` and block auto-publish
(`ungrounded_specifics`). Never raises; a checker error degrades to **flag**,
never to pass.

> **The exclusions are load-bearing, not cosmetic.** A naive version flagged
> ~70% of drafts, which would have doubled write cost. Three artifacts are
> excluded, each measured against the 60 real eval drafts:
> - **4-digit years** — the incident year reaches the model via the `date` field,
>   not the article body, so "in 2026" is grounded but would not match.
> - **month names** derived from `date` — "in August" for a `2026-08-15` story.
> - **a phrase whose tail matches** — a sentence-initial capital glues an ordinary
>   word onto a real name ("On August", "As Jethro"); an article differs only by
>   "The".
>
> Final validated rate: **Sonnet 17%, Haiku 23%** — one extra write call on
> roughly one draft in four. If you tighten the checker, **re-measure this rate
> before shipping.**

The cost of that conservatism is missing an invention that merely *extends* a
real name. That is the correct direction: the flag blocks auto-publish, so a miss
degrades to today's behaviour while a false positive burns a model call.

### Change 3 — the model

`MODEL_WRITE = claude-haiku-4-5-20251001`, env-overridable via
`STAGE2_WRITE_MODEL` so a rollback is a config change, not a redeploy.

---

## 5. Deterministic deaths/injuries validation

`filters/casualty_check.py` (new). The classify prompt calls deaths/injuries a
legal record with STRICT rules — this is the reason not to take the model's word
for it.

**It only ever FLAGS. It never corrects and never overwrites the model's value.**
Language is ambiguous and this is a regex; a validator confident enough to
rewrite a legal record would be more dangerous than the error it prevents.

- Confirmed past-tense death language vs the prompt's own exclusion list
  ("critical condition", "fighting for his life", "hospitalised", "suspected").
- **Negation windows.** "no one was killed" / "nobody died" / "no fatalities" are
  not deaths. This also applies to injury counts — and there it is load-bearing:
  `\b(one)\s+…(injured)` matches inside **"no one was injured"**, which was the
  single largest source of false flags on live data.
- Ambiguity flags; it does not correct. Vague "several people" yields no count.

Disagreement sets `raw_content._casualty_check.flagged` and blocks auto-publish
(`casualty_mismatch`).

**Measured false-positive rate on 190 real production drafts:**

| | flag rate |
|---|---|
| Before the negation fix | 5.0% |
| **After** | **2.6%** |
| Of 165 operator-**approved** drafts | 2.4% held |

The survivors are genuine discrepancies (model said 3 injured, source says 2).

---

## 6. Guardrail #4 made audible (not weaker)

The guardrail is unchanged: political content still has confidence forced to
`0.0` in `_classify` and still cannot reach the 0.95 gate. What was added is
**audibility** — under unattended operation a confidence-0 row was
indistinguishable from any other low-confidence row, so the story was dropped and
the operator never learned it existed.

- **Distinct marker** — `raw_content._political_flagged` (with source URL and
  `confidence_forced_to: 0.0`), separate from merely being low-confidence.
- **Operator notification** via `ops/notify.py`, `dedup_key=f"political:{url}"`
  so re-running a pass over the same story cannot spam the existing ledger.
- **`agent_events` row at level `warning`** carrying the title and source URL.
- Raised **before** the consolidation skip-check, so a political item that also
  duplicates an existing row is still reported. Otherwise the loudest cases —
  stories covered by several outlets — would be the most likely to stay silent.

> **Known false-positive mode, deliberately surfaced.** This classifier
> over-triggers on ordinary crime and news that merely mentions an MP or the
> People's Association, despite the prompt saying that is NOT political. The
> notification is precisely what makes that rate visible — before this, the
> misfires were invisible too.

`test_political_alert.py` asserts there is no env switch that can disable it
(`POLITICAL_ENABLED`, `ALLOW_POLITICAL`, `SKIP_POLITICAL`, `POLITICAL_OVERRIDE`,
`DISABLE_POLITICAL` are all absent) and that nothing in the alert path can write
back to `confidence`.

> **Forward note (2026-08-02) — none of the above could fire on part of its own
> target.** As shipped here, `_classify` validated its fields *before* reading
> `political`, and `result["classification"].lower()` raised `AttributeError`
> whenever the model returned `"classification": null` — which is exactly what it
> tends to do on a political story, because the prompt tells it to reject rather
> than categorise. The candidate died on that exception, so confidence was never
> forced to 0, the reject marker was never prepended, and neither the operator
> email nor the `agent_events` warning row was ever written. Observed live on an
> MP-resignation article. The guardrail block now runs **first** in `_classify`,
> ahead of any validation that can raise, and a political row whose classification
> is unusable is given a placeholder category so the reject path can complete.
> Guard: `test_stage2_guardrails.py`.

---

## 7. Truncation guard on every LLM call

**`stop_reason` was read nowhere in the repo.** Every model call parses JSON; a
reply cut off at `max_tokens` is invalid JSON, and `_parse_json` then reports
*"No JSON object in model response"* — **byte-identical to the error a model
returning prose produces.** The trivially fixable fault was disguised as the hard
one.

`filters/model_call.py` (new) — see `AUTONOMY.md` §5c for the runbook.

Measured headroom on the largest real inputs; **nothing truncates today**:

| Call | Cap | Observed | Spare |
|---|---:|---:|---:|
| `stage2._write_draft` | 2048 | 763 | 63% |
| `stage2._classify` | 512 | 167 | 67% |
| `consolidation._judge_batch` | 1024 | 128 | 88% |
| `clustering._make_grouper` | 1024 | 132 | 87% |

Recovery: **one** retry at double the cap (ceilinged at 8192), recorded as
`raw_content._write_truncation_retry` (does *not* block publishing — the retry
produced a valid draft), then a loud named `TruncatedResponse`.

> **The grouper is the case that most needed this.** A truncated partition is not
> an *exact* partition, so `group_candidates` correctly degrades to
> all-singletons — safe, but it means **a pass would silently stop merging
> entirely while looking healthy.** Exactly the "cost falls while single-source
> rises" trap. Now it surfaces as a logged retry.

> ⚠️ `filters/stage2_writer.py` documents itself as runnable directly
> (`python packages/agents/filters/stage2_writer.py`), which puts `filters/` on
> `sys.path` instead of `packages/agents/`. Its import of `model_call` therefore
> needs the `try: from filters.model_call … except ImportError: from model_call …`
> fallback. **Do not "tidy" that into a single import** — it breaks the smoke path.

---

## 8. Few-shot learning context

`ingestion/learning.load_recent_signal_patterns()` returned aggregate counts
("Operators re-classified 3 item(s) from 'clown' to 'dagger'"). That is unusable
by a frozen model: it says a correction happened but not *which kind of story*
was corrected, so there is nothing to pattern-match against. It now returns 5–8
**concrete labelled examples**.

- Title resolved via two batched lookups: `queue_id` → `war_room_queue.proposed_title`
  (35/35 where present) and `incident_id` → `incidents.title` (137/172 rows).
- Examples are **round-robined across reject reasons**, not taken in recency
  order: 10 of the 30 live rejections are `duplicate`, and eight duplicate
  examples would teach one lesson eight times instead of teaching eight.
- Bounded: `MAX_EXAMPLES=8`, `EXAMPLE_TITLE_CHARS=110`, `MAX_EXAMPLES_CHARS=1400`
  — the block is injected into **both** the classify and the write call.
- Cold start returns `""` exactly as before.

### ⚠️ Two dead columns found while doing this

Both are latent data-loss bugs, **not fixed** (out of scope), and both silently
degrade the learning loop:

1. **`training_signals.corrected_classification` is 0/172 filled.** The column the
   learning loop was designed around has never been written. The original brief's
   primary example source ("where corrected_classification differs from
   proposed_classification") therefore yields nothing; examples come from the 30
   reasoned rejections instead. `original_classification` → `edited_classification`
   captures exactly **1** real change in 172 rows.
2. **`edited_draft` is byte-identical to `original_draft` in all 36
   `approve_with_edits` rows.** Operator edits are not being captured as a diff,
   so the richest correction signal in the product is being written and thrown
   away.

---

## 9. The watermark advanced on WRITES, not on decisions

Found by the QA review of this programme, and it is the one item here that was
costing money every day rather than merely risking it.

`pipeline_state.watermark` is defined (INGESTION_DESIGN.md §8) as the max
`published_at` **actually ingested**. The orchestrator read "ingested" as "written
to `war_room_queue`": `max_published_at` moved only when `queued` was true, and the
`'on'` path's `per_source_max` only for a written cluster's non-signal members.

Everything else in the candidate loop is invisible twice over:

| Outcome | Written anywhere? | Visible to `dedup.is_duplicate` next pass? |
|---|---|---|
| Stage 1 rejection (60–70% of raw volume) | no | **no** |
| Consolidation `skip` (duplicate of a *pending* queue row) | no | **no** |
| Non-primary member of a written cluster | only in `raw_content` | no, until the incident is published |
| Signal member of a written cluster | never in `source_urls` (guardrail #2) | **never** |

`dedup` looks only at `war_room_queue.source_url` — which `build_queue_row` sets
to the **primary** member's URL alone — and at `incidents.source_urls`. So for
every row of that table the watermark was the only mechanism that could stop a
re-fetch, and it was declining to move. Those articles were re-fetched,
re-Stage-1'd (Gemini), re-drafted (two Haiku calls) and re-judged by consolidation
on **every daily pass**, until an unrelated candidate from the same source happened
to drag the watermark past them.

> **Forward note (2026-08-02): `source_url` is no longer copied from the candidate
> unchecked.** The claim above still holds — the row carries exactly one headline
> URL, the primary member's — but `build_queue_row` now tests it against
> `source_allowlist.is_redirect_domain()` first and substitutes the first surviving
> real publisher URL when the primary is a redirector, flagging it in
> `raw_content._source_allowlist.redirect_source_url` either way. The trigger was
> two live rows on 2026-08-01 whose visible source was a
> `news.google.com/rss/articles/<blob>` wrapper: dedup matches on URL, so a wrapper
> matched nothing and proposed an update to a story the archive already held. The
> aggregator that produced them (`ingestion/sources/google_news_rss.py`) has been
> deleted; discovery is now the publishers' own news sitemaps and WordPress search
> feeds, both of which emit canonical publisher URLs.

### The fix: `decided` vs `unresolved`, in `ingestion/watermark.py`

The distinction that matters is not written-vs-unwritten, it is **settled vs
interrupted**. Every branch of the candidate loop now marks its source's
`WatermarkTracker` exactly one way — `decided(c)` for a verdict another pass would
only pay to reproduce, `unresolved(c)` for an interruption (model or DB error, pass
deadline, mid-pass Stage 1 budget halt, a gathered candidate the cluster phase
never reached). **A new `continue` or `break` in that loop that marks neither
either loses the story or re-buys it daily.**

### Two holdbacks, and why the fix is unsafe without them

`RecencyFilter` drops `published_at <= watermark`, so the watermark is a
date-granular guillotine: advancing it to one candidate's date drops every same-day
sibling. While only written candidates advanced it that was a rare corner; now it
is the common case, so both of these are part of the fix, not decoration.

1. **Retry floor.** Only decided dates *strictly below* the earliest unresolved
   date advance. Without it a candidate whose write errored would be deleted from
   the future by its own successfully-decided siblings. This also closes a latent
   data loss that predates the change: a mid-pass Stage 1 budget halt `break`s the
   candidate loop but still wrote the watermark, and because most RSS lists
   newest-first the candidates it never examined were the *older* ones — silently
   dropped.
2. **Same-day grace.** A decided candidate dated on or after the pass date is held
   anyway. An outlet publishes all day; the pass runs once, at 14:58 SGT.
   Advancing onto today's date would drop everything that source filed after the
   pass ran, unseen and unlogged — far worse than the cost bug being fixed.

> **The residual cost is one extra pass per article, and that is deliberate.**
> Tomorrow the date is in the past and advances normally. Eliminating even that
> would mean persisting every rejected URL — a new table and a hand-applied
> migration (QA M15) to save a bounded 2×, against the unbounded ∞ this replaces.
> Not worth it; **do not "optimise" the grace away** without replacing it with
> something that keeps this afternoon's stories reachable.

### Also changed, same root cause

- **`'on'` mode advanced nothing when nothing cleared Stage 1.** The watermark
  write lived inside `if gathered and not abort_pass`, so the emptiest passes —
  the ones with the least to show for their Gemini spend — kept every candidate
  for tomorrow. It now runs whenever sources were fetched.
- **Signal members of a written cluster now advance their own source's watermark.**
  Excluding them meant a merged Reddit/EDMW post was re-drafted every pass, and
  once its MSM siblings had been deduped away it came back **alone** — as an
  unverified signal-only queue row for a story already in the archive.
- The vestigial `for grp in [cluster]:` single-iteration loop (left from the
  pre-§2 shred) is gone, and `_write_clusters` no longer needs its
  `if aborted: advance nothing` special case: per-candidate holds are strictly
  more precise, so a source whose every member was written before an abort still
  advances.

### Measured

**Structurally, not against the live archive** — the saving is per-article calls
per pass, and this branch is not deployed, so there is no live delta to quote yet.
`test_watermark_advance.py` asserts it as a call count: a consolidation-skipped
article costs Stage 1 + Stage 2 on the pass that discovers it and **0 + 0** on the
next, where before it cost the same again every pass indefinitely.

To measure it live once deployed, read the new pass note — `N candidate(s) dropped
by consolidation as duplicates of rows already awaiting review` — across
consecutive `pipeline_run_history` rows. Under the bug that count was recurring
spend; it should now fall to only genuinely new duplicates. Read it with
`baseline_report.py`'s single-source percentage, per the warning at the top of this
document.

> **One case a watermark cannot fix, unchanged: dateless candidates.** They bypass
> `RecencyFilter` entirely by design (QA H3), so a dateless candidate that Stage 1
> rejects or consolidation skips *is* still re-processed every pass — there is no
> date to advance to. The tracker records nothing for them rather than pretending
> otherwise. This is bounded only by the rule that a source must supply
> `published_at` to be registered at all.

---

## New environment variables

All optional; defaults match the values shipped. See `.env.example` for the
narrative version.

| Var | Default | Purpose |
|---|---|---|
| `STAGE2_WRITE_MODEL` | `claude-haiku-4-5-20251001` | Write model; rollback lever |
| `STAGE2_SUMMARY_RATIO` | `0.75` | Summary budget as a fraction of source chars |
| `STAGE2_WRITE_MAX_TOKENS` | `2048` | Output cap (truncation recovery lever) |
| `STAGE2_CLASSIFY_MAX_TOKENS` | `512` | Output cap |
| `CONSOLIDATION_BATCH_MAX_TOKENS` | `1024` | Output cap |
| `CONSOLIDATION_PAIR_MAX_TOKENS` | `400` | Output cap (`_judge_pair`, still used by `ops/integrity`) |
| `CLUSTER_GROUPER_MAX_TOKENS` | `1024` | Output cap |
| `OVERSIZED_MERGE_TRUST` | `0.80` | Oversized-merge graduation threshold |
| `OVERSIZED_MERGE_MIN_SAMPLES` | `5` | Oversized-merge sample floor |

## New `auto_publish` gate reasons

Added to `check_eligibility`. All leave the row **`pending` for the operator** —
none reject anything.

| Reason | Meaning | Lifts when |
|---|---|---|
| `ungrounded_specifics` | A number or proper noun in the summary is in no source, and a regeneration did not clear it | Never automatically — it is a factual defect in *that row* |
| `casualty_mismatch` | Source language and the model's deaths/injuries disagree | Never automatically — same reason |
| `oversized_cluster_unproven` | One grouping call merged more than `CLUSTER_MAX_SIZE` articles | **Automatically**, once the grouper has earned it (§2) |

> The asymmetry is deliberate. An oversized merge is a *judgement call* that can
> be earned. An ungrounded specific or a casualty mismatch is a *factual defect*
> in one specific row; there is nothing to earn.

---

## Not done, and why

- **Prompt-caching the consolidation pool (brief §1.2) — STOPPED, correctly.**
  Two independent reasons the premise does not hold: (a) the pool is **not**
  identical within a pass — `orchestrator._emit` inserts into `war_room_queue` in
  the same per-candidate loop that calls `consolidation_check`, and
  `_fetch_recent_queue` reads unprocessed rows `created_at DESC LIMIT 50`; (b)
  more fundamentally, **`_judge_pair` sends one record per call, not the pool** —
  there is no archive-pool prefix to cache. The only stable bytes are
  `_SYSTEM_PROMPT`, measured at **374 tokens against Haiku 4.5's 4096-token
  minimum cacheable prefix**, so a `cache_control` marker would silently no-op
  (`cache_creation_input_tokens: 0`) forever. **Do not re-attempt this against
  the pairwise shape.** If caching is wanted, the batched `_judge_batch` prompt is
  the candidate — but its per-candidate portion still varies, so only the system
  prompt is stable, and it is still far below 4096 tokens.

  > **Forward note (2026-08-02).** The batched shape named at the end of that
  > bullet was then measured properly and rejected too; the verdict is unchanged
  > but the reasoning is now recorded against the *live* code, in the comment
  > block above `UPDATE_MATCH_THRESHOLD` in `consolidation/rules.py`. Read that,
  > not this bullet, before re-attempting A2. Its four findings: the prefix is not
  > byte-identical across candidates *by construction* (`check.py` keyword-filters
  > and ranks the pool per candidate — 39 of 54 records for one measured
  > candidate); the filtered prompt measures 3,889 tokens against Haiku 4.5's
  > 4,096-token minimum, and the full unfiltered pool only 5,259; the pass averages
  > 3.0 candidates against a ~2-call break-even; and A2's premise — "~87 Haiku
  > calls in 3 minutes" — expired when batching collapsed that to one call per
  > candidate.

- **`max_tokens=2048` was never actually a problem** — 763/2048 observed. It was
  guarded (§7) rather than changed.

## Open items found in passing (not fixed)

| Item | Evidence |
|---|---|
| ~~`CLUSTER_BEFORE_WRITE` set nowhere in the repo~~ ✅ **fixed** — pinned in `infra/cloudbuild.yaml` | see below |
| `integrity` agent ran **degraded on 12/12** passes | `baseline_report.py` §1 |
| `training_signals.corrected_classification` never written (0/172) | §8 |
| `edited_draft` == `original_draft` in all 36 rows | §8 |
| `AUTONOMY.md`'s "about 5 clean approvals to clear 0.700" is wrong (it is 2) | §2 |
| `_write_draft` `max_tokens` now over-provisioned after `pixel_art_prompt` removal | §7 — harmless, guarded |
| ~~0a RLS gate never verified~~ ✅ **PASS** — audited 2026-07-30 | `tools/rls_audit.py`, see below |

### `CLUSTER_BEFORE_WRITE` was untracked config — now pinned ✅

**Found:** the var appeared in **neither** `.env` **nor** `infra/cloudbuild.yaml`,
so `os.getenv("CLUSTER_BEFORE_WRITE", "off")` resolved to **`off`** anywhere the
repo was the source of truth. Production was plainly running `on` — the archive
holds `cluster_write` `agent_events` dated 2026-07-28 and 2026-07-29, and only
that mode emits them. The value had been set out-of-band on the Cloud Run service.

**Precisely what the risk was** (an earlier draft of this section overstated it,
and the correction is worth keeping): a plain `cloudbuild.yaml` deploy would
*not* have dropped the var — `gcloud run deploy` **preserves** env vars it is not
told about. The actual trap was this file's own documented one-time-setup
command, which uses **`--set-env-vars`** — that flag **replaces the entire env-var
set**, deleting every key not listed, and `CLUSTER_BEFORE_WRITE` was not listed.
Anyone copy-pasting the documented command would have silently turned clustering
off.

**Why it matters more than a normal missing default:** `off` is a *valid* mode.
It raises nothing and logs nothing; the pass just writes one row per URL again.
The only symptom is the single-source percentage drifting back toward its 75.6%
baseline over weeks — the exact failure this programme exists to prevent, and the
one nobody watches for.

**Fixed:** the deploy step now carries
`--update-env-vars CLUSTER_BEFORE_WRITE=on`. `--update-env-vars` **merges** —
it sets that one key and leaves the secrets and Stage 1 config untouched. **Do
not change it to `--set-env-vars`**, which would wipe them. The header's
one-time-setup command now also lists the var and carries the replace-vs-merge
warning.

**Only vars whose code default differs from production intent belong there.**
`STAGE2_WRITE_MODEL`, `STAGE2_SUMMARY_RATIO`, the `max_tokens` caps and the
`OVERSIZED_MERGE_*` knobs all default correctly in code; pinning them would just
create a second source of truth to drift.

---

## The 0a security gate — PASS (2026-07-30)

Originally specified as a hand-run `pg_class` query in the Supabase SQL Editor,
and skipped. It is now `tools/rls_audit.py` — read-only, exit code 1 on
exposure, so it can gate a deploy and cannot be skipped by omission again.

**It tests something stronger than the catalog query did.** `pg_class.relrowsecurity`
tells you RLS is *enabled*; it says nothing about a permissive `USING (true)`
SELECT policy, which passes that check while leaving the table world-readable.
This instead attempts the read with the publishable key and reports what comes
back.

```
table                        secret  publishable   verdict
agent_runs                       88            0   protected
agent_events                    999            0   protected
notifications                    39            0   protected
learning_snapshots               15            0   protected
monthly_reports                   0            0   protected (table empty — weak evidence)
backend_health_checks            65            0   protected
war_room_queue                  220            0   protected
training_signals                172            0   protected
source_reputation                 9            0   protected

CONTROL incidents: secret sees 166, publishable sees 160, published=160 -> as documented
```

**The control is what makes the zeros trustworthy.** `incidents` returns exactly
the 160 published rows to the publishable key — so the key demonstrably *can*
read, and a 0 elsewhere is RLS doing its job rather than a misconfigured client.
Drafts are invisible to it, as CLAUDE.md documents.

Caveat kept honest in the output: `monthly_reports` is empty, so "protected" is
weak evidence there. Re-run once the first monthly report exists.

**Migration 011, second half of the gate — APPLIED.** Proven from existing rows
rather than a trial insert (a row that exists carrying a post-011 value is proof
the CHECK accepts it, and costs no write):

```
decided_by column present     : True  (values seen: ['agent', 'operator'])
action='auto_approve' rows    : 2  -> CHECK accepts it (pre-011 would reject)
action='auto_publish_reverted': 0  (none yet — expected, it is a rare path)
```

Without 011 every autonomous decision insert is silently rejected while every
publish succeeds — live incidents nobody approved, and no record that an agent
chose them. It is applied.

---

## How to verify after deploying

```bash
# 1. Suite (30 files today, all must pass — standalone scripts, not pytest)
cd packages/agents && for f in test_*.py; do ./.venv/Scripts/python.exe "$f" || echo "FAIL $f"; done

# 2. Security gate — exits 1 if any ops table is publicly readable
./.venv/Scripts/python.exe tools/rls_audit.py

# 3. The success measure — re-run after a few daily passes
./.venv/Scripts/python.exe tools/baseline_report.py
```

Read **single-source %** and cost together. Cost falling while single-source
percentage rises means the saving broke grouping.

**Pre-deployment reading, locked in 2026-07-30 06:36 UTC** (re-confirmed
unchanged after the programme landed, because none of it is deployed yet):

| | |
|---|---|
| Published, 60-day window | 127 |
| `cardinality(source_urls) = 1` | 96 |
| **Single-source percentage** | **75.6%** |
| Last daily pass | 2026-07-29T07:02 UTC |

This number cannot move until the branch is deployed AND several daily passes
have run — clustering only affects incidents written *after* it ships.
