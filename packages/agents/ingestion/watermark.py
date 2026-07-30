"""
Per-source recency-watermark arithmetic (INGESTION_DESIGN.md §5.1, §8).

Pure: no I/O, no model calls, no clock of its own. `run_ingestion_pass` owns one
WatermarkTracker per source per pass and asks it for the value to persist;
`state_store.update` writes whatever it is handed.

## Why this is not just "max published_at of the rows we wrote"

§8's rule is that a source's watermark is the max `published_at` *actually
ingested* this run. The orchestrator used to read "ingested" as "written to
war_room_queue", which is much narrower — and it cost money every single day.

A candidate Stage 1 rejected, or that consolidation judged a duplicate of a
still-pending queue row, is never written anywhere. `dedup.is_duplicate` only
looks at `war_room_queue.source_url` and `incidents.source_urls`, so it cannot
see that candidate next pass either. With the watermark also refusing to move
past it, the same article was re-fetched, re-Stage-1'd (Gemini), re-drafted (two
Haiku calls) and re-judged by consolidation on EVERY daily pass — until some
unrelated candidate from the same source happened to drag the watermark forward.

The distinction that matters is not written-vs-unwritten. It is
DECIDED-vs-INTERRUPTED:

    decided(c)     The pipeline reached a verdict on `c` that another pass would
                   only pay to reproduce: queued, Stage-1 rejected, judged a
                   duplicate of something already queued, or already visible to
                   dedup. Safe to advance past.
    unresolved(c)  `c` was never judged on its merits: a model or DB error, the
                   pass deadline, a mid-pass Stage 1 budget halt, or a gathered
                   candidate whose cluster-write phase never ran. It MUST be
                   offered again.

## The two holdbacks, both load-bearing

`RecencyFilter` drops `published_at <= watermark`, so the watermark is a
date-granular guillotine: advancing it to one candidate's date also drops every
same-day sibling. While only *written* candidates advanced it that was a rare
corner. Once decided candidates advance it — the whole point of this module — it
becomes the common case, so both holdbacks below are part of the fix, not
decoration.

1. **Retry floor.** Every unresolved candidate's date is a floor, and only
   decided dates strictly below the lowest floor advance the watermark. Without
   it, a candidate that hit a transient error would be silently dropped by its
   own successfully-decided siblings.

2. **Same-day grace.** A decided candidate dated on or after the pass date is
   recorded as unresolved anyway. An outlet publishes all day; the pass runs once
   (14:58 SGT). Advancing to today's date would drop everything that source
   published after the pass ran — unseen, unlogged, and a far worse failure than
   the cost bug this module exists to fix. The grace costs at most ONE extra pass
   per article, because tomorrow that date is in the past and advances normally.
   Bounded, where the old behaviour was not.

Dateless candidates never move the watermark in either direction. They bypass
RecencyFilter entirely by design (QA H3) and are re-offered every pass until an
operator supplies a date.

Candidates are keyed by URL rather than identity, so re-marking the same
candidate is idempotent and the last call wins — which is what the 'on' path
needs: a gathered candidate is held unresolved and then upgraded to decided once
the cluster-write phase settles it.
"""

from datetime import date, datetime


class WatermarkTracker:
    """
    How far one source's watermark may advance this pass. See the module
    docstring for why `decided` is not the same thing as `queued`.

    Never raises on odd input: a candidate missing `url` or `published_at` is
    accepted and simply contributes nothing to the arithmetic. This runs inside
    the orchestrator's per-candidate loop, whose contract is that it never fails
    the pass for a bookkeeping reason.
    """

    def __init__(self, source_name: str, original: date | None, *, pass_date: date | None):
        """
        Args:
            source_name: for the operator-visible hold note only.
            original:    the persisted watermark this pass started from. The
                         returned value never regresses below it.
            pass_date:   the pass's own date — dates on or after it are held back
                         (same-day grace). None disables the grace, which is only
                         appropriate in tests.
        """
        self.source_name = source_name
        self.original = original
        self.pass_date = pass_date
        self._decided: dict[str, date] = {}
        self._unresolved: dict[str, date] = {}

    # ── recording ────────────────────────────────────────────────────────────

    def decided(self, candidate) -> None:
        """
        Record a terminal verdict: another pass would only pay to reproduce it.

        A candidate dated on or after `pass_date` is recorded as UNRESOLVED
        instead — see "same-day grace" in the module docstring. That is not a
        contradiction of the caller's verdict; the verdict stands, it just may not
        move a date-granular watermark onto a day the source is still publishing.
        """
        key, published = _key_and_date(candidate)
        if published is not None and self.pass_date is not None and published >= self.pass_date:
            self.unresolved(candidate)
            return
        self._unresolved.pop(key, None)
        if published is not None:
            self._decided[key] = published

    def unresolved(self, candidate) -> None:
        """Record that `candidate` must be offered again next pass."""
        key, published = _key_and_date(candidate)
        self._decided.pop(key, None)
        if published is not None:
            self._unresolved[key] = published

    def unresolved_all(self, candidates) -> None:
        """Bulk `unresolved` — for the remainder of a loop that broke early."""
        for candidate in candidates:
            self.unresolved(candidate)

    # ── reading ──────────────────────────────────────────────────────────────

    @property
    def floor(self) -> date | None:
        """The earliest date that still needs another pass, or None."""
        return min(self._unresolved.values(), default=None)

    def value(self) -> date | None:
        """The watermark to persist: the newest decided date strictly below the
        retry floor, never below where this pass started."""
        floor = self.floor
        best = self.original
        for published in self._decided.values():
            if floor is not None and published >= floor:
                continue
            if best is None or published > best:
                best = published
        return best

    def hold_note(self) -> str | None:
        """
        An operator-visible note, but only when the floor actually cost something
        — i.e. a decided candidate could have advanced the watermark and was held
        back. Silence when nothing was held is the point: a note on every source
        every pass would be read by nobody.
        """
        floor = self.floor
        if floor is None:
            return None
        held = sum(1 for published in self._decided.values() if published >= floor)
        if not held:
            return None
        current = self.value()
        return (
            f"{self.source_name}: watermark held at {current.isoformat() if current else 'unset'} "
            f"— {held} decided candidate(s) not advanced past {floor.isoformat()}, "
            f"which still needs another pass."
        )


def _key_and_date(candidate) -> tuple[str, date | None]:
    """
    (dedup key, publication date) for a candidate, tolerating either being absent.

    A `datetime` is narrowed to its date: `datetime` subclasses `date`, so an
    off-contract source emitting one would otherwise poison every comparison here
    with a TypeError (`can't compare datetime to date`).
    """
    key = getattr(candidate, "url", "") or ""
    published = getattr(candidate, "published_at", None)
    if isinstance(published, datetime):
        return key, published.date()
    return key, published if isinstance(published, date) else None
