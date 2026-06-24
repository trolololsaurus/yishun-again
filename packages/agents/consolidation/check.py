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

load_dotenv(override=False)

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"


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
    user_msg = (
        f"NEW CANDIDATE:\n"
        f"Title: {candidate.get('title', '')}\n"
        f"Summary: {candidate.get('summary', candidate.get('content', ''))[:600]}\n"
        f"Date: {candidate.get('incident_date', candidate.get('date', 'unknown'))}\n"
        f"URL: {candidate.get('url', '')}\n\n"
        f"EXISTING PUBLISHED INCIDENT:\n"
        f"ID: {existing['id']}\n"
        f"Title: {existing['title']}\n"
        f"Summary: {existing['summary'][:600]}\n"
        f"Date: {existing['incident_date']}\n"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        temperature=0.0,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    return _parse_json(response.content[0].text)


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

    # ── Keyword pre-filter (tag each comparison row with its kind) ────────────
    candidate_text = f"{candidate.get('title', '')} {candidate.get('summary', candidate.get('content', ''))}"
    candidate_kw   = extract_keywords(candidate_text)

    candidates_to_judge: list[tuple[str, dict]] = []
    for kind, pool in (("published", published), ("queue", queued)):
        for rec in pool:
            rec_text = f"{rec['title']} {rec['summary']}"
            if keyword_overlap(candidate_kw, extract_keywords(rec_text)) >= MIN_KEYWORD_OVERLAP:
                candidates_to_judge.append((kind, rec))

    if not candidates_to_judge:
        logger.debug("Consolidation: no keyword overlap with any published/queued item — new")
        return ConsolidationResult(action="new", matched_incident_id=None)

    # ── Claude judgement loop ─────────────────────────────────────────────────
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

    for kind, rec in candidates_to_judge:
        try:
            judgement = _judge_pair(claude, candidate, rec)
        except Exception as exc:
            logger.warning("Consolidation: judge_pair failed for %s: %s", rec["id"], exc)
            continue

        same_conf = float(judgement.get("same_incident_confidence", 0.0))

        if judgement.get("same_incident") and same_conf > best_match_confidence:
            best_match_confidence = same_conf
            best_kind             = kind
            best_match            = rec
            best_match_result     = judgement

        # Related-but-distinct links only make sense against published incidents
        # (incident_links joins two published rows). Skip for queue comparisons.
        if kind == "published" and not judgement.get("same_incident") and judgement.get("related"):
            rel_conf  = float(judgement.get("related_confidence", 0.0))
            link_type = judgement.get("link_type") or "related"
            if link_type not in ("related", "follow_up", "same_location"):
                link_type = "related"
            if rel_conf >= RELATED_LINK_THRESHOLD:
                related_links.append(RelatedLink(
                    incident_id = rec["id"],
                    confidence  = rel_conf,
                    reason      = judgement.get("related_reason", ""),
                    link_type   = link_type,
                ))

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
