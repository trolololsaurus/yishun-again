"""
CNA source adapter (INGESTION_DESIGN.md §3.2, §3.3) — PRIMARY (MSM) role,
Q1=1b.

Thin wrapper around scrapers.scrape_cna.scrape() — does not reimplement its
RSS fetch/parse/filter logic, only adapts its output to Candidate (§3.1).

scrape_cna.scrape() now raises ScraperError/ScraperBlocked on a source-level
failure instead of logging and returning [], so this adapter can honour the
Source protocol's "MUST raise SourceBlockedError/SourceUnavailableError, MUST
NOT swallow" contract. An empty fetch now genuinely means "no new Yishun items
in CNA's feed", not "something broke quietly".

CNA polls several feeds; one feed failing to parse is skipped rather than
failing the whole run — only a source-level failure propagates.
"""

import logging
from datetime import date

from ingestion.contracts import (
    Candidate,
    SourceBlockedError,
    SourceUnavailableError,
)
from scrapers import ScraperBlocked, ScraperError
from scrapers import scrape_cna

logger = logging.getLogger(__name__)


class CNASource:
    """PRIMARY-role source (§3.3, Q1=1b) — Channel NewsAsia RSS."""

    name = "cna"
    enabled = True
    source_type = "msm"

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        `since` is accepted for Source protocol conformance but not used —
        scrape_cna.scrape() has no date-range query (CNA's RSS feed is the
        current feed only). RecencyFilter (§5.1) applies the real window
        downstream.
        """
        try:
            items = scrape_cna.scrape()
        except ScraperBlocked as exc:
            raise SourceBlockedError(f"cna: {exc}") from exc
        except ScraperError as exc:
            raise SourceUnavailableError(f"cna: {exc}") from exc
        except Exception as exc:
            raise SourceUnavailableError(f"cna: {type(exc).__name__}: {exc}") from exc

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
