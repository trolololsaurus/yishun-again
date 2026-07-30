"""
Phase-1 contextual-learning read-back (LEARNING_LOOP.md §2.2, §2.3).

PHASE 1 ONLY. This module reads accumulated operator-decision signal
(source_reputation, training_signals) and returns data for the orchestrator
to inject into prompts/scoring for the FROZEN Stage 1/2 models. It writes
NOTHING — no weights, no model files, no prompt templates. Phases 2
(graduated autonomy) and 3 (LoRA fine-tuning) are documented in
LEARNING_LOOP.md but NOT built here.

Read-only against Supabase (classifiers.corroboration.get_supabase_client).
Depends only on ingestion/contracts.py + corroboration.
"""

import logging
from collections import Counter
from urllib.parse import urlparse

from classifiers.corroboration import get_supabase_client
from ingestion.contracts import Candidate

logger = logging.getLogger(__name__)

# source_reputation.trust_score default (migration 006)
DEFAULT_TRUST_SCORE = 0.500

# Phase-1 confidence steering (LEARNING_LOOP.md §2.3 step 1) — a nudge, not a
# gate. Crime/named-individual review requirements are untouched by this.
TRUST_BOOST_THRESHOLD = 0.700   # trust_score >= this -> ease the bar
TRUST_FLAG_THRESHOLD = 0.300    # trust_score <= this -> deprioritise/flag
CONFIDENCE_ADJUSTMENT = 0.10    # max nudge applied to agent_confidence

# Token-bounding for the Stage 2 prompt summary (§2.3 step 2)
MAX_SIGNAL_ROWS = 50             # most-recent training_signals rows read
MAX_PATTERNS_PER_CATEGORY = 5    # distinct patterns surfaced per category

# Few-shot example bounds. The block is injected into BOTH the Haiku classify
# call and the write call, so it is kept deliberately small.
MAX_EXAMPLES = 8
EXAMPLE_TITLE_CHARS = 110
MAX_EXAMPLES_CHARS = 1400        # hard ceiling on the rendered block


def load_source_reputation(client=None) -> dict[str, float]:
    """
    source_reputation -> {source_domain: trust_score} (LEARNING_LOOP.md §2.2).

    Called once at the start of run_ingestion_pass(); the returned map is
    passed to apply_source_reputation() per candidate.
    """
    if client is None:
        client = get_supabase_client()

    result = (
        client.table("source_reputation")
        .select("source_domain, trust_score")
        .execute()
    )
    return {row["source_domain"]: float(row["trust_score"]) for row in (result.data or [])}


def _resolve_titles(client, rows: list[dict]) -> dict[str, str]:
    """
    training_signals -> the title of the item the operator judged.

    Two paths, because the table is populated by two writers and neither fills
    both keys: `queue_id` -> war_room_queue.proposed_title (set by the War Room
    review routes) and `incident_id` -> incidents.title (set once a row has been
    published). Measured on live data: queue_id resolves 35/35 where present,
    incident_id covers 137/172 rows. Batched — two SELECTs, not one per row.

    Returns {row_id: title}. Never raises; an unresolvable row is simply omitted
    and its example is skipped rather than rendered titleless.
    """
    out: dict[str, str] = {}
    qids = list({r["queue_id"] for r in rows if r.get("queue_id")})
    iids = list({r["incident_id"] for r in rows if r.get("incident_id")})

    q_titles: dict[str, str] = {}
    i_titles: dict[str, str] = {}
    if qids:
        try:
            res = client.table("war_room_queue").select("id,proposed_title").in_("id", qids).execute()
            q_titles = {x["id"]: (x.get("proposed_title") or "") for x in (res.data or [])}
        except Exception as exc:                  # noqa: BLE001
            logger.warning("learning: queue title lookup failed: %s", exc)
    if iids:
        try:
            res = client.table("incidents").select("id,title").in_("id", iids).execute()
            i_titles = {x["id"]: (x.get("title") or "") for x in (res.data or [])}
        except Exception as exc:                  # noqa: BLE001
            logger.warning("learning: incident title lookup failed: %s", exc)

    for r in rows:
        t = q_titles.get(r.get("queue_id") or "") or i_titles.get(r.get("incident_id") or "")
        if t:
            out[r["id"]] = t.strip()
    return out


def load_recent_signal_patterns(client=None, limit: int = MAX_SIGNAL_ROWS) -> str:
    """
    Concrete few-shot examples of recent operator decisions, for injection into
    the Stage 2 prompts (LEARNING_LOOP.md §2.3 step 2).

    Returns "" if there is no signal yet (cold start) — callers treat an empty
    string as "nothing to inject", not an error.

    ## Why examples and not counts

    This used to emit aggregate statistics: "Operators re-classified 3 item(s)
    from 'clown' to 'dagger'." That is unusable by a frozen model. It says a
    correction happened but not WHICH KIND of story was corrected, so there is
    nothing to pattern-match against — the model cannot tell whether the next
    story in front of it is one of the three or not. A labelled example carries
    the thing the statistic threw away: the story.

    Bounded by MAX_EXAMPLES, EXAMPLE_TITLE_CHARS and a hard MAX_EXAMPLES_CHARS
    ceiling, because this block goes into the classify call AND the write call.

    Examples are round-robined across reject reasons rather than taken in recency
    order: 10 of the 30 live rejections are 'duplicate', and eight duplicate
    examples would teach one lesson eight times instead of teaching eight.
    """
    if client is None:
        client = get_supabase_client()

    # edited_classification is what the War Room approve route actually writes
    # on an edit_approve (apps/war-room .../approve/route.ts). The previous
    # column name here, corrected_classification, is written by NOTHING — so
    # Category 1 below could never fire and only reject examples ever reached
    # the prompt.
    result = (
        client.table("training_signals")
        .select("id, decision, reject_reason, proposed_classification, "
                "edited_classification, queue_id, incident_id, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return ""

    titles = _resolve_titles(client, rows)

    # ── Category 1: the operator changed the classification ─────────────────
    reclass: list[str] = []
    for r in rows:
        p, c = r.get("proposed_classification"), r.get("edited_classification")
        title = titles.get(r["id"])
        if p and c and p != c and title:
            reclass.append(
                f'- "{title[:EXAMPLE_TITLE_CHARS]}"\n'
                f"  agent said {p} -> operator corrected to {c}"
            )

    # ── Category 2: the operator rejected it, with a reason ─────────────────
    by_reason: dict[str, list[str]] = {}
    for r in rows:
        title = titles.get(r["id"])
        if r.get("decision") == "reject" and r.get("reject_reason") and title:
            by_reason.setdefault(r["reject_reason"], []).append(
                f'- "{title[:EXAMPLE_TITLE_CHARS]}"\n'
                f"  operator REJECTED as '{r['reject_reason']}'"
            )

    rejects: list[str] = []
    while by_reason and len(rejects) < MAX_EXAMPLES:
        for reason in list(by_reason):
            if by_reason[reason]:
                rejects.append(by_reason[reason].pop(0))
            if not by_reason[reason]:
                del by_reason[reason]
            if len(rejects) >= MAX_EXAMPLES:
                break

    # Reclassifications first — a correction is a sharper lesson than a rejection.
    examples = (reclass + rejects)[:MAX_EXAMPLES]
    if not examples:
        return ""

    # _build_user_message already prefixes "Recent operator patterns (advisory,
    # do not override your judgment)", so this header adds only the framing that
    # prefix lacks — these are worked examples, not rules.
    header = "Worked examples of where the operator drew the line:"
    block = "\n".join([header] + examples)
    if len(block) > MAX_EXAMPLES_CHARS:
        block = block[:MAX_EXAMPLES_CHARS].rsplit("\n", 1)[0]
    return block


def apply_source_reputation(
    candidate: Candidate,
    reputation: dict[str, float],
) -> tuple[float, str | None]:
    """
    Pure function (LEARNING_LOOP.md §2.3 step 1).

    Candidate carries no confidence score (that's Stage 1/2's job) — this
    returns a (confidence_adjustment, flag) pair for the orchestrator to
    apply to whatever confidence Stage 1/2 produces for this candidate:

    - High-trust domain (trust_score >= TRUST_BOOST_THRESHOLD):
        +CONFIDENCE_ADJUSTMENT, flag=None — easier to clear the review bar.
    - Repeatedly-rejected domain (trust_score <= TRUST_FLAG_THRESHOLD):
        -CONFIDENCE_ADJUSTMENT, flag='low_reputation_source' — deprioritised
        and flagged for the operator, never silently dropped.
    - Otherwise: (0.0, None) — no adjustment.

    Unknown domains (not yet in source_reputation) get DEFAULT_TRUST_SCORE
    (0.500, migration 006's default), which falls in the neutral band.
    """
    domain = _domain(candidate.url)
    trust_score = reputation.get(domain, DEFAULT_TRUST_SCORE)

    if trust_score >= TRUST_BOOST_THRESHOLD:
        return CONFIDENCE_ADJUSTMENT, None
    if trust_score <= TRUST_FLAG_THRESHOLD:
        return -CONFIDENCE_ADJUSTMENT, "low_reputation_source"
    return 0.0, None


def _domain(url: str) -> str:
    """e.g. 'https://www.mothership.sg/2026/...' -> 'mothership.sg'"""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc
