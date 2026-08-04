"""
run_ingestion_pass() — the ingestion entrypoint (INGESTION_DESIGN.md §4,
§5, §5.4, §6, §7; build order §10b step 9).

Pure function of (sources, now) + external state. NEVER raises to the
caller — every failure mode (per-source fetch failure, per-candidate
processing error, infrastructure failure) is captured in the returned
IngestionReport.

dry_run=True runs the full fetch -> recency -> dedup -> Stage 1 -> Stage 2
-> consolidation path but writes NOTHING: no war_room_queue rows, no
incident_links rows, no watermark advance, no pipeline_run_history row, no
scraper_health row, no budget persistence.

WATERMARKS ARE NOT ADVANCED BY WRITES — they are advanced by DECISIONS. A
Stage-1 rejection and a consolidation duplicate-skip are verdicts that no later
pass would change, and neither writes a row, so `dedup.is_duplicate` cannot see
them next pass either. While only queued candidates advanced the watermark, those
articles were re-fetched, re-Stage-1'd and re-drafted every single day. Each
source therefore gets a `WatermarkTracker` (ingestion/watermark.py) which every
branch of the candidate loop must settle exactly one way: `decided` for a verdict,
`unresolved` for an interruption (error, deadline, budget halt, a gathered
candidate the cluster phase never reached). Adding a new `continue` or `break` to
that loop without marking the tracker either loses the story or re-buys it daily.

KNOWN v1 GAPS (flagged, not fixed here — see chat for detail):
  - IngestionReport.phenomenon_count is always 0. The phenomenon/"kind"
    model from INGESTION_DESIGN.md §5.4 (kind='phenomenon_member',
    corroborates, phenomenon_hub_id) was never implemented in
    consolidation/check.py — the real ConsolidationResult only has
    action='new'|'update'|'skip'. This orchestrator routes on `action`,
    per the build instruction to mirror pipeline.py's real
    new/update/skip semantics.
  - signal_summary (learning.load_recent_signal_patterns) is loaded once
    per pass and threaded into the Stage 2 input dict as
    "learning_context". CLOSED: stage2_writer._build_user_message() now
    reads that key (stage2_writer.py:302).
  - edmw_signal_count is 1 for a signal candidate, else 0. Signal detection
    goes through classifiers.source_allowlist.is_signal_source, which accepts
    both vocabulary spellings AND falls back to a domain lookup — a plain
    `== 'edmw'` comparison here silently breached guardrail #2, because
    scrape_edmw emits the canonical 'signal'. The EDMW adapter IS registered
    now (Phase 3), so this is live, not theoretical.
"""

import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone

from classifiers.corroboration import get_supabase_client
from classifiers.source_allowlist import canonical_url, is_signal_source
from consolidation.check import check as consolidation_check, write_incident_links
from consolidation.queue_row import build_queue_row
from filters.stage1_filter import filter_content
from filters.stage1_quota import RPM_LIMIT, RpdExhaustedError, Stage1HaltError
from filters.stage2_writer import write_stage2
from ingestion import dedup, fallback, health, learning, recency, state_store
from ingestion.budget import load_daily_budget, save_daily_budget
from ingestion.contracts import Candidate, IngestionReport, Source, SourceResult
from ingestion.watermark import WatermarkTracker
from orchestrator.herald_agent import check_milestones

logger = logging.getLogger(__name__)


def _log(activity, level: str, event: str, message: str,
         source_name: str | None = None, **detail) -> None:
    """
    Forward one pipeline event to the ops activity log, if a run was supplied.

    `activity` is optional so run_ingestion_pass keeps working standalone (tests,
    CLI, dry runs) with no ops tables present. Wrapped because the orchestrator's
    contract is that it never raises — an observability failure must not become
    an ingestion failure.
    """
    if activity is None:
        return
    try:
        activity.event(level, event, message, source_name=source_name, **detail)
    except Exception as exc:                      # noqa: BLE001
        logger.debug("activity logging failed (non-fatal): %s", exc)


# Per-article text budget in the grouping prompt. The grouper is contrasting
# articles, not reading them closely — the lede carries the entity, act and
# location that decide the grouping.
CLUSTER_ARTICLE_CHARS = 700

# Output cap for the grouping reply, env-overridable. A 40-candidate grouping
# used 132/1024 tokens, but the reply grows with pass width, so the guard in
# filters/model_call matters here more than anywhere: a truncated partition is
# not an exact partition, so group_candidates would (correctly, but silently)
# fall back to all-singletons and the whole pass would stop merging.
CLUSTER_GROUPER_MAX_TOKENS = int(os.getenv("CLUSTER_GROUPER_MAX_TOKENS", "1024"))

_GROUPER_SYSTEM_PROMPT = """\
You group news articles by the real-world EVENT they report, for a Yishun \
(Singapore) incident archive.

Two articles belong in the same group ONLY if they report the SAME entity
performing the SAME act at the same place and time — two outlets covering one
incident, or a later report (charge, trial, sentencing) about that same incident.

Bias to SPLIT. A wrong merge conflates two real events into one archive record
and is hard to unwind; a wrong split is a cosmetic miss a human catches. When in
doubt, separate. In particular:
- The same kind of act at a DIFFERENT block, street or address is a DIFFERENT
  event, however similar the wording.
- The same location on a different day is a DIFFERENT event.
- Two similar incidents (two fires, two falls, two crashes) with no shared
  specific detail are DIFFERENT events.
- A shared generic word ("fire", "block", "dead", "police", "Yishun") is NOT
  evidence that two articles describe one event.

Return JSON only — no prose, no markdown fences:
{"groups": [[0, 2], [1], [3]]}

Rules for "groups":
- Every article index from 0 to N-1 appears EXACTLY ONCE across all groups.
- An article matching nothing else is its own group of one.
- Never emit an index that was not in the list.
"""


def _make_grouper():
    """
    A grouper(candidates) -> list[list[int]] for clustering: ONE Haiku call that
    sees every candidate at once and partitions them by real-world event.

    Replaces the per-pair merge judge. Two reasons, in order of importance:

    1. Correctness. Pairwise judging fed union-find, which merges transitively —
       A~B and B~C merged A, B and C with nothing ever comparing A to C. That is
       how the beehive / car-crash / fatal-fall blob formed. A single call has no
       union-find and contrasts all members against each other directly.
    2. Cost. N candidates cost one call instead of up to CLUSTER_MAX_JUDGES
       pairwise calls, and the system prompt and shared context are sent once.

    Returns None if Anthropic isn't configured — clustering then degrades to the
    keyword-only fallback. A malformed response raises out of `grouper`, which
    clustering.group_candidates catches and treats as all-singletons.
    """
    try:
        from consolidation.check import MODEL, _get_anthropic_client, _parse_json
        client = _get_anthropic_client()
    except Exception as exc:                          # noqa: BLE001
        logger.warning("clustering: no grouper (%s) — keyword-only grouping", exc)
        return None

    def grouper(cands) -> list[list[int]]:
        lines = []
        for i, c in enumerate(cands):
            when = c.published_at.isoformat() if getattr(c, "published_at", None) else "unknown"
            lines.append(
                f"[{i}] date={when} source={getattr(c, 'source_name', '') or 'unknown'}\n"
                f"    title: {getattr(c, 'title', '') or ''}\n"
                f"    text: {(getattr(c, 'content', '') or '')[:CLUSTER_ARTICLE_CHARS]}"
            )
        user_msg = (
            f"{len(cands)} articles follow. Group the ones that report the SAME "
            f"real-world event.\n\n" + "\n\n".join(lines)
        )
        from filters.model_call import create_with_headroom
        response, _retried = create_with_headroom(
            client,
            call="clustering._make_grouper",
            env_var="CLUSTER_GROUPER_MAX_TOKENS",
            model=MODEL,
            max_tokens=CLUSTER_GROUPER_MAX_TOKENS,
            temperature=0.0,
            system=_GROUPER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        data = _parse_json(response.content[0].text)
        raw = data.get("groups")
        if not isinstance(raw, list):
            raise ValueError(f"grouper response has no 'groups' list: {str(data)[:200]!r}")
        # Coerce numeric strings ("2") to int here; group_candidates then applies
        # the strict is-this-an-exact-partition check and splits on any doubt.
        return [[int(v) for v in grp] for grp in raw]

    return grouper


def _alert_political(draft, candidate, *, client, activity) -> None:
    """
    Make guardrail #4 AUDIBLE. It does not make it weaker.

    Stage 2 forces confidence to 0 on political content, so the row can never
    reach the 0.95 auto-publish gate. That behaviour is untouched here. The
    problem it leaves is silence: under unattended operation a confidence-0 row
    looks exactly like any other low-confidence row, so the story is dropped and
    the operator never learns it existed.

    Worth knowing when reading the alerts: this classifier over-triggers on
    ordinary crime and news that merely mentions an MP or the People's
    Association, even though the prompt explicitly says that is NOT political.
    The notification is precisely what makes that false-positive rate visible —
    before this, the misfires were invisible too.

    Never raises: an alerting failure must not cost the pass.
    """
    title = (draft.get("title") or "")[:120]
    url = getattr(candidate, "url", "") or ""
    source = getattr(candidate, "source_name", None)

    _log(activity, "warning", "political_content",
         f"political content flagged, confidence forced to 0: {title} — {url}",
         source_name=source, source_url=url)

    try:
        from ops.notify import notify
        notify(
            "anomaly",
            f"Political content flagged: {title[:70]}",
            (f"Guardrail #4 flagged this item as political, so Stage 2 forced its "
             f"confidence to 0 and it will NOT publish.\n\n"
             f"Title: {title}\nSource: {source or 'unknown'}\nURL: {url}\n\n"
             f"If this is ordinary news that merely mentions a public official, "
             f"the classifier has over-triggered — that is a known failure mode "
             f"and worth noting, because nothing else surfaces it."),
            # Dedup on the URL: re-running a pass over the same story must not
            # re-mail. Uses the existing notifications ledger.
            dedup_key=f"political:{url}",
            client=client,
        )
    except Exception as exc:                      # noqa: BLE001
        logger.warning("political alert failed (non-fatal): %s", exc)


def _emit(stage2_input, item, is_dateless, edmw_signal_count, primary_candidate,
          *, client, dry_run, reputation, notes, oversized: int = 0, activity=None):
    """
    Stage 2 -> consolidation -> build_queue_row -> insert. The single write
    tail, shared by the per-candidate path (members of 1) and the clustered path
    (members of N). Returns (queued: bool, is_update: bool). Raises on a model/DB
    error so the caller's circuit breaker sees it — same as the old inline code.

    A single-member cluster produces byte-identical output to the old
    per-candidate path: build_cluster_stage2_input attaches no source_articles
    for one member, and the stage2_input / build_queue_row args match exactly.
    """
    draft = write_stage2(stage2_input)

    # Guardrail #4 alerting. Raised BEFORE the consolidation skip-check, so a
    # political item that also happens to duplicate an existing row is still
    # reported — otherwise the loudest cases (a story covered by several outlets)
    # would be the ones most likely to stay silent.
    if draft.get("political"):
        _alert_political(draft, primary_candidate, client=client, activity=activity)

    consolidation_result = consolidation_check(draft, supabase_client=client)
    if consolidation_result.action == "skip":
        return (False, False)

    confidence_adjustment, learning_flag = learning.apply_source_reputation(primary_candidate, reputation)
    if confidence_adjustment:
        draft["confidence"] = max(0.0, min(1.0, draft["confidence"] + confidence_adjustment))
    if learning_flag:
        item["learning_flag"] = learning_flag
        notes.append(f"{primary_candidate.url}: {learning_flag}")

    is_update = consolidation_result.action == "update"
    row = build_queue_row(
        item, draft, consolidation_result,
        is_update=is_update, date_missing=is_dateless,
        edmw_signal_count=edmw_signal_count,
        include_related_incidents=True, is_backfill=False,
    )

    # An unusually large merge is written INTACT (all its sources kept) but
    # marked, so ops/auto_publish holds it for a human until the grouper has
    # earned the right to merge at this size. See auto_publish.oversized_merge_trust.
    if oversized:
        row.setdefault("raw_content", {})["_oversized_cluster"] = oversized

    if not dry_run:
        inserted = client.table("war_room_queue").insert(row).execute()
        queue_id = inserted.data[0]["id"]
        if is_update and consolidation_result.related_incidents:
            write_incident_links(
                queue_id, consolidation_result.matched_incident_id,
                consolidation_result.related_incidents, client,
            )
        try:
            check_milestones(
                draft=draft, queue_id=queue_id, source_url=primary_candidate.url,
                incident_title=draft.get("title", ""), supabase_client=client,
            )
        except Exception as exc:                      # noqa: BLE001
            logger.warning(
                "run_ingestion_pass: herald check failed (non-fatal) for queue_id=%s: %s",
                queue_id, exc,
            )
    return (True, is_update)


def _mark_members(members, by_id, trackers, *, decided: bool) -> None:
    """
    Record one cluster's outcome on each member's OWN source watermark tracker.

    A cluster mixes sources, so a single write settles a candidate for CNA and one
    for Straits Times independently. Tolerates a member or source with no tracker
    (a caller that supplied its own `gathered` list) rather than raising —
    _write_clusters treats an exception from this region as a write failure and
    counts it toward the circuit breaker, which a bookkeeping miss must not do.
    """
    for candidate in members:
        tracker = trackers.get((by_id.get(id(candidate)) or {}).get("source"))
        if tracker is None:
            continue
        if decided:
            tracker.decided(candidate)
        else:
            tracker.unresolved(candidate)


def _write_group(members, by_id, res, *, client, dry_run, reputation, signal_summary,
                 notes, trackers, oversized: int = 0, activity=None):
    """
    Write ONE cluster (or a single-member group) as one queue row with all its
    non-signal sources. Updates `res` counters and settles every member's
    watermark.
    """
    from ingestion import clustering
    item_of = lambda c: by_id[id(c)]["item"]

    non_signal = [c for c in members if clustering.canonical_source_type(getattr(c, "source_type", "")) != "signal"]
    if non_signal:
        primary = min(non_signal, key=lambda c: item_of(c).get("date") or "9999-99-99")
    else:
        primary = members[0]
    primary_item = item_of(primary)
    is_dateless = not primary_item.get("date")

    stage2_input = clustering.build_cluster_stage2_input(members, item_of)
    edmw_signal_count = stage2_input.get("edmw_signal_count", 0)
    if signal_summary:
        stage2_input["learning_context"] = signal_summary

    queued, is_update = _emit(
        stage2_input, primary_item, is_dateless, edmw_signal_count, primary,
        client=client, dry_run=dry_run, reputation=reputation, notes=notes,
        oversized=oversized, activity=activity,
    )
    if queued:
        res["queued"] += 1
        res["update" if is_update else "new"] += 1
    else:
        res["skipped"] += 1

    # Settled either way, and for EVERY member — including signal members, which
    # the old code excluded. A written cluster is represented in the queue; a
    # consolidation skip means an equivalent report already is. Neither will change
    # its mind next pass, so both advance the watermark.
    #
    # This is the only thing that stops a re-fetch, because build_queue_row writes
    # just the PRIMARY member's URL to war_room_queue.source_url — every other
    # member is invisible to dedup until the incident is published. Signal members
    # are invisible to it permanently: guardrail #2 keeps their URL out of
    # source_urls by design, so excluding them from the advance meant a merged
    # Reddit/EDMW post was re-drafted every pass, and once its MSM siblings had
    # been deduped away it came back ALONE as an unverified signal-only row.
    _mark_members(members, by_id, trackers, decided=True)
    return queued


def _write_clusters(gathered, *, client, dry_run, reputation, signal_summary,
                    notes, activity, deadline_monotonic, circuit_breaker_n, trackers):
    """
    The 'on' write phase: cluster the gathered Stage-1-passed candidates, confirm
    merges with the Haiku judge, and write ONE row per cluster with all sources.
    An oversized cluster is written intact as one flagged row (see the loop).
    Returns a result dict the caller folds into the pass totals.

    Every gathered candidate arrives held as `unresolved` on its source's tracker,
    so anything this phase does not reach — a deadline, a tripped breaker, a write
    error — keeps its source's watermark below it and is retried next pass.
    """
    from ingestion import clustering
    cands = [g["candidate"] for g in gathered]
    by_id = {id(g["candidate"]): g for g in gathered}
    res = {"queued": 0, "new": 0, "update": 0, "skipped": 0, "clusters": 0,
           "aborted": False, "cstats": {}}

    # ONE batched grouping call for the whole pass (see _make_grouper). The
    # keyword pass survives only as the input filter that decides who is offered
    # to the grouper. clustering.CLUSTER_MAX_JUDGES is consequently no longer
    # read anywhere — there is no per-pair call count left to cap. It stays
    # defined (and env-settable) so an existing deployment config is not an error.
    grouper = _make_grouper()
    if grouper is not None:
        clusters, res["cstats"] = clustering.group_candidates(cands, grouper)
    else:
        clusters, res["cstats"] = clustering.cluster_candidates(cands), {"grouper": "unavailable"}
    res["clusters"] = len(clusters)

    consecutive: dict[str, int] = {}
    for ci, cluster in enumerate(clusters):
        if time.monotonic() >= deadline_monotonic:
            res["aborted"] = True
            # Hold the unwritten remainder. Per-CANDIDATE, not per-pass: a source
            # whose every candidate was written before the deadline still advances,
            # while one with a member left in the queue holds below that member's
            # date. The whole remainder is retried next pass.
            for unwritten in clusters[ci:]:
                _mark_members(unwritten, by_id, trackers, decided=False)
            _log(activity, "anomaly", "pass_deadline",
                 "deadline hit during cluster write phase — remaining clusters not written")
            break
        # An oversized cluster is written INTACT, as one row, and flagged.
        #
        # It used to be shredded into one row per member. Under pairwise judging
        # + union-find that was defensible: a group of 8 could be a transitive
        # blob no single decision ever saw whole. The batched grouper has no
        # union-find, so a group of 8 is one decision that saw all 8 — and the
        # shred was costing more than it saved. It burned N Sonnet drafts to
        # produce either one single-source row (when consolidation caught the
        # siblings) or several near-duplicates (when it didn't) — manufacturing
        # exactly the single-source incidents clustering exists to eliminate.
        # The live archive holds 7-, 9-, 10- and 12-source incidents; a cap of 6
        # would have shredded every one.
        #
        # The blast-radius concern is answered by holding the row for review
        # instead of destroying the merge — and that hold is temporary: see
        # ops/auto_publish.oversized_merge_trust, which lifts it once the grouper
        # has earned it and re-arms it on any rejection.
        oversized = len(cluster) if clustering.oversized(cluster) else 0
        if oversized:
            _log(activity, "warning", "cluster_oversized",
                 f"cluster of {oversized} exceeds cap — written as ONE row, "
                 f"held for review until the grouper has earned merges this large")
        try:
            _write_group(cluster, by_id, res, client=client, dry_run=dry_run,
                         reputation=reputation, signal_summary=signal_summary,
                         notes=notes, trackers=trackers, oversized=oversized,
                         activity=activity)
            consecutive.clear()
        except Exception as exc:                      # noqa: BLE001
            logger.warning("run_ingestion_pass: cluster write error: %s", exc)
            notes.append(f"cluster write error: {exc}")
            # Never judged on its merits — hold every member so the retry is real.
            _mark_members(cluster, by_id, trackers, decided=False)
            err = _classify_error(exc)
            if err:
                consecutive[err] = consecutive.get(err, 0) + 1
                if consecutive[err] >= circuit_breaker_n:
                    res["aborted"] = True
                    for unwritten in clusters[ci + 1:]:
                        _mark_members(unwritten, by_id, trackers, decided=False)
                    _log(activity, "anomaly", "circuit_breaker",
                         f"{consecutive[err]} consecutive {err} in cluster writes — aborting")
                    return res
    return res


def _record_health(source, *, items_found, items_passed_s1, result, errors,
                   client, dry_run) -> None:
    """
    One scraper_health row for a source this pass actually fetched.

    Written HERE, on the live path, because the table's previous writer
    (`scrapers.log_scraper_run`, called only from the retired `scrape_all`) lost
    its caller when ingestion moved to the source adapters — while
    `ops/supervisor.py` and the War Room health views kept reading it. See
    ingestion/health.py.

    Keyed on `source.name`, the same stable id as pipeline_state: the supervisor
    joins the two tables by that key, so a display name here would split one
    source across two identities and double-count it toward the email threshold.

    Never called for a source the pass SKIPPED — a row there would read as a
    genuine zero-item run and walk that source toward a false zero-streak.
    """
    if dry_run:
        return
    health.record(
        source.name,
        getattr(source, "source_type", None) or "msm",
        items_found=items_found,
        items_passed_s1=items_passed_s1,
        duration_ms=result.duration_ms,
        errors=errors,
        client=client,
    )


def _classify_error(exc: Exception) -> str | None:
    """
    Return a circuit-breaker error class for systemic API failures, or None
    for one-off / unclassified errors.

    Uses string matching only — no SDK imports — so it stays correct even if
    Gemini/Anthropic SDK exception hierarchies shift between minor versions.

    Note an RPD 429 never reaches here: it raises RpdExhaustedError, which is
    caught upstream and halts the pass outright rather than tripping a counter.
    """
    msg = str(exc).lower()
    if "rate limit" in msg or "rate_limit" in msg or "429" in msg:
        return "rate_limit_429"
    if "credit balance" in msg or "billing" in msg:
        return "anthropic_billing"
    return None


def run_ingestion_pass(
    sources: list[Source],
    now: datetime,
    *,
    dry_run: bool = False,
    max_duration_seconds: int = 1200,
    circuit_breaker_n: int = 5,
    activity=None,
) -> IngestionReport:
    started_at = now
    per_source: list[SourceResult] = []
    notes: list[str] = []
    new_count = 0
    update_count = 0
    phenomenon_count = 0
    total_queued = 0
    degraded = False
    # Safety state
    abort_pass: str | None = None        # set → break out of both loops
    consecutive: dict[str, int] = {}     # circuit breaker: error-class → consecutive count
    total_sleep_seconds: float = 0.0     # throttle visibility

    # The pass deadline is a DURATION, measured on the monotonic clock — not a
    # wall-clock timestamp derived from `now`.
    #
    # It used to be `started_at + timedelta(seconds=max_duration_seconds)`, where
    # `started_at = now` is the caller-supplied time, while every check compared
    # against `datetime.now(timezone.utc)`. Two different clocks. In production
    # they are the same instant so the bug is invisible, but any caller passing a
    # `now` that is not the real current time gets a deadline already in the past
    # and the pass aborts before fetching a single source — reporting an empty
    # `per_source` and advancing no watermarks, which reads exactly like "no news
    # today". `test_watermark_advance.py` pins `now` to the 14:58 SGT slot and so
    # was only valid during a ~20-minute real-time window each day.
    #
    # Monotonic also makes the budget immune to an NTP step or a DST jump
    # mid-pass, which a wall-clock deadline is not.
    deadline_monotonic = time.monotonic() + max_duration_seconds

    # Gather -> cluster -> write. Staged rollout (owner chose shadow-first):
    #   off    — current per-candidate path, byte-identical (default)
    #   shadow — current path UNCHANGED, but also collect the Stage-1-passed
    #            candidates and log what clustering WOULD group, so we can validate
    #            grouping on live data before it ever touches a write.
    #   on     — gather across all sources, cluster by story (each merge Haiku-
    #            confirmed), and write ONE queue row per cluster with all sources.
    cluster_mode = os.getenv("CLUSTER_BEFORE_WRITE", "off").strip().lower()
    if cluster_mode not in ("off", "shadow", "on"):
        cluster_mode = "off"
    shadow_cluster = cluster_mode == "shadow"
    passed_candidates: list = []         # Stage-1-passed, for shadow clustering only
    gathered: list = []                  # 'on' mode: deferred writes, one per cluster later
    on_fetched: list = []                # 'on' mode: sources fetched, watermarks persisted post-write
    # source -> WatermarkTracker. How far each source's watermark may advance is
    # decided by what the pipeline SETTLED, not by what it wrote — a Stage-1
    # rejection and a consolidation duplicate-skip are verdicts, and refusing to
    # advance past them re-bought the same Gemini + Haiku calls every daily pass.
    # See ingestion/watermark.py for the two holdbacks that keep that safe.
    trackers: dict[str, WatermarkTracker] = {}
    consolidation_skips = 0              # settled-but-unwritten: the cost this fix bounds

    try:
        client = get_supabase_client()
    except Exception as exc:
        logger.error("run_ingestion_pass: cannot create Supabase client: %s", exc)
        finished_at = datetime.now(timezone.utc)
        return IngestionReport(
            started_at=started_at, finished_at=finished_at, dry_run=dry_run,
            per_source=per_source, total_queued=0,
            new_count=0, update_count=0, phenomenon_count=0,
            degraded=True, infra_error=str(exc), notes=notes,
        )

    try:
        daily_budget = load_daily_budget()
        budget_halted = daily_budget.should_halt()
        if budget_halted:
            notes.append("Stage 1 daily request budget already exhausted (SGT) — Stage 1/2 skipped for this pass.")

        reputation = learning.load_source_reputation(client)
        signal_summary = learning.load_recent_signal_patterns(client)

        seen_urls: set[str] = set()

        for source in sources:
            if abort_pass:
                break

            if not source.enabled:
                continue

            # Safety: deadline check BEFORE the fetch, not only inside the
            # candidate loop. Without this a pass could not abort between
            # sources, so an over-running pass kept starting new fetches (each
            # of which can itself sleep for a minute-plus) long after its budget
            # was gone. Remaining sources are recorded as unprocessed rather
            # than silently dropped.
            if time.monotonic() >= deadline_monotonic:
                abort_pass = (
                    f"aborted before '{source.name}': max_duration_seconds="
                    f"{max_duration_seconds} reached"
                )
                degraded = True
                _log(activity, "anomaly", "pass_deadline",
                     f"pass deadline hit before fetching {source.name}", source.name)
                break

            if budget_halted:
                # Honest accounting (§7) — this source was not processed at
                # all this pass. Watermark is left untouched (no
                # state_store.update call), so it's retried in full next run.
                # No scraper_health row either: nothing was fetched, and a
                # zero-item row would be indistinguishable from a real one.
                per_source.append(SourceResult(
                    name=source.name, status="unavailable",
                    fetched=0, fresh=0, novel=0, queued=0,
                    reason="stage 1 daily request budget exhausted",
                ))
                degraded = True
                continue

            watermark = state_store.get(source.name, client=client)

            # Don't spend the retry backoff if there isn't time left to use the
            # result anyway.
            seconds_left = deadline_monotonic - time.monotonic()
            backoff = fallback.BACKOFF_SECONDS if seconds_left > fallback.BACKOFF_SECONDS * 2 else 0

            candidates, result = fallback.run_with_fallback(
                source.name, lambda: source.fetch(since=watermark),
                backoff_seconds=backoff,
            )

            if candidates is None:
                # Blocked/unavailable: record result, mark pass degraded,
                # leave watermark UNCHANGED (§6 invariant).
                per_source.append(result)
                degraded = True
                # Req #8 — a block is never silent. These are the rows the
                # supervisor and maintenance agents read to decide whether a
                # source is having a bad day or has been dead for a week: an
                # agent_events row, and a scraper_health row with status='error'
                # (the red dot in the War Room health view).
                _log(activity,
                     "anomaly" if result.status == "blocked" else "warning",
                     f"source_{result.status}",
                     f"{source.name}: {result.reason or result.status}",
                     source.name, reason=result.reason, status=result.status)
                _record_health(source, items_found=0, items_passed_s1=0,
                               result=result,
                               errors=[result.reason or result.status],
                               client=client, dry_run=dry_run)
                if not dry_run:
                    state_store.update(source.name, watermark, result.status, result.reason, client=client)
                continue

            fresh, dropped_count, dateless = recency.RecencyFilter(candidates, watermark, now)
            result.fresh = len(fresh) + len(dateless)
            if dropped_count:
                notes.append(f"{source.name}: {dropped_count} candidate(s) <= watermark, dropped.")

            tracker = WatermarkTracker(source.name, watermark, pass_date=now.date())
            trackers[source.name] = tracker
            novel_count = 0
            queued_count = 0
            passed_s1_count = 0     # scraper_health.items_passed_s1

            # Materialised so an early break can hand the untouched remainder to
            # the tracker. Without that, a mid-loop halt (Stage 1 budget, deadline,
            # tripped breaker) let the candidates it already settled advance the
            # watermark straight over the ones it never looked at.
            todo = fresh + dateless
            for idx, candidate in enumerate(todo):
                is_dateless = candidate.published_at is None

                # ── Safety: max-duration timeout ─────────────────────────────
                # Checked before dedup, not after. A pass where every candidate
                # is a duplicate `continue`d past the old check entirely and so
                # could never abort — it just made DB round-trips until the
                # source list ran out, which is the exact shape of an
                # accidentally-unbounded pass.
                if time.monotonic() >= deadline_monotonic:
                    tracker.unresolved_all(todo[idx:])
                    abort_pass = (
                        f"aborted: max_duration_seconds={max_duration_seconds} reached "
                        f"— source '{source.name}' watermark not advanced"
                    )
                    degraded = True
                    _log(activity, "anomaly", "pass_deadline",
                         f"pass deadline hit mid-source {source.name}", source.name)
                    break

                # ── seen_urls + dedup (§5.2, in-memory before DB) ────────────
                try:
                    is_dup = dedup.is_duplicate(candidate, client, seen_urls)
                except dedup.InfraError as exc:
                    # Abort the WHOLE pass as DEGRADED — do not continue
                    # treating remaining candidates as novel (§5.2 review S1).
                    logger.error("run_ingestion_pass: dedup infra failure, aborting pass: %s", exc)
                    finished_at = datetime.now(timezone.utc)
                    return IngestionReport(
                        started_at=started_at, finished_at=finished_at, dry_run=dry_run,
                        per_source=per_source, total_queued=total_queued,
                        new_count=new_count, update_count=update_count, phenomenon_count=phenomenon_count,
                        degraded=True, infra_error=str(exc), notes=notes,
                    )

                if is_dup:
                    # Settled: the URL is already in war_room_queue / incidents (or
                    # was handled earlier in this pass), so it stays visible to
                    # dedup. Advancing saves re-fetching and re-checking it.
                    tracker.decided(candidate)
                    continue
                # Canonical form, matching what dedup.is_duplicate looks up —
                # storing the raw URL here let the same article back in under a
                # different tracking parameter.
                seen_urls.add(canonical_url(candidate.url))
                novel_count += 1

                # ── Safety: max-duration timeout ─────────────────────────────
                if time.monotonic() >= deadline_monotonic:
                    tracker.unresolved_all(todo[idx:])
                    abort_pass = (
                        f"aborted: max_duration_seconds={max_duration_seconds} reached "
                        f"— source '{source.name}' watermark not advanced"
                    )
                    degraded = True
                    break

                if budget_halted:
                    # These were never offered to Stage 1. Holding them is what
                    # stops the candidates processed BEFORE the halt from advancing
                    # the watermark over an unexamined remainder — a real data loss
                    # whenever a source lists newest-first, which most RSS does.
                    tracker.unresolved_all(todo[idx:])
                    notes.append(f"{source.name}: remaining candidates skipped — Stage 1 daily request budget exhausted mid-pass.")
                    break

                try:
                    item = _candidate_to_item(candidate)

                    # ── Stage 1 (budget-guarded) ──────────────────────────────
                    s1 = filter_content(item)
                    usage = s1.get("usage") or {}
                    daily_budget.record(
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )
                    budget_halted = daily_budget.should_halt()
                    total_sleep_seconds += s1.get("rate_limiter_sleep_seconds", 0.0)

                    if not s1["passes"]:
                        # Settled, and the highest-volume case by far — Stage 1
                        # rejects 60-70% of raw scrape volume and none of it is
                        # written anywhere, so dedup can never see it. Re-asking
                        # Gemini the same question about the same article every day
                        # was the largest share of the bleed this fix closes.
                        tracker.decided(candidate)
                        consecutive.clear()
                        continue

                    # Closes the long-standing "items_passed_s1 always 0"
                    # backlog item (TechSpec known issues): the count exists per
                    # source here, and never did inside the old scrape_all,
                    # which ran before Stage 1 and had no way to learn it.
                    passed_s1_count += 1

                    # Shadow clustering observes what passes Stage 1; it never
                    # alters the write path below (still one draft per candidate).
                    if shadow_cluster:
                        passed_candidates.append(candidate)

                    # ── 'on' mode: defer the write to the clustering phase ────
                    # Gather the Stage-1-passed candidate and move on; Stage 2 +
                    # consolidation + write happen once per STORY CLUSTER after
                    # all sources are in, so one draft is written per story with
                    # all its sources (not one single-source draft per outlet).
                    if cluster_mode == "on":
                        gathered.append({
                            "candidate": candidate, "item": item,
                            "is_dateless": is_dateless, "source": source.name,
                        })
                        # Not settled yet — the cluster-write phase decides. Held
                        # unresolved until then so a sibling this source DID settle
                        # cannot advance the watermark past a candidate still
                        # waiting to be written.
                        tracker.unresolved(candidate)
                        consecutive.clear()
                        continue

                    # ── Per-candidate write (off / shadow) ────────────────────
                    # Legal guardrail #2: a signal (EDMW/Reddit) URL is NEVER a
                    # quoted source — is_signal_source accepts both vocabularies
                    # and falls back to a domain lookup, so the forum URL is kept
                    # out of source_urls and only edmw_signal_count carries it.
                    is_edmw = is_signal_source(candidate.source_type, candidate.url)
                    edmw_signal_count = 1 if is_edmw else 0
                    stage2_input = {
                        **item,
                        "source_urls": [] if is_edmw else [candidate.url],
                        "edmw_signal_count": edmw_signal_count,
                    }
                    if signal_summary:
                        stage2_input["learning_context"] = signal_summary

                    queued, is_update = _emit(
                        stage2_input, item, is_dateless, edmw_signal_count, candidate,
                        client=client, dry_run=dry_run, reputation=reputation, notes=notes,
                        activity=activity,
                    )
                    if not queued:
                        # Consolidation judged this a duplicate of a row already
                        # awaiting review. That is a VERDICT, not a failure — the
                        # story is represented, and re-running the pass would only
                        # buy the same two Haiku drafts and the same judgement
                        # again. Nothing was written, so dedup cannot see it next
                        # pass; the watermark is the only thing that can.
                        tracker.decided(candidate)
                        consolidation_skips += 1
                        consecutive.clear()
                        continue

                    queued_count += 1
                    total_queued += 1
                    if is_update:
                        update_count += 1
                    else:
                        new_count += 1

                    tracker.decided(candidate)
                    consecutive.clear()   # clean completion → reset circuit breaker

                except Stage1HaltError as exc:
                    # Non-retryable: daily quota gone (resets midnight US/Pacific)
                    # or a billing block. Either way, retrying every remaining
                    # candidate just hammers the same wall. Halt the pass.
                    #
                    # "Non-retryable" means not retryable NOW — the quota resets, so
                    # this candidate and everything behind it must survive to the
                    # next pass. abort_pass already suppresses this source's
                    # watermark write; holding them keeps that true independently.
                    tracker.unresolved_all(todo[idx:])
                    daily_budget.mark_rpd_exhausted()
                    budget_halted = True
                    abort_pass = str(exc)
                    degraded = True
                    reason = ("Stage 1 daily quota exhausted (RPD 429)"
                              if isinstance(exc, RpdExhaustedError)
                              else "Stage 1 blocked by billing — operator action needed")
                    logger.warning("run_ingestion_pass: %s", exc)
                    notes.append(f"{source.name}: remaining candidates skipped — {reason}.")
                    _log(activity, "anomaly", "stage1_halt", f"{reason} — pass halted",
                         source.name, detail_reason=str(exc))
                    break

                except Exception as exc:
                    logger.warning("run_ingestion_pass: error processing %s (%s): %s", candidate.url, source.name, exc)
                    notes.append(f"{source.name}: error processing {candidate.url}: {exc}")
                    # Never judged on its merits — a model or DB error says nothing
                    # about the article. Holding it also pins the watermark below
                    # its date, so the siblings this source DID settle cannot
                    # quietly carry the watermark past it.
                    tracker.unresolved(candidate)
                    # ── Safety: circuit breaker ───────────────────────────────
                    err_class = _classify_error(exc)
                    if err_class:
                        consecutive[err_class] = consecutive.get(err_class, 0) + 1
                        if consecutive[err_class] >= circuit_breaker_n:
                            tracker.unresolved_all(todo[idx:])
                            abort_pass = (
                                f"circuit breaker: {consecutive[err_class]} consecutive "
                                f"{err_class} failures — aborting pass"
                            )
                            degraded = True
                            logger.error("run_ingestion_pass: %s", abort_pass)
                            _log(activity, "anomaly", "circuit_breaker",
                                 f"{consecutive[err_class]} consecutive {err_class} failures — pass aborted",
                                 source.name, error_class=err_class)
                            break
                    continue

            result.novel = novel_count
            result.queued = queued_count
            per_source.append(result)
            _log(activity, "success", "source_ok",
                 f"{source.name}: fetched={result.fetched} fresh={result.fresh} "
                 f"novel={novel_count} queued={queued_count}",
                 source.name, fetched=result.fetched, queued=queued_count)

            # Outside the `not abort_pass` guard below: the fetch happened and
            # Stage 1 ran, so the run is real health data even if the pass then
            # hit its deadline. items_passed_s1 is honestly partial in that case.
            _record_health(source, items_found=result.fetched,
                           items_passed_s1=passed_s1_count, result=result,
                           errors=None, client=client, dry_run=dry_run)

            if not dry_run and not abort_pass:
                if cluster_mode == "on":
                    # Defer: this source's gathered candidates are still unresolved
                    # until the cluster-write phase settles them.
                    on_fetched.append(source.name)
                else:
                    state_store.update(source.name, tracker.value(), "ok", client=client)
                    hold = tracker.hold_note()
                    if hold:
                        notes.append(hold)

        if abort_pass:
            notes.append(f"Pass aborted: {abort_pass}")

        # ── 'on' mode: cluster the gathered candidates and write per cluster ─
        if cluster_mode == "on" and gathered and not abort_pass:
            cres = _write_clusters(
                gathered, client=client, dry_run=dry_run, reputation=reputation,
                signal_summary=signal_summary, notes=notes, activity=activity,
                deadline_monotonic=deadline_monotonic, circuit_breaker_n=circuit_breaker_n,
                trackers=trackers,
            )
            total_queued += cres["queued"]
            new_count += cres["new"]
            update_count += cres["update"]
            consolidation_skips += cres["skipped"]
            if cres["aborted"]:
                degraded = True
            # Keys match group_candidates' stats dict (clustering.py) — the
            # old edges_confirmed/edges_judged keys died with pairwise judging
            # and always rendered "?/?".
            notes.append(
                f"[cluster-write] {len(gathered)} candidate(s) -> {cres['clusters']} cluster(s); "
                f"wrote {cres['queued']} row(s) "
                f"(merges {cres['cstats'].get('merges', '?')}, "
                f"grouper errors {cres['cstats'].get('grouper_errors', '?')})."
            )
            _log(activity, "success", "cluster_write",
                 f"{cres['queued']} row(s) from {cres['clusters']} cluster(s) of {len(gathered)} candidate(s)")

        # Persist 'on'-mode watermarks. Outside the block above on purpose: a pass
        # where nothing cleared Stage 1 has no clusters to write but has still
        # SETTLED every candidate it looked at, and skipping the write there was
        # the same bug in its purest form — the emptiest passes advanced nothing at
        # all. Each tracker already holds itself below anything the write phase
        # never reached, so an abort needs no special case here. Every fetched
        # source is marked 'ok' so the supervisor doesn't read it as stale.
        if cluster_mode == "on" and not dry_run:
            for src_name in on_fetched:
                tracker = trackers[src_name]
                state_store.update(src_name, tracker.value(), "ok", client=client)
                hold = tracker.hold_note()
                if hold:
                    notes.append(hold)

        # ── Shadow clustering: log what gather->cluster->write WOULD do ──────
        # Pure analysis of the candidates that passed Stage 1 this pass; writes
        # nothing, so it is safe to run in production to validate grouping before
        # the 'on' path is wired. Wrapped: an analysis error must not fail a pass.
        if shadow_cluster and passed_candidates:
            try:
                from ingestion import clustering
                # Use the SAME batched grouper the 'on' path uses, so shadow logs
                # the decisions that would actually be written — not the loose
                # keyword pre-clusters (which over-merge). Costs one Haiku call.
                grouper = _make_grouper()
                if grouper is not None:
                    clusters, cstats = clustering.group_candidates(passed_candidates, grouper)
                else:
                    clusters, cstats = clustering.cluster_candidates(passed_candidates), {}
                stats = clustering.summarize(clusters)
                stats.update(cstats)
                notes.append(
                    f"[shadow-cluster] {stats['candidates']} candidate(s) -> "
                    f"{stats['clusters']} cluster(s); {stats['multi_member_clusters']} multi-member, "
                    f"~{stats['sonnet_drafts_saved_estimate']} Sonnet draft(s) would be saved "
                    f"(grouper: {cstats.get('pool_size', '?')} offered / "
                    f"{cstats.get('merges', '?')} merged / "
                    f"{cstats.get('grouper_errors', '?')} error(s))."
                )
                _log(activity, "info", "shadow_cluster", str(stats))
                for cl in clusters:
                    if len(cl) > 1:
                        titles = " | ".join((getattr(c, "title", "") or "")[:50] for c in cl)
                        _log(activity, "info", "shadow_cluster_group",
                             f"WOULD MERGE {len(cl)}: {titles}")
            except Exception as exc:                  # noqa: BLE001
                logger.warning("run_ingestion_pass: shadow clustering failed (non-fatal): %s", exc)

        if consolidation_skips:
            notes.append(
                f"{consolidation_skips} candidate(s) dropped by consolidation as duplicates of "
                f"rows already awaiting review — each cost a Stage 2 draft this pass. "
                + ("Watermarks were not advanced (dry run), so a real pass would pay again."
                   if dry_run else
                   "The watermark has advanced past them, so none is re-drafted next pass.")
            )

        if total_sleep_seconds > 0:
            notes.append(
                f"Rate limiter slept {total_sleep_seconds:.1f}s total across Stage 1 calls "
                f"(Gemini free-tier {RPM_LIMIT} RPM)."
            )

        if not dry_run:
            save_daily_budget(daily_budget)

    except Exception as exc:
        logger.error("run_ingestion_pass: unexpected failure: %s", exc)
        finished_at = datetime.now(timezone.utc)
        return IngestionReport(
            started_at=started_at, finished_at=finished_at, dry_run=dry_run,
            per_source=per_source, total_queued=total_queued,
            new_count=new_count, update_count=update_count, phenomenon_count=phenomenon_count,
            degraded=True, infra_error=str(exc), notes=notes,
        )

    finished_at = datetime.now(timezone.utc)
    report = IngestionReport(
        started_at=started_at,
        finished_at=finished_at,
        dry_run=dry_run,
        per_source=per_source,
        total_queued=total_queued,
        new_count=new_count,
        update_count=update_count,
        phenomenon_count=phenomenon_count,
        degraded=degraded,
        infra_error=None,
        notes=notes,
    )

    if not dry_run:
        state_store.record_run(report, client=client)

    return report


def _candidate_to_item(candidate: Candidate) -> dict:
    """
    Candidate -> dict at the consolidation/build_queue_row boundary (§5.4,
    review S3 fix). `published_at` is a `date` object — not JSON-safe for
    raw_content — so it's replaced with a `date` string key
    ("YYYY-MM-DD", or "" if dateless), the convention build_queue_row's
    date_missing/_date_fallback handling expects.
    """
    item = asdict(candidate)
    published_at = item.pop("published_at")
    item["date"] = published_at.isoformat() if published_at else ""
    return item
