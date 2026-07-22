"""
Consolidation rules — thresholds and keyword pre-filter (spec v1.5 §4.5).

Extracted from classifiers/consolidation.py unchanged (INGESTION_DESIGN.md §10b
step 2). These are the small, executable "rules" consolidation.check() applies
before/around the Claude judgement call.
"""

import os
import re

# Fetch up to this many recent published incidents for comparison.
CANDIDATE_FETCH_LIMIT = 50

# Fetch up to this many recent UNPROCESSED war_room_queue items for comparison.
# Catches duplicates that arrive across successive passes before any sibling is
# approved/published — the published-only comparison can't see those (a second
# report of the same event just becomes a second pending row). A same_incident
# match against a queued item means the event is already awaiting review, so the
# new candidate is a SKIP rather than a fresh duplicate row.
QUEUE_FETCH_LIMIT = 50

# Minimum token overlap for the keyword pre-filter before sending to Claude.
# If fewer than this many candidate keywords overlap with a published incident,
# skip the Claude call for that pair to save API cost.
MIN_KEYWORD_OVERLAP = 1

# Cap on Claude judgement calls per candidate.
#
# This is the pipeline's dominant cost. With MIN_KEYWORD_OVERLAP=1, a single
# shared 4-letter word ("road", "fire", "block") makes a pair eligible, so
# against a 50-published + 50-queued pool a candidate could fan out to ~100
# Haiku calls — and the count grows with the archive. One live pass spent ~87
# Haiku calls in 3 minutes here, more than Stage 1 and Stage 2 combined.
#
# Ranking the eligible pairs by keyword overlap and judging only the top N caps
# cost at O(candidates) instead of O(candidates x archive size): a genuine
# same-incident report shares the distinctive tokens (the act, names, the
# block), so it sits near the top of the overlap ranking. A dropped low-overlap
# pair is a rare miss, and ops/integrity.py is the backstop — it re-scans for
# duplicate entries every pass and flags them for the operator.
MAX_JUDGEMENTS_PER_CANDIDATE = int(os.getenv("CONSOLIDATION_MAX_JUDGEMENTS", "12"))

# same_incident_confidence >= this → treat as an UPDATE to the matched incident.
UPDATE_MATCH_THRESHOLD = 0.7

# A same_incident match at or above this confidence settles the action (UPDATE
# or SKIP), so stop judging the remaining lower-ranked pairs rather than paying
# to hunt for secondary related-links once the outcome is fixed. Clamped to at
# least UPDATE_MATCH_THRESHOLD: exiting on anything weaker could stop before a
# stronger match that would have changed the decision.
EARLY_EXIT_CONFIDENCE = max(
    UPDATE_MATCH_THRESHOLD,
    float(os.getenv("CONSOLIDATION_EARLY_EXIT_CONFIDENCE", "0.9")),
)

# same_incident_confidence >= this (but below UPDATE_MATCH_THRESHOLD) → possible
# match, surfaced as a low-confidence related link instead of an update.
WEAK_MATCH_THRESHOLD = 0.4

# related_confidence >= this → record the related-incident link.
RELATED_LINK_THRESHOLD = 0.5

# Lower-cased word tokens shorter than this are dropped from keyword extraction.
_MIN_KEYWORD_LEN = 4

_STOP_WORDS = {
    "that", "this", "with", "from", "have", "been", "were", "they",
    "their", "said", "after", "over", "into", "yishun", "singapore",
    "police", "arrested", "incident", "man", "woman", "people",
}


def extract_keywords(text: str) -> set[str]:
    """Lower-cased word tokens >=4 chars, stripped of common stop words."""
    words = re.findall(rf"[a-z]{{{_MIN_KEYWORD_LEN},}}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def keyword_overlap(kw_a: set[str], kw_b: set[str]) -> int:
    return len(kw_a & kw_b)
