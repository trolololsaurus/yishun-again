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
  - action='skip' is handled defensively but is currently unreachable —
    consolidation/check.py's implementation never returns it. Exact-URL
    duplicates are caught earlier by dedup.is_duplicate.
  - signal_summary (learning.load_recent_signal_patterns) is loaded once
    per pass and threaded into the Stage 2 input dict as
    "learning_context", but filters/stage2_writer.py's
    _build_user_message() does not currently read that key — it has no
    injection hook. The load-once wiring is in place per spec; consuming
    it requires a small follow-up edit to stage2_writer.py.
  - edmw_signal_count is derived as 1 if the candidate's source_type is
    'edmw' else 0 — no EDMW source adapter exists yet in
    ingestion/sources/, so this is currently always 0 in practice.
"""

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from classifiers.corroboration import get_supabase_client
from consolidation.check import check as consolidation_check, write_incident_links
from consolidation.queue_row import build_queue_row
from filters.stage1_filter import filter_content
from filters.stage2_writer import write_stage2
from ingestion import dedup, fallback, learning, recency, state_store
from ingestion.budget import load_daily_budget, save_daily_budget
from ingestion.contracts import Candidate, IngestionReport, Source, SourceResult
from orchestrator.herald_agent import check_milestones

logger = logging.getLogger(__name__)


def _classify_error(exc: Exception) -> str | None:
    """
    Return a circuit-breaker error class for systemic API failures, or None
    for one-off / unclassified errors.

    Uses string matching only — no SDK imports — so it stays correct even if
    Groq/Anthropic SDK exception hierarchies shift between minor versions.
    """
    msg = str(exc).lower()
    if "rate limit" in msg or "rate_limit" in msg or "429" in msg:
        return "groq_429"
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
            notes.append("Groq daily budget already exhausted (SGT) — Stage 1/2 skipped for this pass.")

        reputation = learning.load_source_reputation(client)
        signal_summary = learning.load_recent_signal_patterns(client)

        seen_urls: set[str] = set()

        for source in sources:
            if abort_pass:
                break

            if not source.enabled:
                continue

            if budget_halted:
                # Honest accounting (§7) — this source was not processed at
                # all this pass. Watermark is left untouched (no
                # state_store.update call), so it's retried in full next run.
                per_source.append(SourceResult(
                    name=source.name, status="unavailable",
                    fetched=0, fresh=0, novel=0, queued=0,
                    reason="groq budget exhausted",
                ))
                degraded = True
                continue

            watermark = state_store.get(source.name, client=client)

            candidates, result = fallback.run_with_fallback(
                source.name, lambda: source.fetch(since=watermark),
            )

            if candidates is None:
                # Blocked/unavailable: record result, mark pass degraded,
                # leave watermark UNCHANGED (§6 invariant).
                per_source.append(result)
                degraded = True
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

                # ── seen_urls + dedup FIRST (§5.2, in-memory before DB) ──────
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
                    notes.append(f"{source.name}: remaining candidates skipped — Groq daily budget exhausted mid-pass.")
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

                    # ── Stage 2 ────────────────────────────────────────────
                    # Legal guardrail #2: an EDMW/signal URL is NEVER a quoted
                    # source. EDMW candidates contribute only edmw_signal_count;
                    # source_urls must stay empty until an MSM URL is attached.
                    is_edmw = candidate.source_type == "edmw"
                    edmw_signal_count = 1 if is_edmw else 0
                    stage2_input = {
                        **item,
                        "source_urls": [] if is_edmw else [candidate.url],
                        "edmw_signal_count": edmw_signal_count,
                    }
                    if signal_summary:
                        stage2_input["learning_context"] = signal_summary

                    draft = write_stage2(stage2_input)

                    # ── Consolidation (§5.4) ──────────────────────────────────
                    consolidation_result = consolidation_check(draft, supabase_client=client)

                    if consolidation_result.action == "skip":
                        # Unreachable in the current consolidation/check.py
                        # implementation (no code path returns 'skip'), kept
                        # for forward-compat / mirrors backfill_agent.py.
                        continue

                    # ── Learning nudge (confidence-only; hard gates below are
                    #    immune — the forward pipeline never auto-publishes,
                    #    every row lands in war_room_queue for human review) ──
                    confidence_adjustment, learning_flag = learning.apply_source_reputation(candidate, reputation)
                    if confidence_adjustment:
                        draft["confidence"] = max(0.0, min(1.0, draft["confidence"] + confidence_adjustment))
                    if learning_flag:
                        item["learning_flag"] = learning_flag
                        notes.append(f"{candidate.url}: {learning_flag}")

                    is_update = consolidation_result.action == "update"
                    row = build_queue_row(
                        item,
                        draft,
                        consolidation_result,
                        is_update=is_update,
                        date_missing=is_dateless,
                        edmw_signal_count=edmw_signal_count,
                        include_related_incidents=True,
                        is_backfill=False,   # QA H4 — live ingestion, not backfill
                    )

                    if not dry_run:
                        inserted = client.table("war_room_queue").insert(row).execute()
                        queue_id = inserted.data[0]["id"]

                        if is_update and consolidation_result.related_incidents:
                            write_incident_links(
                                queue_id,
                                consolidation_result.matched_incident_id,
                                consolidation_result.related_incidents,
                                client,
                            )

                        try:
                            check_milestones(
                                draft=draft,
                                queue_id=queue_id,
                                source_url=candidate.url,
                                incident_title=draft.get("title", ""),
                                supabase_client=client,
                            )
                        except Exception as exc:
                            logger.warning(
                                "run_ingestion_pass: herald check failed (non-fatal) for queue_id=%s: %s",
                                queue_id, exc,
                            )

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
                            break
                    continue

            result.novel = novel_count
            result.queued = queued_count
            per_source.append(result)

            if not dry_run and not abort_pass:
                state_store.update(source.name, max_published_at, "ok", client=client)

        if abort_pass:
            notes.append(f"Pass aborted: {abort_pass}")

        if total_sleep_seconds > 0:
            notes.append(
                f"Rate limiter slept {total_sleep_seconds:.1f}s total across Stage 1 calls "
                f"(Groq 6k TPM free-tier)."
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
