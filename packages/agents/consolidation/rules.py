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

# Cap on Claude judgement calls per candidate. UNUSED since batching landed —
# check.py sends every eligible record in ONE call and no longer reads this.
# Kept defined so the env var does not become a silent no-op mid-rollback.
#
# ⚠️ The "dominant cost" note below is HISTORICAL. It described pairwise
# judging, which is gone.
#
# This was the pipeline's dominant cost. With MIN_KEYWORD_OVERLAP=1, a single
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

# ── Prompt caching on the consolidation call: DECIDED AGAINST (A2, 2026-08-02) ──
#
# The plan was to send the archive pool as a cached prefix so only per-candidate
# content is billed at full rate. Four measurements killed it, any one of them
# sufficient:
#
# 1. THE PREFIX IS NOT IDENTICAL ACROSS CANDIDATES. check.py scores every record
#    by keyword overlap against THAT candidate and keeps only those clearing
#    MIN_KEYWORD_OVERLAP, sorted by overlap descending. Membership AND order
#    differ per candidate by construction — 39 of 54 records for one measured
#    candidate. Caching is a byte-exact prefix match, so there is nothing stable
#    to cache. Sending the full unfiltered pool instead would work, but that
#    removes the ranking, and A2 required behaviour to be unchanged.
#
# 2. THE FILTERED PROMPT IS BELOW THE CACHEABLE MINIMUM. Measured via
#    /v1/messages/count_tokens against claude-haiku-4-5: filtered pool 3,889
#    tokens, full pool 5,259. Haiku 4.5's minimum cacheable prefix is 4,096 —
#    the HIGHEST tier of any current model, above Opus 4.8 (1,024) and Claude
#    Opus 5 (512). The minimum is not monotonic across generations, so a figure
#    remembered from another model is not transferable. Below it a cache_control
#    marker is silently ignored: no error, cache_creation_input_tokens = 0.
#
# 3. VOLUME SITS AT BREAK-EVEN. One batched call per candidate, and the pass
#    averages 3.0 candidates (measured over five passes: 2, 2, 4, 6, 1). A cache
#    write costs 1.25x base input at the 5-minute TTL and a read 0.1x, so
#    break-even is ~2 calls sharing one prefix. Three is marginal at best, and
#    only if 1 and 2 were solved.
#
# 4. THE PREMISE EXPIRED. A2 was written against "~87 Haiku calls in 3 minutes",
#    which was pairwise judging. Batching (A3) already collapsed that to one call
#    per candidate — the ~30x saving A2 was chasing was banked before it ran.
#
# Revisit only if pass volume rises far enough that step 3 stops being marginal,
# AND the per-candidate ranking is dropped or moved inside the prompt.

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


# ── Numeric locality tokens ─────────────────────────────────────────────────
#
# extract_keywords is re.findall(r"[a-z]{4,}") — digits can NEVER become
# keywords. So the single most discriminating fact between two Yishun car-park
# fires, "Block 512" vs "Block 900", is completely invisible to keyword overlap:
# the two stories look lexically identical.
#
# This extracts those numbers separately. It does NOT modify extract_keywords —
# consolidation's ranking, clustering's edge proposal and the stop-word set all
# depend on that function's exact output.

# "Block 512", "Blk 512C", "blk. #512", "BLOCK 107"
_BLOCK_RE = re.compile(r"\b(?:block|blk)\.?\s*#?\s*(\d{1,4}[a-z]?)\b", re.IGNORECASE)

# "Yishun Street 81", "Ave 4", "Avenue 11", "Ring Road 3"
_STREET_TYPES = {
    "street": "st", "st": "st",
    "avenue": "ave", "ave": "ave", "av": "ave",
    "ring road": "ring", "ringroad": "ring",
    "drive": "dr", "dr": "dr",
    "central": "ctrl",
    "link": "link", "walk": "walk", "close": "cl", "crescent": "cres",
    "lane": "ln", "road": "rd",
}
_STREET_RE = re.compile(
    r"\b(street|st|avenue|ave|av|ring road|ringroad|drive|dr|central|link|walk|close|crescent|lane|road)"
    r"\.?\s*(\d{1,3})\b",
    re.IGNORECASE,
)


def extract_locality_tokens(text: str) -> set[str]:
    """
    Numeric locality identifiers in `text`, normalised and namespaced.

    Returns tokens like {"blk:512c", "st:81", "ave:4"}. The namespace prefix
    matters: it is what lets a caller compare like with like, so "Block 512" and
    "Street 81" are simply two different facts rather than a contradiction (a
    block does sit on a street). Only a disagreement WITHIN a namespace —
    blk:512 against blk:900 — is evidence of two different places.

    Pure and deterministic; no model call, no I/O.
    """
    if not text:
        return set()
    out: set[str] = set()
    for m in _BLOCK_RE.finditer(text):
        out.add(f"blk:{m.group(1).lower()}")
    for m in _STREET_RE.finditer(text):
        kind = _STREET_TYPES.get(m.group(1).lower().replace(" ", ""), None) \
            or _STREET_TYPES.get(m.group(1).lower(), None)
        if kind:
            out.add(f"{kind}:{m.group(2)}")
    return out


def locality_conflict(a: set[str], b: set[str]) -> bool:
    """
    True when `a` and `b` name DIFFERENT places, and we can prove it.

    Conflict requires both sides to carry a token in the SAME namespace and for
    those tokens to be disjoint — "Block 512" vs "Block 900". Deliberately NOT a
    conflict:
      - either side empty (no evidence is not counter-evidence)
      - overlapping tokens ("Block 512" vs "Block 512, Street 81" is one place
        described in more detail)
      - tokens in different namespaces ("Block 512" vs "Street 81" may well be
        the same location)
    """
    if not a or not b:
        return False
    by_ns_a: dict[str, set[str]] = {}
    by_ns_b: dict[str, set[str]] = {}
    for token, bucket in ((t, by_ns_a) for t in a):
        bucket.setdefault(token.split(":", 1)[0], set()).add(token)
    for token, bucket in ((t, by_ns_b) for t in b):
        bucket.setdefault(token.split(":", 1)[0], set()).add(token)
    for ns in by_ns_a.keys() & by_ns_b.keys():
        if not (by_ns_a[ns] & by_ns_b[ns]):
            return True
    return False
