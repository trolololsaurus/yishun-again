"""
Consolidation rules — thresholds and keyword pre-filter (spec v1.5 §4.5).

Extracted from classifiers/consolidation.py unchanged (INGESTION_DESIGN.md §10b
step 2). These are the small, executable "rules" consolidation.check() applies
before/around the Claude judgement call.
"""

import re

# Fetch up to this many recent published incidents for comparison.
CANDIDATE_FETCH_LIMIT = 50

# Minimum token overlap for the keyword pre-filter before sending to Claude.
# If fewer than this many candidate keywords overlap with a published incident,
# skip the Claude call for that pair to save API cost.
MIN_KEYWORD_OVERLAP = 1

# same_incident_confidence >= this → treat as an UPDATE to the matched incident.
UPDATE_MATCH_THRESHOLD = 0.7

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
