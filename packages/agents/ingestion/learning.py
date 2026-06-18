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


def load_recent_signal_patterns(client=None, limit: int = MAX_SIGNAL_ROWS) -> str:
    """
    Compact natural-language summary of recent operator decisions
    (LEARNING_LOOP.md §2.3 step 2), for injection into the Stage 2 prompt.

    Returns "" if there's no signal yet (cold start) — callers should treat
    an empty string as "nothing to inject," not an error.

    Bounded by `limit` (rows read) and MAX_PATTERNS_PER_CATEGORY (patterns
    surfaced per category) so this stays small enough for a prompt.
    """
    if client is None:
        client = get_supabase_client()

    result = (
        client.table("training_signals")
        .select("decision, reject_reason, proposed_classification, corrected_classification")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return ""

    lines: list[str] = []

    # Pattern 1: what gets rejected, and why (reject_reason taxonomy, TechSpec §1.6)
    reject_reasons = Counter(
        row["reject_reason"]
        for row in rows
        if row.get("decision") == "reject" and row.get("reject_reason")
    )
    for reason, count in reject_reasons.most_common(MAX_PATTERNS_PER_CATEGORY):
        lines.append(f"Operators recently rejected {count} item(s) as '{reason}'.")

    # Pattern 2: consistent reclassifications (proposed -> corrected)
    reclassifications = Counter(
        (row["proposed_classification"], row["corrected_classification"])
        for row in rows
        if row.get("proposed_classification")
        and row.get("corrected_classification")
        and row["proposed_classification"] != row["corrected_classification"]
    )
    for (proposed, corrected), count in reclassifications.most_common(MAX_PATTERNS_PER_CATEGORY):
        lines.append(
            f"Operators re-classified {count} item(s) from '{proposed}' to '{corrected}'."
        )

    if not lines:
        return ""

    header = f"Recent operator patterns (last {len(rows)} decisions):"
    return "\n".join([header] + [f"- {line}" for line in lines])


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
