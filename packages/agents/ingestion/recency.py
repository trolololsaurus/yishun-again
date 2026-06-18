"""
RecencyFilter (INGESTION_DESIGN.md §5.1).

Pure function, no I/O. Splits a source's candidates into:
  - kept:     published_at > watermark (strictly newer than last successful run)
  - dropped:  published_at <= watermark (already covered by a prior run) — counted, not kept
  - dateless: published_at is None — ROUTE-TO-REVIEW (Q2=2b), never dropped

A dateless item is queued to War Room with a 'dateless' flag; the operator's
sourcing decision (approve-with-date or reject) is itself training signal
(LEARNING_LOOP.md §2.1) — War Room is the sourcing model's training-data
generator, not just an approval gate.

First run (watermark is None, §5.3): nothing has been ingested yet, so no
dated candidate is dropped — all dated candidates are kept.
"""

from datetime import date, datetime

from ingestion.contracts import Candidate


def RecencyFilter(
    candidates: list[Candidate],
    watermark: date | None,
    now: datetime,
) -> tuple[list[Candidate], int, list[Candidate]]:
    """
    Args:
        candidates: this source's fresh fetch (post-source, pre-dedup).
        watermark:  this source's persisted pipeline_state.watermark, or
                    None on first run (§5.3 — NOT a "last N days" lookback).
        now:        pass timestamp, accepted for signature parity with the
                    run_ingestion_pass() entrypoint (§4). Not used by the
                    §5.1 watermark comparison itself in v1.

    Returns:
        (kept, dropped_count, dateless)
    """
    kept: list[Candidate] = []
    dateless: list[Candidate] = []
    dropped_count = 0

    for candidate in candidates:
        if candidate.published_at is None:
            dateless.append(candidate)
            continue

        if watermark is not None and candidate.published_at <= watermark:
            dropped_count += 1
            continue

        kept.append(candidate)

    return kept, dropped_count, dateless
