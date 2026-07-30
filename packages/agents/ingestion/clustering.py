"""
Story clustering (gather -> cluster -> write-once).

The forward pipeline processes one candidate (one URL) at a time: it writes a
full Sonnet draft, THEN asks consolidation whether it is a duplicate, and if so
drops it (orchestrator: action='skip' -> continue). So when N outlets report the
same event in one pass, we pay for N Sonnet drafts and keep ONE single-source
incident — the other sources' corroboration is thrown away, and the ⚡ meter
(corroboration_count - 1) reads zero. Live audit: ~85% of forward-era published
incidents are single-source.

Clustering groups the Stage-1-passed candidates by story BEFORE Stage 2, so each
story is written once with all its sources (corroboration_count = cluster size,
source_urls = all non-signal members). This module is the pure, offline-testable
core: no I/O, no model calls. The orchestrator feeds it the passed candidates and
either logs the clusters (shadow mode) or writes one row per cluster (on mode).

## The algorithm, and why it is shaped to never MERGE two different events

A wrong SPLIT (two cards for one event) is a cosmetic miss the operator or the
integrity agent catches. A wrong MERGE (one card conflating two events) is a
data-corruption error that is hard to unwind. So the whole design is biased
toward splitting:

1. **Dated MSM members cluster first** by union-find, with an edge only when the
   keyword overlap is >= CLUSTER_MIN_OVERLAP (deliberately stricter than
   consolidation's 1) AND the two dates are within CLUSTER_DATE_WINDOW_DAYS.
   Requiring both a strong lexical match and date proximity stops a shared
   generic token ("fire", "Yishun") from bridging unrelated events.

2. **Signal (Reddit/EDMW) and dateless members ATTACH, never BRIDGE.** A signal
   post carries no reliable event date, so it is added to the single best
   keyword-matching event cluster afterwards — it can never transitively merge
   two dated clusters. This is what "signal members never widen the window"
   means, and it closes the reddit-revival duplicate at the clustering layer too.

3. **MAX_CLUSTER_SIZE caps a runaway.** A cluster that grows past the cap is left
   intact but flagged; the orchestrator refuses to auto-merge an oversized
   cluster (writes its members individually) so one viral keyword cannot swallow
   a whole pass into one incident.

The keyword-only edges here are a cheap pre-filter. In `on` mode the orchestrator
still runs a bounded Haiku confirmation per multi-member cluster before merging,
and consolidation-against-the-archive still runs on the resulting draft — this
module only proposes groups, it never decides to publish.
"""

import logging
import os
from datetime import date

from classifiers.source_allowlist import canonical_source_type
from consolidation.rules import (
    extract_keywords,
    extract_locality_tokens,
    keyword_overlap,
    locality_conflict,
)

logger = logging.getLogger(__name__)

# Stricter than consolidation's MIN_KEYWORD_OVERLAP=1: a merge is higher-stakes
# than a judge call, so it needs a stronger lexical signal.
CLUSTER_MIN_OVERLAP = int(os.getenv("CLUSTER_MIN_OVERLAP", "2"))
# Two MSM reports of the same event usually land within a couple of days
# (initial + next-day follow-up). Wider risks merging distinct same-location events.
CLUSTER_DATE_WINDOW_DAYS = int(os.getenv("CLUSTER_DATE_WINDOW_DAYS", "3"))
# Blast-radius cap: no single cluster may auto-merge more than this many members.
CLUSTER_MAX_SIZE = int(os.getenv("CLUSTER_MAX_SIZE", "6"))
# Ceiling on LLM merge-confirmation calls per pass (cost + latency bound). Edges
# are judged in overlap-rank order, so genuine duplicates (which share the
# distinctive tokens) are confirmed first; unjudged edges default to NO merge.
CLUSTER_MAX_JUDGES = int(os.getenv("CLUSTER_MAX_JUDGES", "20"))

# Generic Yishun-incident vocabulary that is NOT distinctive between events and
# so must not, on its own, propose a merge. Shadow validation showed the pure
# keyword pass chaining a beehive story, a car crash and a fatal fall into one
# cluster because they all share "block"/"flat"/"fire"/"dead" inside the date
# window and union-find fuses the transitive blob. Dropping these tokens from the
# CLUSTERING keyword set (not consolidation's) cuts most false edges; the batched
# grouper is the real precision layer. Deliberately keeps act words (stab;
# fire-as-verb is ambiguous but kept) and names.
#
# NUMBERS ARE NOT RETAINED. extract_keywords is re.findall(r"[a-z]{4,}"), which
# cannot match a digit — "block 107" contributes only the token "block", and that
# is in the generic set above, so it contributes nothing at all. An earlier
# version of this comment claimed numbers were kept; they never were, and no
# threshold tuning here can change it. Block and street numbers are handled
# separately by consolidation.rules.extract_locality_tokens, which group_candidates
# applies as a post-grouping VETO (see _veto_group) rather than as a merge signal.
_GENERIC_TOKENS = frozenset({
    "block", "blk", "flat", "unit", "hdb", "resident", "residents", "void", "deck",
    "road", "street", "avenue", "lane", "ring", "near", "along", "outside", "home",
    "house", "estate", "carpark", "car", "park", "hospital", "hospitalised",
    "sends", "sent", "after", "before", "found", "dead", "dies", "died", "death",
    "fatal", "rushed", "scene", "case", "reported", "video", "footage", "viral",
})


def _kw(text: str) -> set[str]:
    return extract_keywords(text) - _GENERIC_TOKENS


def _is_signal(c) -> bool:
    # By source_type alone (pure, no DB): the source adapter already
    # canonicalises reddit/EDMW to 'signal', so a domain lookup here would be
    # redundant AND would make this pure module hit the network. The real
    # guardrail-#2 enforcement (domain-aware URL stripping) still runs later in
    # build_queue_row/check_source_urls on the written row.
    return canonical_source_type(getattr(c, "source_type", "")) == "signal"


def _text(c) -> str:
    return f"{getattr(c, 'title', '') or ''} {getattr(c, 'content', '') or ''}"


def _pub_date(c):
    d = getattr(c, "published_at", None)
    return d if isinstance(d, date) else None


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_candidates(
    candidates,
    *,
    min_overlap: int = CLUSTER_MIN_OVERLAP,
    date_window_days: int = CLUSTER_DATE_WINDOW_DAYS,
) -> list[list]:
    """
    Group candidates by story. Pure function — returns a list of clusters, each a
    list of the input candidates; every candidate appears in exactly one cluster.
    Order within a cluster follows input order. A single unmatched candidate is a
    cluster of one, so the caller can treat every result uniformly.
    """
    n = len(candidates)
    if n <= 1:
        return [[c] for c in candidates]

    kw = [_kw(_text(c)) for c in candidates]
    dates = [_pub_date(c) for c in candidates]
    signal = [_is_signal(c) for c in candidates]

    # An "anchor" is a dated, non-signal member — the only kind that may seed and
    # bridge a cluster. Signal/dateless members attach afterwards.
    anchor = [(not signal[i]) and (dates[i] is not None) for i in range(n)]

    uf = _UnionFind(n)
    for i in range(n):
        if not anchor[i]:
            continue
        for j in range(i + 1, n):
            if not anchor[j]:
                continue
            if keyword_overlap(kw[i], kw[j]) < min_overlap:
                continue
            if abs((dates[i] - dates[j]).days) > date_window_days:
                continue
            uf.union(i, j)

    # Anchor clusters, keyed by union-find root, preserving input order.
    groups: dict[int, list[int]] = {}
    for i in range(n):
        if anchor[i]:
            groups.setdefault(uf.find(i), []).append(i)

    # Signal members attach to their best keyword cluster (never bridging);
    # dateless MSM members stay standalone (no unconfirmed keyword-merge).
    _attach_non_anchors(candidates, kw, anchor, signal, groups, min_overlap)

    clusters = [[candidates[i] for i in sorted(idxs)] for idxs in groups.values()]
    # Deterministic order: by the first member's input position.
    idx_of = {id(c): k for k, c in enumerate(candidates)}
    clusters.sort(key=lambda cl: idx_of[id(cl[0])])
    return clusters


def oversized(cluster, max_size: int = CLUSTER_MAX_SIZE) -> bool:
    """A cluster the caller must NOT auto-merge (blast-radius cap)."""
    return len(cluster) > max_size


def cluster_with_confirmation(
    candidates,
    judge,
    *,
    min_overlap: int = CLUSTER_MIN_OVERLAP,
    date_window_days: int = CLUSTER_DATE_WINDOW_DAYS,
    max_judges: int = CLUSTER_MAX_JUDGES,
) -> tuple[list[list], dict]:
    """
    Cluster with an LLM confirming EVERY merge — the precision layer.

    The keyword pre-filter is high-recall but low-precision: it proposes edges
    between dated MSM anchors that share >= min_overlap distinctive tokens within
    the date window. Left to union-find those loose edges chain unrelated events
    into a blob (shadow proved it). So here every proposed edge is put to
    `judge(a, b) -> bool` ("same event?"), and ONLY confirmed edges are unioned.
    A generic-token bridge (beehive vs fatal fall) is rejected and never merges.

    `judge` is injected (the orchestrator wraps the Haiku pair judge) so this
    module stays offline-testable. Edges are judged in overlap-rank order and
    capped at `max_judges`; an unjudged or errored edge defaults to NO merge
    (split-safe). Signal/dateless members then attach to at most one confirmed
    cluster, never bridging — same as the pure pass.

    Returns (clusters, stats).
    """
    n = len(candidates)
    if n <= 1:
        return ([[c] for c in candidates],
                {"edges_proposed": 0, "edges_judged": 0, "edges_confirmed": 0, "judge_errors": 0})

    kw = [_kw(_text(c)) for c in candidates]
    dates = [_pub_date(c) for c in candidates]
    signal = [_is_signal(c) for c in candidates]
    anchor = [(not signal[i]) and (dates[i] is not None) for i in range(n)]

    # Propose anchor edges, ranked by overlap (genuine dups share more tokens, so
    # they are judged first and survive the cap).
    edges = []
    for i in range(n):
        if not anchor[i]:
            continue
        for j in range(i + 1, n):
            if not anchor[j]:
                continue
            ov = keyword_overlap(kw[i], kw[j])
            if ov < min_overlap:
                continue
            if abs((dates[i] - dates[j]).days) > date_window_days:
                continue
            edges.append((ov, i, j))
    edges.sort(reverse=True)

    uf = _UnionFind(n)
    judged = confirmed = errors = 0
    for ov, i, j in edges:
        if judged >= max_judges:
            logger.info("clustering: hit CLUSTER_MAX_JUDGES=%d; %d edge(s) left unjudged (kept split)",
                        max_judges, len(edges) - judged)
            break
        if uf.find(i) == uf.find(j):
            continue  # already in the same cluster via a confirmed path — don't pay again
        judged += 1
        try:
            same = bool(judge(candidates[i], candidates[j]))
        except Exception as exc:                  # noqa: BLE001
            errors += 1
            logger.warning("clustering: merge judge failed for a pair (kept split): %s", exc)
            continue
        if same:
            uf.union(i, j)
            confirmed += 1

    groups: dict[int, list[int]] = {}
    for i in range(n):
        if anchor[i]:
            groups.setdefault(uf.find(i), []).append(i)

    _attach_non_anchors(candidates, kw, anchor, signal, groups, min_overlap)

    clusters = [[candidates[i] for i in sorted(idxs)] for idxs in groups.values()]
    idx_of = {id(c): k for k, c in enumerate(candidates)}
    clusters.sort(key=lambda cl: idx_of[id(cl[0])])
    stats = {"edges_proposed": len(edges), "edges_judged": judged,
             "edges_confirmed": confirmed, "judge_errors": errors}
    return clusters, stats


def _veto_group(group_global_idx, loc) -> tuple[str, str] | None:
    """
    The locality veto. Returns the first conflicting token pair found in the
    group, or None if the group is locality-consistent.

    Deterministic and offline — no model call. It overrules the grouper because
    block and street numbers are ground truth the grouper is structurally bad at
    weighing: two Yishun car-park fires read almost identically in prose, and the
    ONE fact that separates them is a number the keyword layer cannot even see
    (extract_keywords is [a-z]{4,}).
    """
    for a_pos in range(len(group_global_idx)):
        for b_pos in range(a_pos + 1, len(group_global_idx)):
            a, b = loc[group_global_idx[a_pos]], loc[group_global_idx[b_pos]]
            if locality_conflict(a, b):
                return (",".join(sorted(a)), ",".join(sorted(b)))
    return None


def _validate_index_groups(raw, n):
    """
    Accept a grouper's answer only if it is an EXACT partition of range(n).

    Returns the normalised list of index groups, or None if the answer is not a
    partition — a missing index, a repeated one, a non-integer, an out-of-range
    value, an empty group, or a non-list. None means "keep everything split".

    Deliberately all-or-nothing. A merge corrupts a published incident and is
    hard to unwind, so a partially-parseable answer is not salvaged into
    "merge the bit we understood" — an unparseable response is exactly the case
    where we know least about what the model meant.
    """
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[list[int]] = []
    seen: set[int] = set()
    for grp in raw:
        if not isinstance(grp, (list, tuple)) or not grp:
            return None
        members: list[int] = []
        for v in grp:
            # bool is an int subclass; `True` must not silently mean index 1.
            if isinstance(v, bool) or not isinstance(v, int):
                return None
            if v < 0 or v >= n or v in seen:
                return None
            seen.add(v)
            members.append(v)
        out.append(members)
    if len(seen) != n:
        return None
    return out


def group_candidates(
    candidates,
    grouper,
    *,
    min_overlap: int = CLUSTER_MIN_OVERLAP,
    date_window_days: int = CLUSTER_DATE_WINDOW_DAYS,
) -> tuple[list[list], dict]:
    """
    Cluster with ONE batched grouping decision instead of per-pair judgements.

    `grouper(candidates) -> list[list[int]]` is injected (the orchestrator wraps
    a single Haiku call) so this module stays pure and offline-testable. It is
    handed only the candidates the keyword pass found a plausible peer for, and
    returns index groups into that list.

    ## Why this replaces cluster_with_confirmation

    Pairwise judging plus union-find creates TRANSITIVE merges: A~B confirmed and
    B~C confirmed merges A, B and C even though nothing ever compared A to C.
    That is the mechanism behind the beehive / car-crash / fatal-fall blob the
    shadow run caught — no single judgement was obviously wrong, the blob was
    assembled by the union-find between them. One grouping call has no union-find
    and can contrast every member simultaneously, so a group is only ever formed
    by a decision that actually saw all of its members.

    ## What each layer still does

    - The keyword pass is now purely an INPUT FILTER: a candidate is offered to
      the grouper only if it shares >= min_overlap distinctive tokens with some
      other anchor inside date_window_days. This bounds prompt size, and an
      anchor with no plausible peer is a singleton without paying for a call.
      It no longer decides who merges — the grouper does.
    - Signal and dateless members keep their existing handling via
      _attach_non_anchors: signals ATTACH to at most one group and never bridge;
      dateless MSM members stay standalone.

    An errored, unparseable, or non-partition grouper response degrades to
    all-singletons (split-safe) — never to a merge. Never raises.

    Returns (clusters, stats).
    """
    n = len(candidates)
    stats = {"pool_size": 0, "edges_proposed": 0, "groups_returned": 0,
             "merges": 0, "grouper_errors": 0, "grouper_called": False,
             "locality_vetoes": 0}
    if n <= 1:
        return ([[c] for c in candidates], stats)

    kw = [_kw(_text(c)) for c in candidates]
    dates = [_pub_date(c) for c in candidates]
    signal = [_is_signal(c) for c in candidates]
    loc = [extract_locality_tokens(_text(c)) for c in candidates]
    anchor = [(not signal[i]) and (dates[i] is not None) for i in range(n)]

    # Input filter: who even gets offered to the grouper.
    in_pool = [False] * n
    for i in range(n):
        if not anchor[i]:
            continue
        for j in range(i + 1, n):
            if not anchor[j]:
                continue
            if keyword_overlap(kw[i], kw[j]) < min_overlap:
                continue
            if abs((dates[i] - dates[j]).days) > date_window_days:
                continue
            stats["edges_proposed"] += 1
            in_pool[i] = in_pool[j] = True

    pool = [i for i in range(n) if in_pool[i]]
    stats["pool_size"] = len(pool)

    # Anchors the filter found no plausible peer for: singletons, no call needed.
    groups: dict = {("solo", i): [i] for i in range(n) if anchor[i] and not in_pool[i]}

    merged: list[list[int]] | None = None
    if len(pool) >= 2 and grouper is not None:
        stats["grouper_called"] = True
        raw = None
        try:
            raw = grouper([candidates[i] for i in pool])
        except Exception as exc:                      # noqa: BLE001 — split-safe
            stats["grouper_errors"] += 1
            logger.warning("clustering: grouper failed for %d candidate(s) "
                           "(all kept split): %s", len(pool), exc)
        if raw is not None:
            partition = _validate_index_groups(raw, len(pool))
            if partition is None:
                stats["grouper_errors"] += 1
                logger.warning(
                    "clustering: grouper returned an invalid partition of %d candidate(s) "
                    "— missing, duplicate or out-of-range indices (all kept split)", len(pool))
            else:
                merged = [[pool[t] for t in grp] for grp in partition]

    if merged is None:
        merged = [[i] for i in pool]                  # split-safe default

    # ── Locality veto ────────────────────────────────────────────────────────
    # Applied AFTER the grouper and overruling it. A group holding two members
    # that name different blocks is split back to singletons wholesale rather
    # than partially repaired: once the group contains a provable contradiction
    # we no longer trust its shape, and splitting is the cheap error.
    vetoed: list[list[int]] = []
    for grp in merged:
        conflict = _veto_group(grp, loc) if len(grp) > 1 else None
        if conflict:
            stats["locality_vetoes"] += 1
            logger.warning(
                "clustering: locality veto — refusing to merge %d candidate(s); "
                "conflicting locality tokens %s vs %s", len(grp), conflict[0], conflict[1],
            )
            vetoed.extend([[i] for i in grp])
        else:
            vetoed.append(grp)
    merged = vetoed

    for k, grp in enumerate(merged):
        groups[("g", k)] = grp
    stats["groups_returned"] = len(merged)
    stats["merges"] = sum(1 for g in merged if len(g) > 1)

    _attach_non_anchors(candidates, kw, anchor, signal, groups, min_overlap)

    clusters = [[candidates[i] for i in sorted(idxs)] for idxs in groups.values()]
    idx_of = {id(c): k for k, c in enumerate(candidates)}
    clusters.sort(key=lambda cl: idx_of[id(cl[0])])
    return clusters, stats


def _attach_non_anchors(candidates, kw, anchor, signal, groups, min_overlap):
    """
    Attach ONLY signal members (reddit/EDMW) to their best keyword-matching
    cluster — low-stakes, and their whole purpose is to corroborate a story with
    forum buzz. A dateless MSM member is NOT attached by keyword alone: an
    unconfirmed merge of a real reporting source is the same false-merge risk the
    LLM confirmation exists to prevent, so it stays STANDALONE (its own row) until
    a human links it. Mutates `groups` in place.
    """
    anchor_roots = list(groups.keys())
    for i in range(len(candidates)):
        if anchor[i]:
            continue
        if signal[i]:
            best_root, best_score = None, min_overlap - 1
            for root in anchor_roots:
                score = max(keyword_overlap(kw[i], kw[m]) for m in groups[root])
                if score > best_score:
                    best_root, best_score = root, score
            groups.setdefault(best_root if best_root is not None else id(candidates[i]), []).append(i)
        else:
            groups[id(candidates[i])] = [i]  # dateless MSM: standalone, never keyword-merged


def build_cluster_stage2_input(cluster, item_of) -> dict:
    """
    Turn a multi-member cluster into a single write_stage2 input, mirroring the
    proven multi-source shape from seed_backfill.py (source_urls / source_timeline
    / source_articles are non-signal only; guardrail #2). `item_of(candidate)` maps
    a candidate to its `_candidate_to_item` dict (title/content/url/date/...).

    Primary = earliest-dated non-signal member (the initial report), falling back
    to the first member. edmw_signal_count = number of signal members. Returns the
    stage2 input dict WITHOUT source_urls stripping applied — the caller runs
    check_source_urls / build_queue_row exactly as the single-candidate path does.
    """
    members = [(c, item_of(c)) for c in cluster]
    non_signal = [(c, it) for (c, it) in members if not _is_signal(c)]
    signal_count = len(members) - len(non_signal)

    if non_signal:
        # Earliest-dated non-signal member is the primary/initial report.
        non_signal.sort(key=lambda ci: (ci[1].get("date") or "9999-99-99"))
        primary_item = dict(non_signal[0][1])
    else:
        # Signal-only cluster: no quotable source. Keep the first member's item so
        # the row still has a title/summary for the operator, but source_urls stays
        # empty -> guardrail #1 keeps it in the queue, never auto-published.
        primary_item = dict(members[0][1])

    src_urls = [it["url"] for (c, it) in non_signal]
    timeline = [{
        "date": it.get("date") or "",
        "source_url": it["url"],
        "source_name": it.get("source_name", ""),
        "headline": it.get("title", ""),
    } for (c, it) in non_signal]
    articles = [{
        "source_name": it.get("source_name", ""),
        "source_type": it.get("source_type", "msm"),
        "url": it.get("url", ""),
        "date": it.get("date"),
        "title": it.get("title", ""),
        "content": it.get("content", ""),
    } for (c, it) in non_signal]

    stage2 = dict(primary_item)
    stage2["source_urls"] = src_urls           # may be [] for a signal-only cluster
    stage2["edmw_signal_count"] = signal_count
    if len(articles) > 1:
        # Only attach multi-source context when there genuinely is more than one
        # source — a single-member cluster must stay byte-identical to the
        # per-candidate path, which passes no source_articles.
        stage2["source_timeline"] = timeline
        stage2["source_articles"] = articles
    return stage2


def summarize(clusters) -> dict:
    """Shadow-mode telemetry: what would grouping do this pass?"""
    multi = [c for c in clusters if len(c) > 1]
    members_in_multi = sum(len(c) for c in multi)
    # Each multi-member cluster collapses len-1 Sonnet drafts into 1.
    drafts_saved = sum(len(c) - 1 for c in multi)
    return {
        "candidates": sum(len(c) for c in clusters),
        "clusters": len(clusters),
        "multi_member_clusters": len(multi),
        "members_in_multi": members_in_multi,
        "sonnet_drafts_saved_estimate": drafts_saved,
        "oversized_clusters": sum(1 for c in clusters if oversized(c)),
    }
