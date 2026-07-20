"""
Adapter over the legacy `scrapers.scrape_*` modules (INGESTION_DESIGN.md §3.2).

Those scrapers predate the Source protocol: each exposes a bare
`scrape() -> list[dict]` yielding
`{title, content, url, source_name, source_type, published_at}`. Rather than
duplicate a near-identical wrapper per source, this adapter maps that shape to
`Candidate` and is instantiated once per scraper in this package's `__init__`.

The scrapers now RAISE on a source-level failure (`ScraperError` /
`ScraperBlocked`) instead of logging and returning `[]`, so this adapter can
tell "the source is broken or blocking us" from "no Yishun items right now" and
map it onto the Source protocol's SourceBlockedError / SourceUnavailableError.
FallbackLadder and the run report already act on those.

That distinction is not academic: Stomp's search endpoint moved, every run
logged "skipping run" and returned zero, and nothing surfaced it until someone
looked. Per-ARTICLE failures still degrade quietly (one bad article must not
kill a run) — only source-level failures raise.

`published_at` is passed through untouched. A source that cannot supply a date
yields None, and RecencyFilter (§5.1) routes it to review rather than dropping
it (Q2=2b). Never infer a date here — Candidate's contract forbids it.
"""

import logging
from datetime import date
from typing import Callable

from ingestion.contracts import (
    Candidate,
    SourceBlockedError,
    SourceUnavailableError,
)
from classifiers.source_allowlist import canonical_source_type
from scrapers import ScraperBlocked, ScraperError

logger = logging.getLogger(__name__)


class LegacyScraperSource:
    """Wraps one legacy `scrapers.scrape_*` module as a Source (§3.2)."""

    def __init__(
        self,
        name: str,
        scrape: Callable[[], list[dict]],
        *,
        source_name: str | None = None,
        source_type: str = "msm",
        enabled: bool = True,
    ) -> None:
        self.name = name              # stable id — keys the pipeline_state watermark
        self.enabled = enabled
        self._scrape = scrape
        self._source_name = source_name
        self._source_type = source_type

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        `since` is accepted for protocol conformance but unused — these scrapers
        poll a current feed and have no date-range query. RecencyFilter (§5.1)
        applies the real window downstream.

        Raises SourceBlockedError / SourceUnavailableError per the Source
        protocol; an empty list now genuinely means "no Yishun items", not
        "something broke and we swallowed it".
        """
        try:
            items = self._scrape()
        except ScraperBlocked as exc:
            raise SourceBlockedError(f"{self.name}: {exc}") from exc
        except ScraperError as exc:
            raise SourceUnavailableError(f"{self.name}: {exc}") from exc
        except Exception as exc:
            # A scraper that fails in a way it didn't classify is still a source
            # failure — surface it rather than reporting a silent empty run.
            raise SourceUnavailableError(f"{self.name}: {type(exc).__name__}: {exc}") from exc

        candidates: list[Candidate] = []
        for item in items:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            candidates.append(Candidate(
                title=item.get("title", ""),
                content=item.get("content", ""),
                url=url,
                source_name=item.get("source_name") or self._source_name or self.name,
                # Normalised here so the legacy 'edmw' spelling never reaches
                # downstream code — 'signal' is canonical (QA M14).
                source_type=canonical_source_type(item.get("source_type") or self._source_type),
                published_at=item.get("published_at"),
                discovered_via=self.name,
            ))

        # A dateless candidate bypasses the recency watermark, is re-processed by
        # Stage 1/2 on every pass, and cannot be approved until an operator sets
        # the date by hand (QA H3). Surface it rather than letting it be silent.
        dateless = sum(1 for c in candidates if c.published_at is None)
        if dateless:
            logger.warning(
                "%s: %d/%d candidates have no published_at — these bypass the "
                "recency watermark and need a manual date before approval",
                self.name, dateless, len(candidates),
            )

        return candidates
