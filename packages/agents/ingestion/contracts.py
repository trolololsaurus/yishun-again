"""
Data contracts for the ingestion layer (INGESTION_DESIGN.md §3.1, §3.2, §6, §7).

Pure types only — no logic, no dependencies on other ingestion modules.
This file is the drift-resistant seam: everything downstream (sources,
RecencyFilter, Deduplicator, FallbackLadder, run_ingestion_pass) is written
against these shapes.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


# ── §3.1 Candidate (the unit of flow) ────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    """
    A plain, immutable data object. Every Source emits these; nothing
    downstream cares which Source produced them.

    Contract rules:
    - `url` MUST be the canonical article URL, not a wrapper (e.g. resolve
      news.google.com redirects). Dedupe correctness depends on this.
    - `published_at` MUST be parsed from the source's own date field, never
      inferred from "now." If a source cannot supply a date, published_at is
      None and the item is treated conservatively (§5 RecencyFilter).
    - Frozen (immutable). Transformations produce new objects.
    """
    title: str
    content: str                # summary / snippet (HTML stripped)
    url: str                    # CANONICAL article url (redirects resolved)
    source_name: str            # human-readable, e.g. "Channel NewsAsia"
    # CANONICAL vocabulary: 'msm' | 'reddit' | 'signal' | 'reference' | 'rss'.
    # 'signal' (not 'edmw') is the forum/EDMW value — it is what the sources
    # table CHECK allows and what scrape_edmw emits. Adapters normalise via
    # classifiers.source_allowlist.canonical_source_type, so the legacy 'edmw'
    # spelling never reaches downstream code (QA M14).
    source_type: str
    published_at: date | None   # parsed publication date; None if unknowable
    discovered_via: str         # which Source produced this (e.g. 'google_news_rss')


# ── §3.2 Source (the pluggable interface) ────────────────────────────────────

class Source(Protocol):
    name: str                   # stable id, e.g. 'google_news_rss'; key into pipeline_state
    enabled: bool

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        Return candidates this source currently offers.

        `since` is the source's last-run watermark (may be None on first
        run). A source MAY use `since` to narrow its query, but MUST NOT rely
        on it for correctness — the orchestrator re-applies the
        RecencyFilter regardless. `since` is advisory, not authoritative; the
        orchestrator re-filters.

        MUST raise SourceBlockedError on bot-detection / rate-limit, or
        SourceUnavailableError on transient failure. MUST NOT swallow these.
        """
        ...


# ── §6 FallbackLadder exception types ────────────────────────────────────────

class SourceBlockedError(Exception):
    """Bot-detection / rate-limit (429/403/CAPTCHA). SKIP_SOURCE immediately —
    do NOT retry into a ban."""


class SourceUnavailableError(Exception):
    """Transient failure. FallbackLadder waits one fixed interval and retries
    ONCE before SKIP_SOURCE."""


# ── §7 IngestionReport (no silent failure) ───────────────────────────────────

@dataclass
class SourceResult:
    name: str
    status: str          # 'ok' | 'degraded' | 'blocked' | 'unavailable'
    fetched: int
    fresh: int
    novel: int
    queued: int
    reason: str | None = None


@dataclass
class IngestionReport:
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    per_source: list[SourceResult]
    total_queued: int
    new_count: int                     # kind='new' rows
    update_count: int                  # kind='update' rows (timeline enrichments proposed)
    phenomenon_count: int              # kind='phenomenon_member' rows
    degraded: bool                     # True if any source was skipped
    infra_error: str | None            # set if the pass aborted on a DB/infrastructure failure
    notes: list[str] = field(default_factory=list)
