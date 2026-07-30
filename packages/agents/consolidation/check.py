"""
Consolidation agent (spec v1.5 §4.5).

Runs BEFORE a new candidate is inserted into war_room_queue.
Decides whether the candidate is:
  - A new incident             → action='new',    queue status='pending'
  - An update to an existing   → action='update', queue status='update'
  - A skip (exact duplicate)   → action='skip'

Also identifies related-but-distinct incidents and writes incident_links rows
for the operator to confirm or dismiss.

Public API
----------
check(candidate, client=None) -> ConsolidationResult

Moved here unchanged from classifiers/consolidation.py (INGESTION_DESIGN.md
§10b step 2) — classifiers/consolidation.py now re-exports from this module
for backward compatibility.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv

from filters.model_call import create_with_headroom

from consolidation.rules import (
    CANDIDATE_FETCH_LIMIT,
    MIN_KEYWORD_OVERLAP,
    QUEUE_FETCH_LIMIT,
    RELATED_LINK_THRESHOLD,
    UPDATE_MATCH_THRESHOLD,
    WEAK_MATCH_THRESHOLD,
    extract_keywords,
    keyword_overlap,
)

# MAX_JUDGEMENTS_PER_CANDIDATE and EARLY_EXIT_CONFIDENCE are deliberately no
# longer imported. Both existed to bound a per-candidate fan-out of pairwise
# Haiku calls; the comparison now costs exactly ONE call regardless of pool size,
# so there is no call count to cap and nothing to exit early from. They stay
# defined in consolidation/rules.py so an existing env override is not an error.

load_dotenv(override=False)

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

# Output caps, env-overridable — raising one is the whole fix for a truncation,
# and it should not need a redeploy. Measured against the full live pool (53
# records) the batch judge used 128/1024 tokens, so both carry large headroom;
# filters/model_call guards them regardless.
PAIR_MAX_TOKENS = int(os.getenv("CONSOLIDATION_PAIR_MAX_TOKENS", "400"))
BATCH_MAX_TOKENS = int(os.getenv("CONSOLIDATION_BATCH_MAX_TOKENS", "1024"))


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass
class RelatedLink:
    incident_id: str
    confidence: float
    reason: str
    link_type: str = "related"   # 'related' | 'follow_up' | 'same_location'


@dataclass
class ConsolidationResult:
    action: str                          # 'new' | 'update' | 'skip'
    matched_incident_id: str | None      # set when action='update'
    related_incidents: list[RelatedLink] = field(default_factory=list)
    queue_status: str = "pending"        # 'pending' | 'update'
    match_confidence: float = 0.0        # confidence of the update match, if any
    match_reason: str = ""
    agent_role_proposed: str = "initial" # 'initial' | 'update' | 'follow_up' | 'skip'


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

_SYSTEM_PROMPT = """\
You are a deduplication and incident-linking agent for Yishun Again, \
a satirical Yishun incident archive.

Given a NEW candidate incident and an EXISTING published incident, determine:

1. same_incident: Are they about the same entity performing the same core act?
   - Entity = specific person / animal / object involved
   - Act = the specific thing that happened (stabbing, theft, fire, etc.)
   - Date proximity matters: if the new report is clearly a follow-up story
     about the SAME act (update, trial, sentencing) → still same_incident=true
   - Different acts by the same entity on different days → same_incident=false

2. If same_incident=false, check related:
   - related: Different entities/acts but clearly connected (e.g., same block,
     same day, related chain of events)
   - follow_up: Later development of a broader situation
   - same_location: Same block or street, different incident — only flag if
     it would genuinely be editorially useful to a reader browsing the archive

Return JSON only (no markdown fences):
{
  "same_incident": boolean,
  "same_incident_confidence": float (0.0–1.0),
  "same_incident_reason": string (1-2 sentences),
  "related": boolean,
  "related_confidence": float (0.0–1.0),
  "related_reason": string (1-2 sentences),
  "link_type": "related" | "follow_up" | "same_location" | null
}
"""


def _judge_pair(
    client: anthropic.Anthropic,
    candidate: dict,
    existing: dict,
) -> dict:
    """Ask Claude Haiku whether candidate and existing are the same incident."""
    # `or`-chained rather than dict.get defaults: a key present but empty/None
    # (a dateless candidate) must read 'unknown', not "" — otherwise the judge
    # sees a blank date and can't tell it apart from a real one.
    cand_date = candidate.get("incident_date") or candidate.get("date") or "unknown"
    user_msg = (
        f"NEW CANDIDATE:\n"
        f"Title: {candidate.get('title', '')}\n"
        f"Summary: {candidate.get('summary', candidate.get('content', ''))[:600]}\n"
        f"Date: {cand_date}\n"
        f"URL: {candidate.get('url', '')}\n\n"
        f"EXISTING PUBLISHED INCIDENT:\n"
        f"ID: {existing['id']}\n"
        f"Title: {existing['title']}\n"
        f"Summary: {existing['summary'][:600]}\n"
        f"Date: {existing['incident_date']}\n"
    )

    response, _retried = create_with_headroom(
        client,
        call="consolidation._judge_pair",
        env_var="CONSOLIDATION_PAIR_MAX_TOKENS",
        model=MODEL,
        max_tokens=PAIR_MAX_TOKENS,
        temperature=0.0,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    return _parse_json(response.content[0].text)


# ── Batched judgement (one call per candidate, not one per pair) ─────────────

# Per-record summary budget inside the batch prompt. Shorter than the pairwise
# 600 because the batch carries many records; the opening sentences are what
# identify the entity, act and location that decide a match.
BATCH_RECORD_CHARS = 400

_BATCH_SYSTEM_PROMPT = """\
You are a deduplication and incident-linking agent for Yishun Again, \
a satirical Yishun incident archive.

You are given ONE new candidate incident and a numbered list of EXISTING archive
records. Decide which existing record, if any, describes the SAME event as the
candidate, and which others are related but distinct.

1. same event: the same entity performing the same core act.
   - Entity = the specific person / animal / object involved
   - Act = the specific thing that happened (stabbing, theft, fire, etc.)
   - A later report about the SAME act (update, charge, trial, sentencing) is
     still the same event.
   - Different acts by the same entity on different days are NOT the same event.
   - The same kind of act at a different block or street, or on a different day,
     is NOT the same event.

2. related (only for records that are NOT the same event):
   - related: different entities/acts but clearly connected
   - follow_up: a later development of a broader situation
   - same_location: same block or street, different incident — only when it
     would genuinely be useful to a reader browsing the archive

Return JSON only (no markdown fences):
{
  "match_index": integer index of the same-event record, or null,
  "match_confidence": float 0.0-1.0,
  "match_reason": string (1-2 sentences),
  "related": [
    {"index": integer, "confidence": float 0.0-1.0, "reason": string,
     "link_type": "related" | "follow_up" | "same_location"}
  ]
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
        temperature=0.0,
        system=_BATCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    return _parse_json(response.content[0].text)


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
    `_judge_pair` expects for an existing record (id, title, summary,
    incident_date). These are siblings still awaiting operator review — a
    same_incident match against one of them means the candidate is a duplicate
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
    best_match_result: dict | None = None
    best_match_confidence: float   = 0.0
    related_links: list[RelatedLink] = []

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
        best_match_result     = {"same_incident_reason": verdict.get("match_reason", "")}

    # Related-but-distinct links only make sense against published incidents
    # (incident_links joins two published rows). Skip queue comparisons, and skip
    # the matched record itself — it is an update target, not a sibling link.
    raw_related = verdict.get("related")
    for rel in raw_related if isinstance(raw_related, list) else []:
        if not isinstance(rel, dict):
            continue
        idx = _coerce_index(rel.get("index"), len(records))
        if idx is None or idx == match_index:
            continue
        kind, rec = candidates_to_judge[idx]
        if kind != "published":
            continue
        rel_conf  = _coerce_conf(rel.get("confidence"))
        link_type = rel.get("link_type") or "related"
        if link_type not in ("related", "follow_up", "same_location"):
            link_type = "related"
        if rel_conf >= RELATED_LINK_THRESHOLD:
            related_links.append(RelatedLink(
                incident_id = rec["id"],
                confidence  = rel_conf,
                reason      = rel.get("reason", "") or "",
                link_type   = link_type,
            ))

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
                match_reason         = (best_match_result or {}).get("same_incident_reason", ""),
                agent_role_proposed  = "skip",
            )

        logger.info(
            "Consolidation: UPDATE match — '%s' (conf=%.2f) → incident %s",
            candidate.get("title", "")[:60], best_match_confidence, best_match["id"],
        )
        return ConsolidationResult(
            action               = "update",
            matched_incident_id  = best_match["id"],
            related_incidents    = related_links,
            queue_status         = "update",
            match_confidence     = best_match_confidence,
            match_reason         = (best_match_result or {}).get("same_incident_reason", ""),
            agent_role_proposed  = "update",
        )

    if best_match and best_kind == "published" and best_match_confidence >= WEAK_MATCH_THRESHOLD:
        # Possible match but below update threshold — queue as normal pending
        # but still surface the possible link as a related row (low confidence).
        # Only for published matches: incident_links joins two published rows,
        # so a weak queue match can't be expressed as a link and is left as new.
        logger.info(
            "Consolidation: weak match (conf=%.2f) with %s — queuing as pending",
            best_match_confidence, best_match["id"],
        )
        related_links.append(RelatedLink(
            incident_id = best_match["id"],
            confidence  = best_match_confidence,
            reason      = (best_match_result or {}).get("same_incident_reason", "Possible duplicate — below update threshold"),
            link_type   = "related",
        ))

    logger.debug(
        "Consolidation: NEW incident — %d related link(s) found",
        len(related_links),
    )
    # 'follow_up' when there are related published incidents; 'initial' otherwise
    role = "follow_up" if related_links else "initial"
    return ConsolidationResult(
        action               = "new",
        matched_incident_id  = None,
        related_incidents    = related_links,
        queue_status         = "pending",
        agent_role_proposed  = role,
    )


def write_incident_links(
    queue_id: str,
    published_incident_id: str,
    links: list[RelatedLink],
    supabase_client,
) -> None:
    """
    Insert incident_links rows for confirmed published incident ↔ related links.
    queue_id is unused here (links join two published incidents); it is passed
    for logging. Silently skips rows that violate the UNIQUE constraint.
    """
    for link in links:
        try:
            supabase_client.table("incident_links").insert({
                "incident_a":   published_incident_id,
                "incident_b":   link.incident_id,
                "link_type":    link.link_type,
                "confidence":   link.confidence,
                "agent_reason": link.reason,
            }).execute()
            logger.debug(
                "incident_links: %s ↔ %s (%s conf=%.2f)",
                published_incident_id, link.incident_id, link.link_type, link.confidence,
            )
        except Exception as exc:
            # UNIQUE violation is expected if the pipeline runs twice; log and continue.
            logger.debug("incident_links insert skipped for %s: %s", link.incident_id, exc)
