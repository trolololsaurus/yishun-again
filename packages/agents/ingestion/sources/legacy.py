"""
Adapter over the legacy `scrapers.scrape_*` modules (INGESTION_DESIGN.md §3.2).

Those scrapers predate the Source protocol: each exposes a bare
`scrape() -> list[dict]` yielding
`{title, content, url, source_name, source_type, published_at}`. Rather than
duplicate a near-identical wrapper per source, this adapter maps that shape to
`Candidate` and is instantiated once per scraper in this package's `__init__`.

Known limitation (shared with msm/cna.py): the legacy scrapers catch their own
exceptions and log+return `[]` instead of raising, so this adapter cannot
distinguish "the source blocked us" from "no Yishun items in the feed right
now" — both surface as an empty fetch with status='ok'. Making the scrapers
raise SourceBlockedError/SourceUnavailableError is tracked in issue #23.

`published_at` is passed through untouched. A source that cannot supply a date
yields None, and RecencyFilter (§5.1) routes it to review rather than dropping
it (Q2=2b). Never infer a date here — Candidate's contract forbids it.
"""

import logging
from datetime import date
from typing import Callable

from ingestion.contracts import Candidate

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
        """
        items = self._scrape()

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
                source_type=item.get("source_type") or self._source_type,
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
