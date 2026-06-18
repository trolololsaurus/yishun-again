"""
CNA source adapter (INGESTION_DESIGN.md §3.2, §3.3) — PRIMARY (MSM) role,
Q1=1b.

Thin wrapper around scrapers.scrape_cna.scrape() — does not reimplement its
RSS fetch/parse/filter logic, only adapts its output to Candidate (§3.1).

Known v1 limitation: scrape_cna.scrape() catches its own per-feed exceptions
and logs+returns [] rather than raising — it was written for scrape_all()'s
best-effort aggregation, not the Source protocol's "MUST raise
SourceBlockedError/SourceUnavailableError, MUST NOT swallow" contract. This
adapter cannot currently distinguish "CNA blocked us" from "no new Yishun
items in CNA's feed right now" — both surface as an empty fetch with
status='ok'. Revisit if CNA blocking becomes observable in practice.
"""

import logging
from datetime import date

from ingestion.contracts import Candidate
from scrapers import scrape_cna

logger = logging.getLogger(__name__)


class CNASource:
    """PRIMARY-role source (§3.3, Q1=1b) — Channel NewsAsia RSS."""

    name = "cna"
    enabled = True

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        `since` is accepted for Source protocol conformance but not used —
        scrape_cna.scrape() has no date-range query (CNA's RSS feed is the
        current feed only). RecencyFilter (§5.1) applies the real window
        downstream.
        """
        items = scrape_cna.scrape()
        return [
            Candidate(
                title=item["title"],
                content=item["content"],
                url=item["url"],
                source_name="Channel NewsAsia",
                source_type="msm",
                published_at=item.get("published_at"),
                discovered_via="cna",
            )
            for item in items
        ]
