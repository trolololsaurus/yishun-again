"""
Consolidation rules — thresholds and keyword pre-filter (spec v1.5 §4.5).

Extracted from classifiers/consolidation.py unchanged (INGESTION_DESIGN.md §10b
step 2). These are the small, executable "rules" consolidation.check() applies
before/around the Claude judgement call.
"""

import re

# Fetch up to this many published incidents for comparison, ordered
# most-recent-first. This is NOT the cost control (MIN_KEYWORD_OVERLAP below
# is — it's what bounds how many of these ever reach a Haiku call), so it must
# comfortably exceed the archive size rather than trail it. A low limit here
# silently excludes anything published long ago from the comparison pool
# altogether: the 2008 Yishun triple murder (backfilled, published_at
# 2012-11-30) sat outside a 50-row recency window while 200+ incidents from
# 2026 out-ranked it, so a 2026 "on this day" retrospective about it was never
# even shown to the judge and queued as a brand-new incident. Bump this before
# the archive grows past it.
CANDIDATE_FETCH_LIMIT = 2000

# Fetch up to this many recent UNPROCESSED war_room_queue items for comparison.
# Catches duplicates that arrive across successive passes before any sibling is
# approved/published — the published-only comparison can't see those (a second
# report of the same event just becomes a second pending row). A same_incident
# match against a queued item means the event is already awaiting review, so the
# new candidate is a SKIP rather than a fresh duplicate row.
QUEUE_FETCH_LIMIT = 50

# Minimum token overlap for the keyword pre-filter before adding a record to the
# batched Claude comparison. Records below this overlap are not sent to the model.
MIN_KEYWORD_OVERLAP = 1

# Same-event confidence at or above this value means UPDATE/SKIP; otherwise the
# candidate remains a new pending row for operator review.
UPDATE_MATCH_THRESHOLD = 0.7

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
