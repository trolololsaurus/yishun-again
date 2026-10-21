"""
Consolidation agent (spec v1.5 §4.5).

Runs BEFORE a new candidate is inserted into war_room_queue.
Decides whether the candidate is:
  - A new incident             → action='new',    queue status='pending'
  - An update to an existing   → action='update', queue status='update'
  - A skip (exact duplicate)   → action='skip'

Public API
----------
check(candidate, client=None) -> ConsolidationResult

Moved here from the former classifiers/consolidation.py module (INGESTION_DESIGN.md
§10b step 2). The unused backward-compatibility shim was removed after checking
that no in-repository caller imports it.
"""

import json
import logging
import os
import re
from dataclasses import dataclass

import anthropic
from dotenv import load_dotenv

from filters.model_call import create_with_headroom, extract_text

from consolidation.rules import (
    CANDIDATE_FETCH_LIMIT,
    MIN_KEYWORD_OVERLAP,
    QUEUE_FETCH_LIMIT,
    UPDATE_MATCH_THRESHOLD,
    extract_keywords,
    keyword_overlap,
)

# Pairwise-only judgment limits were removed with the batch judge. The separate
# integrity scan still has its own per-run call budget in ops/integrity.py.

load_dotenv(override=False)

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"

# Output cap, env-overridable. The batch judge uses one response for the whole
# eligible comparison pool; filters/model_call guards the configured limit.
BATCH_MAX_TOKENS = int(os.getenv("CONSOLIDATION_BATCH_MAX_TOKENS", "1024"))


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass
class ConsolidationResult:
    action: str                          # 'new' | 'update' | 'skip'
    matched_incident_id: str | None      # set when action='update'
    queue_status: str = "pending"        # 'pending' | 'update'
    match_confidence: float = 0.0        # confidence of the update match, if any
    match_reason: str = ""
    agent_role_proposed: str = "initial" # 'initial' | 'update' | 'follow_up' | 'skip'
    matched_queue_id: str | None = None   # set when action='skip' for queued duplicate


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set")
    return anthropic.Anthropic(api_key=api_key)


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError(f"No JSON in consolidation response: {text[:300]!r}")
        return json.loads(match.group())


# ── Claude call ──────────────────────────────────────────────────────────────

# ── Batched judgement (one call per candidate, not one per pair) ─────────────

# Per-record summary budget inside the batch prompt. Shorter than the pairwise
# 600 because the batch carries many records; the opening sentences are what
# identify the entity, act and location that decide a match.
BATCH_RECORD_CHARS = 400

_BATCH_SYSTEM_PROMPT = """\
You are a deduplication agent for Yishun Again, \
a satirical Yishun incident archive.

You are given ONE new candidate incident and a numbered list of EXISTING archive
records. Decide which existing record, if any, describes the SAME event as the
candidate.

1. same event: the same entity performing the same core act.
   - Entity = the specific person / animal / object involved
   - Act = the specific thing that happened (stabbing, theft, fire, etc.)
   - A later report about the SAME act (update, charge, trial, sentencing) is
     still the same event.
   - Different acts by the same entity on different days are NOT the same event.
   - The same kind of act at a different block or street, or on a different day,
     is NOT the same event.

Return JSON only (no markdown fences):
{
  "match_index": integer index of the same-event record, or null,
  "match_confidence": float 0.0-1.0,
  "match_reason": string (1-2 sentences)
}

At most ONE match_index. If no record is the same event, set it to null.
Never emit an index that is not in the list.
"""


def _judge_batch(
    client: anthropic.Anthropic,
    candidate: dict,
    records: list[dict],
) -> dict:
    """
    One Haiku call: which of these N archive records, if any, is the same event?

    Replaces the per-pair fan-out. Besides the call-count saving, the model can
    contrast the whole shortlist at once — under pairwise judging each record was
    scored in isolation, so two near-identical archive rows could both come back
    confident and only the ranking order decided which one won.
    """
    cand_date = candidate.get("incident_date") or candidate.get("date") or "unknown"
    lines = []
    for i, rec in enumerate(records):
        lines.append(
            f"[{i}] date={rec.get('incident_date') or 'unknown'}\n"
            f"    title: {rec.get('title', '')}\n"
            f"    summary: {(rec.get('summary') or '')[:BATCH_RECORD_CHARS]}"
        )
    user_msg = (
        f"NEW CANDIDATE:\n"
        f"Title: {candidate.get('title', '')}\n"
        f"Summary: {candidate.get('summary', candidate.get('content', ''))[:600]}\n"
        f"Date: {cand_date}\n"
        f"URL: {candidate.get('url', '')}\n\n"
        f"EXISTING ARCHIVE RECORDS ({len(records)}):\n" + "\n\n".join(lines)
    )

    response, _retried = create_with_headroom(
        client,
        call="consolidation._judge_batch",
        env_var="CONSOLIDATION_BATCH_MAX_TOKENS",
        model=MODEL,
        max_tokens=BATCH_MAX_TOKENS,
        system=_BATCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    return _parse_json(extract_text(response))


def _coerce_index(value, n: int):
    """An in-range int index, or None. bool is rejected (it is an int subclass)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value < n else None


def _coerce_conf(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


# ── Published incident fetcher ───────────────────────────────────────────────

def _fetch_recent_published(supabase_client) -> list[dict]:
    """Fetch the most recent published incidents for comparison."""
    result = (
        supabase_client.table("incidents")
        .select("id, title, summary, incident_date, slug")
        .eq("is_published", True)
        .order("published_at", desc=True)
        .limit(CANDIDATE_FETCH_LIMIT)
        .execute()
    )
    return result.data or []


def _fetch_recent_queue(supabase_client) -> list[dict]:
    """
    Fetch recent UNPROCESSED war_room_queue items, normalised to the same shape
    `_judge_batch` expects for an existing record (id, title, summary,
    incident_date). These are siblings still awaiting operator review — a
    same-event match against one of them means the candidate is a duplicate
    that should be SKIPPED, not added as a second pending row.

    Only items not yet acted on are considered (processed_at IS NULL). Already
    approved items have become published incidents and are covered by
    _fetch_recent_published; already rejected items are intentionally ignored.
    """
    result = (
        supabase_client.table("war_room_queue")
        .select("id, proposed_title, proposed_summary, raw_content, created_at")
        .is_("processed_at", "null")
        .order("created_at", desc=True)
        .limit(QUEUE_FETCH_LIMIT)
        .execute()
    )
    out: list[dict] = []
    for row in result.data or []:
        rc = row.get("raw_content") or {}
        out.append({
            "id":            row["id"],
            "title":         row.get("proposed_title") or (rc.get("title") if isinstance(rc, dict) else "") or "",
            # QA M13: fall back to raw_content.summary when proposed_summary is empty
            # (older rows), so queue-dedup isn't silently reduced to title-only matching.
            "summary":       row.get("proposed_summary") or (rc.get("summary") if isinstance(rc, dict) else "") or "",
            "incident_date": (rc.get("date") if isinstance(rc, dict) else "") or "",
        })
    return out


# ── Public API ───────────────────────────────────────────────────────────────

def check(candidate: dict, supabase_client=None) -> ConsolidationResult:
    """
    Run consolidation check on a Stage-2-approved candidate.

    Args:
        candidate: The Stage 2 draft dict. Expected keys:
                   title, summary (or content), url,
                   incident_date (ISO date string, optional).
        supabase_client: Pre-built admin Supabase client. If None, one is
                         created (requires SUPABASE_URL + SUPABASE_SECRET_KEY).

    Returns:
        ConsolidationResult describing what action pipeline.py should take.
    """
    if supabase_client is None:
        from classifiers.corroboration import get_supabase_client
        try:
            supabase_client = get_supabase_client()
        except EnvironmentError as exc:
            logger.warning("Consolidation: Supabase not configured — treating as new: %s", exc)
            return ConsolidationResult(action="new", matched_incident_id=None)

    # ── Fetch comparison pool: published incidents + unprocessed queue ───────
    # Published matches → UPDATE the existing incident. Queue matches → SKIP
    # (an equivalent report is already awaiting review; don't add a second row).
    try:
        published = _fetch_recent_published(supabase_client)
    except Exception as exc:
        logger.warning("Consolidation: failed to fetch published incidents — treating as new: %s", exc)
        return ConsolidationResult(action="new", matched_incident_id=None)

    try:
        queued = _fetch_recent_queue(supabase_client)
    except Exception as exc:
        # Queue comparison is a best-effort second net; a fetch failure here
        # must not block the (working) published-comparison path.
        logger.warning("Consolidation: failed to fetch queue items — queue-dedup skipped: %s", exc)
        queued = []

    if not published and not queued:
        return ConsolidationResult(action="new", matched_incident_id=None)

    # ── Keyword pre-filter, ranked ───────────────────────────────────────────
    # Score every eligible record by keyword overlap. The ranking is retained
    # because it puts the likeliest match first in the prompt, but it no longer
    # gates a call count: every eligible record goes into ONE batched judgement
    # (see _judge_batch), so the long tail that the old cap discarded is now
    # judged too rather than silently dropped. sort() is stable, so equal-overlap
    # ties keep the published-before-queue, recency-desc order the pools
    # arrived in.
    candidate_text = f"{candidate.get('title', '')} {candidate.get('summary', candidate.get('content', ''))}"
    candidate_kw   = extract_keywords(candidate_text)

    scored: list[tuple[int, str, dict]] = []
    for kind, pool in (("published", published), ("queue", queued)):
        for rec in pool:
            rec_text = f"{rec['title']} {rec['summary']}"
            overlap = keyword_overlap(candidate_kw, extract_keywords(rec_text))
            if overlap >= MIN_KEYWORD_OVERLAP:
                scored.append((overlap, kind, rec))

    if not scored:
        logger.debug("Consolidation: no keyword overlap with any published/queued item — new")
        return ConsolidationResult(action="new", matched_incident_id=None)

    scored.sort(key=lambda t: t[0], reverse=True)
    candidates_to_judge = [(kind, rec) for _, kind, rec in scored]

    # ── One batched Claude judgement ─────────────────────────────────────────
    try:
        claude = _get_anthropic_client()
    except EnvironmentError as exc:
        logger.warning("Consolidation: Anthropic not configured — treating as new: %s", exc)
        return ConsolidationResult(action="new", matched_incident_id=None)

    best_kind: str | None         = None
    best_match: dict | None       = None
    best_match_reason = ""
    best_match_confidence: float   = 0.0

    records = [rec for _, rec in candidates_to_judge]
    try:
        verdict = _judge_batch(claude, candidate, records)
    except Exception as exc:
        # A failed batch loses the whole comparison for this candidate, where a
        # failed pair used to lose one. Treating it as NEW keeps the old
        # fail-open direction (a duplicate row an operator can merge, never a
        # silently dropped story); ops/integrity.py re-scans for duplicates.
        logger.warning(
            "Consolidation: batch judge failed for '%s' over %d record(s) — treating as new: %s",
            candidate.get("title", "")[:50], len(records), exc,
        )
        return ConsolidationResult(action="new", matched_incident_id=None)

    match_index = _coerce_index(verdict.get("match_index"), len(records))
    if match_index is None and verdict.get("match_index") is not None:
        logger.warning(
            "Consolidation: batch judge returned unusable match_index %r for %d record(s) "
            "— treating as no match", verdict.get("match_index"), len(records),
        )
    if match_index is not None:
        best_kind, best_match = candidates_to_judge[match_index]
        best_match_confidence = _coerce_conf(verdict.get("match_confidence"))
        best_match_reason = verdict.get("match_reason", "") or ""

    logger.debug(
        "Consolidation: 1 batched Haiku judgement over %d record(s) for '%s'",
        len(records), candidate.get("title", "")[:50],
    )

    # ── Decision ─────────────────────────────────────────────────────────────
    if best_match and best_match_confidence >= UPDATE_MATCH_THRESHOLD:
        if best_kind == "queue":
            # An equivalent report is already in the queue awaiting review —
            # drop this duplicate rather than minting a second pending row.
            logger.info(
                "Consolidation: SKIP — '%s' (conf=%.2f) duplicates queued item %s",
                candidate.get("title", "")[:60], best_match_confidence, best_match["id"],
            )
            return ConsolidationResult(
                action               = "skip",
                matched_incident_id  = None,
                match_confidence     = best_match_confidence,
                match_reason         = best_match_reason,
                agent_role_proposed  = "skip",
                matched_queue_id     = best_match["id"],
            )

        logger.info(
            "Consolidation: UPDATE match — '%s' (conf=%.2f) → incident %s",
            candidate.get("title", "")[:60], best_match_confidence, best_match["id"],
        )
        return ConsolidationResult(
            action               = "update",
            matched_incident_id  = best_match["id"],
            queue_status         = "update",
            match_confidence     = best_match_confidence,
            match_reason         = best_match_reason,
            agent_role_proposed  = "update",
        )

    if best_match and best_match_confidence > 0:
        # A below-threshold match remains a normal pending candidate. It is not
        # surfaced as a proposed relationship or written to incident_links.
        logger.info(
            "Consolidation: weak match (conf=%.2f) with %s — queuing as pending",
            best_match_confidence, best_match["id"],
        )

    logger.debug("Consolidation: NEW incident — no related-link suggestions generated")
    return ConsolidationResult(
        action               = "new",
        matched_incident_id  = None,
        queue_status         = "pending",
        agent_role_proposed  = "initial",
    )
