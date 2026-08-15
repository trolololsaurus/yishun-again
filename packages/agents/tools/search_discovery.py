"""
Search-driven historical discovery — "search <topic> <year>" as a pipeline.

WHY THIS EXISTS
---------------
The live pass is recency-gated: it hunts NEW Yishun news off each outlet's feed,
sitemap and (for WordPress sites) search feed. It is not a historical crawler, so
it structurally cannot surface:

  * stories on domains we do not scrape (a one-off blog, a small local outlet), or
  * old stories that have scrolled out of every feed/sitemap window
    (Mothership has no news sitemap and ignores ?s=; Stomp's sitemap is ~17 rows).

Both are the BACKFILL gap. This tool closes it the way a human would: run a web
search for "yishun <year>", collect the PUBLISHER links, and hand them to the
existing seed backfill (seed_backfill.py), which fetches, drafts and queues them
for operator review exactly like any other source.

It is deliberately NOT a daily Source. Topic x year is static history — running it
on every 14:58 pass would re-buy the same queries daily for no new signal. Run it
occasionally, review the manifest, then feed it to seed_backfill.

WHY DUCKDUCKGO, AND WHY KEYLESS
-------------------------------
The paid/free search-API landscape collapsed in 2025-26: Google's Custom Search
JSON API closed to new signups and dies 2027-01-01 (and dropped "search the whole
web" for new engines); Microsoft retired every Bing Search API on 2025-08-11;
Brave/Tavily/Serper all now require a card on file. There is no free whole-web
search API left for new users.

DuckDuckGo's HTML endpoint (html.duckduckgo.com/html) still answers a plain
query with real, DIRECT publisher links and needs no key, no signup, no cost —
which is all a run-once, low-volume backfill sweep needs. We parse it with bs4
(already a dependency) so there is no new pip requirement either.

Two hazards this handles:
  * DDG sometimes returns its own `duckduckgo.com/l/?uddg=<real-url>` redirect
    wrapper instead of the target. `_ddg_result_url` DECODES it back to the
    publisher URL — storing the wrapper would reintroduce exactly the redirect
    problem that got Google News RSS removed on 2026-08-02.
  * Search results are full of social/video noise (Facebook, YouTube, HWZ …).
    `filter_links` drops those, drops redirect wrappers (`is_redirect_domain`),
    and keeps only links that actually mention a Yishun keyword. The authoritative
    source_allowlist still runs downstream in process_candidates.

If DDG ever rate-limits too hard for a big sweep, swap `search()` for a paid
backend (Brave/Tavily) — nothing else in this file changes.

USAGE (from packages/agents/)
-----------------------------
    .venv/Scripts/python.exe tools/search_discovery.py --years 2015-2026 --out seeds.json
    .venv/Scripts/python.exe tools/search_discovery.py --topics yishun --years 2018 --out seeds.json
    # then review seeds.json and:
    .venv/Scripts/python.exe seed_backfill.py --manifest seeds.json --route queue --dry-run
"""
import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Reuse the town keyword filter and the redirect-wrapper net — never re-implement.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bs4 import BeautifulSoup  # noqa: E402  (already a project dependency)
from scrapers import content_matches_keywords  # noqa: E402
from classifiers.source_allowlist import is_redirect_domain  # noqa: E402

logger = logging.getLogger(__name__)

DDG_HTML = "https://html.duckduckgo.com/html/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REQUEST_TIMEOUT = 25

# Hosts that are never a citable news source: forums/UGC (guardrail #2) and
# social/video/aggregator noise a web search inevitably returns. The authoritative
# allowlist runs downstream; this is the cheap pre-drop so they never reach the
# manifest an operator reviews.
_DROP_HOSTS = (
    # forums / UGC (guardrail #2) and social/video noise
    "reddit.com", "hardwarezone.com.sg", "hwz.com.sg",
    "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "tiktok.com", "pinterest.com", "linkedin.com",
    "quora.com", "duckduckgo.com",
    # hosts that STRUCTURALLY never carry a Yishun incident report but do contain
    # the town name — reference, exam papers, property portals, gov archives. Not
    # exhaustive: Stage 1 is the real precision gate, this just spares it the
    # obvious junk a whole-web search always returns.
    # ponytail: static denylist, not a classifier — extend when a new junk host recurs.
    "wikipedia.org", "everybodywiki.com", ".gov.sg",
    "sgtestpaper.com", "testpapersfree.com", "btohq.com", "99.co",
    "frasersproperty.com", "propertyguru.com.sg",
)
# Default topics = the Yishun place terms that stand alone. A search ranks on
# relevance, so "yishun" carries most of it; the subzone terms catch stories
# that name only Khatib / Chong Pang.
DEFAULT_TOPICS = ["yishun", "khatib", "chong pang"]


def parse_years(spec: str) -> list[int]:
    """'2017-2020' -> [2017..2020]; '2018,2020' -> [2018,2020]; '2019' -> [2019]."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def build_queries(topics: list[str], years: list[int],
                  sites: list[str] | None) -> list[str]:
    """
    -> list of query strings, one per (topic, year[, site]). DDG has no exact
    date-range sort, so the year is a query term; it biases ranking toward that
    year's coverage, and seed_backfill re-dates each article from its own page.
    """
    queries: list[str] = []
    site_list = sites or [None]
    for topic in topics:
        for year in years:
            for site in site_list:
                q = f"{topic} {year}"
                if site:
                    q += f" site:{site}"
                queries.append(q)
    return queries


def _ddg_result_url(href: str) -> str:
    """Decode DDG's `//duckduckgo.com/l/?uddg=<real url>` redirect back to the
    publisher URL. A direct href is returned unchanged."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    p = urllib.parse.urlparse(href)
    if "duckduckgo.com" in p.netloc and p.path.startswith("/l/"):
        return (urllib.parse.parse_qs(p.query).get("uddg") or [""])[0]
    return href


def _fetch_html(query: str, attempts: int = 3) -> str:
    """
    POST one DDG query -> HTML, with a bounded backoff-retry on throttling.
    DDG answers a rate-limited request with HTTP 202 (a "slow down" challenge,
    not an error urlopen raises) or 429; both self-heal after a pause. Returns ""
    when throttling persists across `attempts` so the sweep skips the query
    rather than aborting.
    ponytail: bounded retry, known ceiling — sustained IP throttling (a
    datacenter/cloud IP) still fails; run from a residential IP or a paid backend.
    """
    data = urllib.parse.urlencode({"q": query}).encode()
    for attempt in range(attempts):
        req = urllib.request.Request(DDG_HTML, data=data, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                if r.status == 200:
                    return r.read().decode("utf-8", "ignore")
                status = r.status                  # 202 challenge
        except urllib.error.HTTPError as e:
            status = e.code                        # 429 etc
        except Exception as e:                     # transport / timeout
            logger.warning("DDG error for %r: %s", query, e)
            return ""
        if attempt < attempts - 1:
            wait = 5 * (attempt + 1)
            logger.info("DDG %s on %r — backoff %ss (attempt %d/%d)",
                        status, query, wait, attempt + 1, attempts)
            time.sleep(wait)
    logger.warning("DDG throttled on %r after %d attempts", query, attempts)
    return ""


def search(query: str) -> list[dict]:
    """
    One keyless DDG HTML query -> [{link, title, snippet}]. Network. A throttled
    or failed query returns [] (a backfill sweep skips it, does not abort).
    Politeness: caller spaces queries; _fetch_html backs off on 202/429.
    """
    html = _fetch_html(query)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for res in soup.select("div.result, div.web-result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        link = _ddg_result_url(a.get("href", ""))
        if not link:
            continue
        snip = res.select_one(".result__snippet")
        out.append({
            "link": link,
            "title": a.get_text(" ", strip=True),
            "snippet": snip.get_text(" ", strip=True) if snip else "",
        })
    return out


def filter_links(items: list[dict]) -> list[dict]:
    """
    Pure. Keep publisher links that (a) actually mention a Yishun keyword and
    (b) are not a redirect wrapper, a forum/signal host or social/video noise.
    Dedupe by URL.
    """
    kept: list[dict] = []
    seen: set[str] = set()
    for it in items:
        link = (it.get("link") or "").strip()
        if not link or link in seen:
            continue
        blob = f"{it.get('title', '')} {it.get('snippet', '')} {link}"
        if not content_matches_keywords(blob):
            continue
        if is_redirect_domain(link):
            continue
        low = link.lower()
        if any(h in low for h in _DROP_HOSTS):
            continue
        seen.add(link)
        kept.append({"url": link, "title": (it.get("title") or "").strip()})
    return kept


def to_manifest(links: list[dict], route: str) -> list[dict]:
    """One manifest entry per link (one story per URL). seed_backfill fetches,
    dates and dedupes them; consolidation merges any that are the same event."""
    return [
        {
            "story": lk["title"] or lk["url"],
            "urls": [lk["url"]],
            "category": "",
            "value": "",
            "route": route,
        }
        for lk in links
    ]


def run(topics, years, sites, route, spacing=2.0) -> list[dict]:
    queries = build_queries(topics, years, sites)
    logger.info("%d queries (%d topics x %d years x %d sites)",
                len(queries), len(topics), len(years), len(sites or [None]))
    all_items: list[dict] = []
    for i, q in enumerate(queries):
        got = search(q)
        logger.info("  %-32s -> %d raw", q, len(got))
        all_items.extend(got)
        if i < len(queries) - 1:
            time.sleep(spacing)          # be polite to DDG across the sweep
    links = filter_links(all_items)
    logger.info("%d raw results -> %d Yishun publisher links (deduped)",
                len(all_items), len(links))
    return to_manifest(links, route)


def main():
    ap = argparse.ArgumentParser(description="Keyless search backfill discovery (DuckDuckGo).")
    ap.add_argument("--topics", default=",".join(DEFAULT_TOPICS),
                    help="Comma-separated search topics (default: Yishun place terms).")
    ap.add_argument("--years", required=True, help="e.g. 2015-2026 or 2018,2020 or 2019")
    ap.add_argument("--sites", default="", help="Optional comma-separated site: restrictions "
                    "(e.g. mothership.sg,stomp.sg). Blank = whole web.")
    ap.add_argument("--route", choices=["queue", "auto", "consolidate"], default="queue",
                    help="seed_backfill route for every entry (default queue = operator review).")
    ap.add_argument("--spacing", type=float, default=2.0,
                    help="Seconds between queries (DDG politeness; raise if throttled).")
    ap.add_argument("--out", required=True, help="Manifest JSON to write for seed_backfill.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    years = parse_years(args.years)
    sites = [s.strip() for s in args.sites.split(",") if s.strip()] or None

    manifest = run(topics, years, sites, args.route, spacing=args.spacing)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(manifest)} entries -> {args.out}")
    print("Review it, then:")
    print(f"  .venv/Scripts/python.exe seed_backfill.py --manifest {args.out} "
          f"--route {args.route} --dry-run")


if __name__ == "__main__":
    main()
