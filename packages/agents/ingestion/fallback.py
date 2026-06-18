"""
FallbackLadder (INGESTION_DESIGN.md §6) — the "when blocked, then what" state
machine, per source.

  NORMAL ──fetch ok──────────────────────────────▶ done (status='ok')
     │
     ├─ SourceUnavailableError (transient) ──▶ BACKOFF
     │                                          wait one fixed interval, retry ONCE
     │                                          ├─ ok ─▶ done (status='ok')
     │                                          └─ fail ─▶ SKIP_SOURCE (status='unavailable'/'blocked')
     │
     └─ SourceBlockedError (bot trap) ───────▶ SKIP_SOURCE immediately
                                                (status='blocked', no retry — do NOT
                                                retry into a ban)

SKIP_SOURCE: record reason, leave watermark UNCHANGED (caller's job — this
module is pure state logic and does not touch pipeline_state), mark the pass
DEGRADED, continue to the next source.

Pure state logic: takes a zero-arg fetch callable, returns
(candidates_or_none, SourceResult). No DB access, no consolidation.
"""

import logging
import time

from ingestion.contracts import (
    Candidate,
    SourceBlockedError,
    SourceResult,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = 30  # fixed interval before the single retry


def run_with_fallback(
    source_name: str,
    fetch,
    backoff_seconds: float = BACKOFF_SECONDS,
) -> tuple[list[Candidate] | None, SourceResult]:
    """
    Execute `fetch()` — a zero-arg callable, typically
    `lambda: source.fetch(since=watermark)` — through the FallbackLadder.

    Returns:
        (candidates, SourceResult(status='ok', ...))                — success
        (None, SourceResult(status='blocked'|'unavailable', ...))   — SKIP_SOURCE

    A None result means: this source's watermark must NOT be advanced, and
    the pass as a whole is DEGRADED. `fetched`/`fresh`/`novel`/`queued` on the
    returned SourceResult are left at 0 — the orchestrator fills these in
    after RecencyFilter/Deduplicator/queue-write, since this module has no
    visibility into those steps.
    """
    try:
        candidates = fetch()
        return candidates, SourceResult(
            name=source_name, status="ok",
            fetched=len(candidates), fresh=0, novel=0, queued=0, reason=None,
        )
    except SourceBlockedError as exc:
        logger.warning(
            "FallbackLadder: %s BLOCKED — skipping immediately (no retry): %s",
            source_name, exc,
        )
        return None, SourceResult(
            name=source_name, status="blocked",
            fetched=0, fresh=0, novel=0, queued=0, reason=str(exc),
        )
    except SourceUnavailableError as exc:
        logger.warning(
            "FallbackLadder: %s unavailable — backing off %ds then retrying once: %s",
            source_name, backoff_seconds, exc,
        )
        time.sleep(backoff_seconds)
        try:
            candidates = fetch()
            return candidates, SourceResult(
                name=source_name, status="ok",
                fetched=len(candidates), fresh=0, novel=0, queued=0, reason=None,
            )
        except SourceBlockedError as retry_exc:
            logger.warning(
                "FallbackLadder: %s blocked on retry — skipping: %s", source_name, retry_exc,
            )
            return None, SourceResult(
                name=source_name, status="blocked",
                fetched=0, fresh=0, novel=0, queued=0, reason=str(retry_exc),
            )
        except SourceUnavailableError as retry_exc:
            logger.warning(
                "FallbackLadder: %s still unavailable after retry — skipping: %s",
                source_name, retry_exc,
            )
            return None, SourceResult(
                name=source_name, status="unavailable",
                fetched=0, fresh=0, novel=0, queued=0, reason=str(retry_exc),
            )
