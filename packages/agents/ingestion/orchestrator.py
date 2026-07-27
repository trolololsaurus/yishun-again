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
budget persistence.

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
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from classifiers.corroboration import get_supabase_client
from classifiers.source_allowlist import is_signal_source
from consolidation.check import check as consolidation_check, write_incident_links
from consolidation.queue_row import build_queue_row
from filters.stage1_filter import filter_content
from filters.stage1_quota import RPM_LIMIT, RpdExhaustedError, Stage1HaltError
from filters.stage2_writer import write_stage2
from ingestion import dedup, fallback, learning, recency, state_store
from ingestion.budget import load_daily_budget, save_daily_budget
from ingestion.contracts import Candidate, IngestionReport, Source, SourceResult
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


def _make_merge_judge():
    """
    A judge(a, b) -> bool for clustering — 'are these two candidates the same
    event?' — wrapping the SAME Haiku pair judge consolidation already uses, so
    grouping and archive-dedup apply one consistent standard. Returns None if
    Anthropic isn't configured (clustering then degrades to keyword-only). Each
    call never raises out of `judge` (clustering treats a raise as "not same").
    """
    try:
        from consolidation.check import _get_anthropic_client, _judge_pair
        from consolidation.rules import UPDATE_MATCH_THRESHOLD
        client = _get_anthropic_client()
    except Exception as exc:                          # noqa: BLE001
        logger.warning("clustering: no merge judge (%s) — keyword-only grouping", exc)
        return None

    def judge(a, b) -> bool:
        ca = {"title": getattr(a, "title", ""), "summary": getattr(a, "content", ""),
              "url": getattr(a, "url", ""),
              "incident_date": a.published_at.isoformat() if getattr(a, "published_at", None) else ""}
        ex = {"id": "cluster-peer", "title": getattr(b, "title", ""),
              "summary": getattr(b, "content", ""),
              "incident_date": b.published_at.isoformat() if getattr(b, "published_at", None) else ""}
        j = _judge_pair(client, ca, ex)
        return bool(j.get("same_incident")) and \
            float(j.get("same_incident_confidence", 0.0)) >= UPDATE_MATCH_THRESHOLD

    return judge


def _emit(stage2_input, item, is_dateless, edmw_signal_count, primary_candidate,
          *, client, dry_run, reputation, notes):
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


def _write_group(members, by_id, res, *, client, dry_run, reputation, signal_summary, notes):
    """
    Write ONE cluster (or a single-member group) as one queue row with all its
    non-signal sources. Updates `res` counters and the per-source watermark map.
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
    )
    if queued:
        res["queued"] += 1
        res["update" if is_update else "new"] += 1
        for c in non_signal:
            d = getattr(c, "published_at", None)
            if d:
                src = by_id[id(c)]["source"]
                cur = res["per_source_max"].get(src)
                if cur is None or d > cur:
                    res["per_source_max"][src] = d
    return queued


def _write_clusters(gathered, *, client, dry_run, reputation, signal_summary,
                    notes, activity, deadline, circuit_breaker_n):
    """
    The 'on' write phase: cluster the gathered Stage-1-passed candidates, confirm
    merges with the Haiku judge, and write ONE row per cluster with all sources.
    Oversized clusters are written member-by-member (never auto-merge a blob).
    Returns a result dict the caller folds into the pass totals.
    """
    from ingestion import clustering
    cands = [g["candidate"] for g in gathered]
    by_id = {id(g["candidate"]): g for g in gathered}
    res = {"queued": 0, "new": 0, "update": 0, "clusters": 0, "aborted": False,
           "per_source_max": {}, "cstats": {}}

    judge = _make_merge_judge()
    if judge is not None:
        clusters, res["cstats"] = clustering.cluster_with_confirmation(cands, judge)
    else:
        clusters, res["cstats"] = clustering.cluster_candidates(cands), {"judge": "unavailable"}
    res["clusters"] = len(clusters)

    consecutive: dict[str, int] = {}
    for cluster in clusters:
        if datetime.now(timezone.utc) >= deadline:
            res["aborted"] = True
            _log(activity, "anomaly", "pass_deadline",
                 "deadline hit during cluster write phase — remaining clusters not written")
            break
        groups = [[m] for m in cluster] if clustering.oversized(cluster) else [cluster]
        if clustering.oversized(cluster):
            _log(activity, "warning", "cluster_oversized",
                 f"cluster of {len(cluster)} exceeds cap — writing members individually")
        for grp in groups:
            try:
                _write_group(grp, by_id, res, client=client, dry_run=dry_run,
                             reputation=reputation, signal_summary=signal_summary, notes=notes)
                consecutive.clear()
            except Exception as exc:                  # noqa: BLE001
                logger.warning("run_ingestion_pass: cluster write error: %s", exc)
                notes.append(f"cluster write error: {exc}")
                err = _classify_error(exc)
                if err:
                    consecutive[err] = consecutive.get(err, 0) + 1
                    if consecutive[err] >= circuit_breaker_n:
                        res["aborted"] = True
                        _log(activity, "anomaly", "circuit_breaker",
                             f"{consecutive[err]} consecutive {err} in cluster writes — aborting")
                        return res
    return res


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
    deadline = started_at + timedelta(seconds=max_duration_seconds)

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
    on_fetched: dict = {}                # 'on' mode: source -> original watermark, advanced post-write

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
            if datetime.now(timezone.utc) >= deadline:
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
            seconds_left = (deadline - datetime.now(timezone.utc)).total_seconds()
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
                # Req #8 — a block is never silent. This is the row the
                # supervisor and maintenance agents read to decide whether a
                # source is having a bad day or has been dead for a week.
                _log(activity,
                     "anomaly" if result.status == "blocked" else "warning",
                     f"source_{result.status}",
                     f"{source.name}: {result.reason or result.status}",
                     source.name, reason=result.reason, status=result.status)
                if not dry_run:
                    state_store.update(source.name, watermark, result.status, result.reason, client=client)
                continue

            fresh, dropped_count, dateless = recency.RecencyFilter(candidates, watermark, now)
            result.fresh = len(fresh) + len(dateless)
            if dropped_count:
                notes.append(f"{source.name}: {dropped_count} candidate(s) <= watermark, dropped.")

            max_published_at = watermark
            novel_count = 0
            queued_count = 0

            for candidate in fresh + dateless:
                is_dateless = candidate.published_at is None

                # ── Safety: max-duration timeout ─────────────────────────────
                # Checked before dedup, not after. A pass where every candidate
                # is a duplicate `continue`d past the old check entirely and so
                # could never abort — it just made DB round-trips until the
                # source list ran out, which is the exact shape of an
                # accidentally-unbounded pass.
                if datetime.now(timezone.utc) >= deadline:
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
                    continue
                seen_urls.add(candidate.url)
                novel_count += 1

                # ── Safety: max-duration timeout ─────────────────────────────
                if datetime.now(timezone.utc) >= deadline:
                    abort_pass = (
                        f"aborted: max_duration_seconds={max_duration_seconds} reached "
                        f"— source '{source.name}' watermark not advanced"
                    )
                    degraded = True
                    break

                if budget_halted:
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
                        consecutive.clear()
                        continue

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
                    )
                    if not queued:
                        continue   # consolidation returned skip — same as the old inline `continue`

                    queued_count += 1
                    total_queued += 1
                    if is_update:
                        update_count += 1
                    else:
                        new_count += 1

                    if not is_dateless:
                        if max_published_at is None or candidate.published_at > max_published_at:
                            max_published_at = candidate.published_at

                    consecutive.clear()   # clean completion → reset circuit breaker

                except Stage1HaltError as exc:
                    # Non-retryable: daily quota gone (resets midnight US/Pacific)
                    # or a billing block. Either way, retrying every remaining
                    # candidate just hammers the same wall. Halt the pass.
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
                    # ── Safety: circuit breaker ───────────────────────────────
                    err_class = _classify_error(exc)
                    if err_class:
                        consecutive[err_class] = consecutive.get(err_class, 0) + 1
                        if consecutive[err_class] >= circuit_breaker_n:
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

            if not dry_run and not abort_pass:
                if cluster_mode == "on":
                    # Defer: the cluster-write phase advances watermarks only for
                    # sources whose candidates were actually written.
                    on_fetched[source.name] = watermark
                else:
                    state_store.update(source.name, max_published_at, "ok", client=client)

        if abort_pass:
            notes.append(f"Pass aborted: {abort_pass}")

        # ── 'on' mode: cluster the gathered candidates and write per cluster ─
        if cluster_mode == "on" and gathered and not abort_pass:
            cres = _write_clusters(
                gathered, client=client, dry_run=dry_run, reputation=reputation,
                signal_summary=signal_summary, notes=notes, activity=activity,
                deadline=deadline, circuit_breaker_n=circuit_breaker_n,
            )
            total_queued += cres["queued"]
            new_count += cres["new"]
            update_count += cres["update"]
            if cres["aborted"]:
                degraded = True
            notes.append(
                f"[cluster-write] {len(gathered)} candidate(s) -> {cres['clusters']} cluster(s); "
                f"wrote {cres['queued']} row(s) "
                f"(edges {cres['cstats'].get('edges_confirmed', '?')} confirmed / "
                f"{cres['cstats'].get('edges_judged', '?')} judged)."
            )
            _log(activity, "success", "cluster_write",
                 f"{cres['queued']} row(s) from {cres['clusters']} cluster(s) of {len(gathered)} candidate(s)")
            # Advance watermarks. If the write phase completed, advance each
            # fetched source to the max date of ITS written candidates (unchanged
            # if it wrote none). If it aborted mid-write, advance NOTHING past the
            # original watermark — the whole set retries next pass, and dedup
            # catches anything already written. Either way every fetched source is
            # marked 'ok' so the supervisor doesn't read it as stale.
            if not dry_run:
                for src_name, orig_wm in on_fetched.items():
                    wm = cres["per_source_max"].get(src_name, orig_wm) if not cres["aborted"] else orig_wm
                    state_store.update(src_name, wm, "ok", client=client)

        # ── Shadow clustering: log what gather->cluster->write WOULD do ──────
        # Pure analysis of the candidates that passed Stage 1 this pass; writes
        # nothing, so it is safe to run in production to validate grouping before
        # the 'on' path is wired. Wrapped: an analysis error must not fail a pass.
        if shadow_cluster and passed_candidates:
            try:
                from ingestion import clustering
                # Use the CONFIRMED path (each merge LLM-gated) so the shadow logs
                # the REAL grouping decisions, not the loose keyword pre-clusters
                # (which over-merge — the reason confirmation exists). Bounded by
                # CLUSTER_MAX_JUDGES, so shadow costs at most that many Haiku calls.
                judge = _make_merge_judge()
                if judge is not None:
                    clusters, cstats = clustering.cluster_with_confirmation(passed_candidates, judge)
                else:
                    clusters, cstats = clustering.cluster_candidates(passed_candidates), {}
                stats = clustering.summarize(clusters)
                stats.update(cstats)
                notes.append(
                    f"[shadow-cluster] {stats['candidates']} candidate(s) -> "
                    f"{stats['clusters']} cluster(s); {stats['multi_member_clusters']} multi-member, "
                    f"~{stats['sonnet_drafts_saved_estimate']} Sonnet draft(s) would be saved "
                    f"(edges: {cstats.get('edges_confirmed', '?')} confirmed / "
                    f"{cstats.get('edges_judged', '?')} judged)."
                )
                _log(activity, "info", "shadow_cluster", str(stats))
                for cl in clusters:
                    if len(cl) > 1:
                        titles = " | ".join((getattr(c, "title", "") or "")[:50] for c in cl)
                        _log(activity, "info", "shadow_cluster_group",
                             f"WOULD MERGE {len(cl)}: {titles}")
            except Exception as exc:                  # noqa: BLE001
                logger.warning("run_ingestion_pass: shadow clustering failed (non-fatal): %s", exc)

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
