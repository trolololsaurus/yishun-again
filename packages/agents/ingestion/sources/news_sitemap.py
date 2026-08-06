"""
Google-News sitemap source adapter — DISCOVERY role.

WHY THIS EXISTS
---------------
This replaces `google_news_rss`, which was removed on 2026-08-02.

Google News RSS was the dominant discovery channel, and it was the wrong one.
Its entries link to `https://news.google.com/rss/articles/CBMi<blob>` wrappers,
not to publishers. Those wrappers do not HTTP-redirect — resolving one needs a
reverse-engineered `batchexecute` RPC that Google rotates periodically — so
when the resolver failed it degraded to storing the WRAPPER as the article URL.
That breaks three things at once:

  1. `Candidate.url` is contractually "the canonical article URL, not a
     wrapper" (contracts.py §3.1), because dedupe correctness depends on it.
     A wrapper URL matches nothing, so `dedup.is_duplicate` cannot see that we
     already hold the story.
  2. The wrapper then lands in `war_room_queue.source_url` and, worse, in
     `source_urls` — a published incident citing an opaque Google redirect
     instead of the outlet that did the reporting.
  3. `source_allowlist` cannot classify news.google.com, so every such row was
     flagged `unapproved_source_domain` and held back from auto-publish.

All three fired in production on 2026-08-01: two queue rows proposing "updates"
to an incident we already had, each citing an unresolved wrapper, each a
duplicate of a Stomp article the Stomp scraper had already ingested cleanly the
day before.

A publisher's own Google-News sitemap gives the same discovery reach with none
of that. It is a static XML file the publisher maintains for search engines:
every entry carries the CANONICAL article URL, its real publication date, and
usually its headline. No redirects, no RPC, no rotation risk, no third party.

It is also a much wider window than the front-page RSS feeds the primary
scrapers read. Measured 2026-08-02:

    source            RSS feed entries    news sitemap entries
    straits_times     44                  462
    zaobao            (HTML listing)      366
    yahoo             5                   204
    tamil_murasu      (HTML listing)      113
    cna               33                  50
    asiaone           (HTML listing)      50
    berita_harian     (HTML listing)      39
    stomp             (HTML listing)      17

The Straits Times feed carried zero Yishun items that morning; its sitemap
carried "Refreshed heritage trail in Yishun offers two routes for exploration".
A once-a-day pass against a 44-entry feed simply cannot see a story that
scrolled out of the window, and that — not a broken scraper — is why the fleet
kept reporting zeros.

WHAT THIS SOURCE DOES NOT DO
----------------------------
It does not replace the per-source scrapers. They stay: they read the current
feed/listing and carry article summaries, which is the cheap path for anything
published in the last few hours. This source is the wider, slower net behind
them, and the two dedupe against each other downstream on the canonical URL —
which works precisely because both now emit canonical URLs.

Sitemaps carry no article body, only a headline. So for the handful of entries
that match the Yishun keywords (0-3 a day across every publisher combined) the
article itself is fetched to give Stage 1/2 something to read. Recency is
applied BEFORE that fetch — the lesson google_news_rss taught the hard way,
where resolving first burned ~600 round-trips a pass on entries the recency
filter discarded seconds later.
"""

import gzip
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime

from bs4 import BeautifulSoup

from ingestion.contracts import Candidate, SourceBlockedError, SourceUnavailableError
from scrapers import BROWSER_HEADERS, content_matches_keywords

logger = logging.getLogger(__name__)

_NS = {
    "sm":   "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

REQUEST_TIMEOUT = 25
_SITEMAP_READ_CAP = 8_000_000     # ST's is ~1.5 MB; leave headroom
_ARTICLE_READ_CAP = 400_000
_CONTENT_LIMIT = 3_000

# Safety valve, not an expected path. Keyword matches run 0-3/day across the
# whole fleet; this only binds if a sitemap balloons or a keyword goes generic,
# and it stops one source from eating the pass deadline in article fetches.
MAX_ARTICLE_FETCHES = 15

# Same bot-detection vocabulary the scrapers use.
_BLOCK_MARKERS = (
    "unusual traffic", "/sorry/", "captcha", "recaptcha",
    "detected unusual", "automated queries", "not a robot",
)


# ── Pure parse helpers (unit-tested offline) ─────────────────────────────────

def _title_from_slug(url: str) -> str:
    """Derive a readable headline from an article URL's last path segment.

    Not every sitemap entry carries <news:title> — CNA's had 35 titles across
    50 entries on 2026-08-02 — and an entry with no title must still be usable,
    because the keyword match and Stage 1 both read it.
    """
    slug = re.sub(r"[?#].*$", "", url or "").rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(html?|php|aspx)$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"-\d{6,}$", "", slug)          # trailing article ids
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()


def _parse_pub_date(raw: str | None) -> date | None:
    """Parse a <news:publication_date> value (ISO 8601, usually with offset)."""
    if not raw:
        return None
    text = raw.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_sitemap(xml_bytes: bytes) -> list[tuple[str, str, date | None]]:
    """Return [(url, title, published_at)] from Google-News sitemap XML.

    Namespace-tolerant: a few SG publishers emit the news elements without
    declaring the namespace, so each field is looked up namespaced first and
    then bare. A single malformed <url> block is skipped, never fatal — one bad
    entry must not cost the other 400.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SourceUnavailableError(f"sitemap parse failed: {exc}") from exc

    def _text(node, *paths):
        for p in paths:
            try:
                found = node.find(p, _NS)
            except SyntaxError:
                continue
            if found is not None and (found.text or "").strip():
                return found.text.strip()
        return None

    out: list[tuple[str, str, date | None]] = []
    entries = root.findall("sm:url", _NS) or root.findall("url")
    for entry in entries:
        loc = _text(entry, "sm:loc", "loc")
        if not loc:
            continue
        title = _text(entry, "news:news/news:title", "news/title", "sm:title", "title")
        published = _parse_pub_date(
            _text(entry, "news:news/news:publication_date", "news/publication_date",
                  "sm:lastmod", "lastmod")
        )
        out.append((loc, title or _title_from_slug(loc), published))
    return out


def _extract_body(html: str) -> str:
    """Best-effort article body text. Returns '' rather than raising."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        return " ".join(p for p in paras if len(p) > 30)[:_CONTENT_LIMIT]
    except Exception as exc:                      # noqa: BLE001 — body is optional
        logger.debug("news_sitemap: body extract failed: %s", exc)
        return ""


# ── Source ───────────────────────────────────────────────────────────────────

class NewsSitemapSource:
    """DISCOVERY-role source over one publisher's Google-News sitemap.

    Emits `source_type='msm'` and the publisher's own canonical URL, so an item
    found here is indistinguishable downstream from one the publisher's own
    scraper found — which is the point. Dedupe collapses the overlap.
    """

    enabled = True
    source_type = "msm"

    def __init__(self, name: str, source_name: str, sitemap_url: str, *,
                 enabled: bool = True):
        self.name = name
        self.source_name = source_name
        self.sitemap_url = sitemap_url
        self.enabled = enabled

    def _get(self, url: str, cap: int) -> bytes:
        # polite_get shares the per-host spacing, the 403 back-off and the
        # per-pass cache with every other publisher request. This adapter is the
        # heaviest fetcher in the pass — one sitemap plus one request per
        # keyword match — and fetching directly made it invisible to the
        # throttle, which is how a burst got the datacenter IP refused by every
        # SPH property at once on 2026-08-05.
        from scrapers.fetch_strategy import polite_get
        status, body = polite_get(url, timeout=REQUEST_TIMEOUT, cap=cap)
        if status in (403, 429):
            raise SourceBlockedError(f"{self.name}: HTTP {status} for {url}")
        if status == 0:
            raise SourceUnavailableError(f"{self.name}: fetch failed for {url}")
        if status != 200:
            raise SourceUnavailableError(f"{self.name}: HTTP {status} for {url}")

        # Some publishers (Yahoo) serve the sitemap as a gzipped FILE rather
        # than with Content-Encoding: gzip, so urllib hands back the raw
        # deflate stream. Sniff the magic bytes instead of trusting headers.
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except Exception as exc:
                raise SourceUnavailableError(
                    f"{self.name}: gzip decompress failed for {url}") from exc

        snippet = body[:4000].decode("utf-8", errors="ignore").lower()
        for marker in _BLOCK_MARKERS:
            if marker in snippet:
                raise SourceBlockedError(f"{self.name}: block-page marker {marker!r}")
        return body

    def fetch(self, since: date | None) -> list[Candidate]:
        """
        Read the sitemap, keep Yishun-matching entries newer than `since`, then
        fetch a body for each survivor.

        `since` is advisory (the orchestrator re-applies RecencyFilter), but it
        is applied here because it gates the only expensive step: entries at or
        below the watermark are dropped BEFORE any article fetch. Dateless
        entries are never dropped here — routing them to review rather than
        deleting them is deliberate (INGESTION_DESIGN §5.1).
        """
        raw = self._get(self.sitemap_url, _SITEMAP_READ_CAP)
        entries = parse_sitemap(raw)
        if not entries:
            raise SourceUnavailableError(
                f"{self.name}: sitemap parsed to 0 entries ({self.sitemap_url})")

        candidates: list[Candidate] = []
        seen: set[str] = set()
        matched = skipped_stale = fetch_failures = 0

        for url, title, published_at in entries:
            if url in seen:
                continue
            # Match on headline AND slug: SG outlets almost always put the town
            # in the slug, and it is the only signal when <news:title> is absent.
            if not content_matches_keywords(f"{title} {url}"):
                continue
            matched += 1

            if since is not None and published_at is not None and published_at <= since:
                skipped_stale += 1
                continue

            if len(candidates) >= MAX_ARTICLE_FETCHES:
                logger.warning(
                    "%s: hit MAX_ARTICLE_FETCHES=%d — remaining matches left for "
                    "the next pass (watermark will not advance past them)",
                    self.name, MAX_ARTICLE_FETCHES)
                break

            seen.add(url)
            try:
                html = self._get(url, _ARTICLE_READ_CAP).decode("utf-8", errors="ignore")
                content = _extract_body(html)
            except (SourceBlockedError, SourceUnavailableError) as exc:
                # One unreachable article must not blank the source. The
                # headline alone still carries the story to Stage 1, and losing
                # the candidate entirely would look like "no Yishun news".
                fetch_failures += 1
                logger.warning("%s: article fetch failed for %s (%s) — "
                               "keeping headline-only candidate", self.name, url[:90], exc)
                content = ""

            candidates.append(Candidate(
                title=title,
                content=content or title,
                url=url,
                source_name=self.source_name,
                source_type="msm",
                published_at=published_at,
                discovered_via=self.name,
            ))

        logger.info(
            "%s: %d sitemap entries, %d keyword match(es), %d stale, "
            "%d article fetch failure(s) -> %d candidate(s)",
            self.name, len(entries), matched, skipped_stale,
            fetch_failures, len(candidates))
        return candidates


# ── Registry ─────────────────────────────────────────────────────────────────
# Every URL below was verified live on 2026-08-02 (HTTP 200, parses, carries
# <news:publication_date>). Each is the publisher's OWN sitemap on the
# publisher's OWN domain — that is the whole point, so do not add an
# aggregator, a redirect wrapper, or anything on news.google.com here.
#
# Not represented, and why:
#   mothership       — no news sitemap; /sitemap.xml just re-serves /feed/ and
#                      ?paged=N returns the same 10 entries. Front-page feed is
#                      the ceiling for this publisher.
#   mustsharenews    — no news sitemap, but WordPress search works: see
#                      wp_search.py.
#   the_independent  — news sitemap lives at /sitemap-news.xml (its robots.txt
#                      advertises /news-sitemap.xml, which 404s). Also covered
#                      by wp_search.py.
#   shinmin          — serves no robots.txt and no sitemap; HTML listing only.

NEWS_SITEMAPS: list[tuple[str, str, str]] = [
    ("cna_sitemap", "Channel NewsAsia",
     "https://www.channelnewsasia.com/api/v1/sitemap-news-feed?_format=xml"),
    ("straits_times_sitemap", "The Straits Times",
     "https://www.straitstimes.com/googlenews.xml"),
    # Yahoo serves this pre-gzipped as a FILE (gzip magic bytes, no
    # Content-Encoding header), so _get() gunzips it explicitly — urllib will
    # not, since this is not transfer encoding. The un-suffixed path 404s.
    ("yahoo_sitemap", "Yahoo News Singapore",
     "https://sg.news.yahoo.com/sitemaps/news-sitemap_googlenews_SG_en-SG.xml.gz"),
    ("asiaone_sitemap", "AsiaOne",
     "https://www.asiaone.com/googlenews.xml"),
    ("stomp_sitemap", "Stomp",
     "https://www.stomp.sg/googlenews.xml"),
    ("zaobao_sitemap", "Lianhe Zaobao",
     "https://www.zaobao.com.sg/google-news-sitemap.xml"),
    ("berita_harian_sitemap", "Berita Harian",
     "https://www.beritaharian.sg/googlenews.xml"),
    ("tamil_murasu_sitemap", "Tamil Murasu",
     "https://www.tamilmurasu.com.sg/googlenews.xml"),
    ("the_independent_sitemap", "The Independent Singapore",
     "https://theindependent.sg/sitemap-news.xml"),
]


def news_sitemap_sources() -> list[NewsSitemapSource]:
    return [NewsSitemapSource(n, sn, u) for n, sn, u in NEWS_SITEMAPS]
