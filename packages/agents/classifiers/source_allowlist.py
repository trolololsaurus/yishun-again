"""
Source allowlist — guardrail #2 enforcement and unapproved-outlet flagging.

The `sources` table is the operator-approved source universe, but nothing ever
checked a URL against it. Google News RSS aggregates arbitrary publishers, so
outlets nobody approved can become `source_urls` on a published incident —
8days.sg is live on one today.

Three rules, deliberately different in severity:

  signal  (EDMW/HWZ): guardrail #2 — a signal URL must NEVER be a quoted source.
          Removed unconditionally, no operator discretion.

  redirector (news.google.com and friends): removed unconditionally, same as
          signal. A citation must point at the outlet that did the reporting,
          not at a wrapper that stands in front of it. See REDIRECT_DOMAINS.

  unknown/unapproved: NOT removed. Stripping it could take an incident's only
          source and break guardrail #1 (`source_urls` must hold >= 1 URL).
          Flagged instead, so the War Room can surface it and the operator can
          either approve the domain or re-source the story.

Matching is suffix-aware: `cnalifestyle.channelnewsasia.com` matches CNA's
registered `www.channelnewsasia.com`. A bare `www.` prefix is ignored on both
sides. Note this means a subdomain of an approved host is trusted — acceptable,
since the operator approved the parent publisher.
"""

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_cache: dict[str, dict] | None = None


def domain_of(url: str) -> str:
    """Normalised registrable host for a URL ('' when unparseable)."""
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, approved: str) -> bool:
    """True if `host` is the approved host or a subdomain of it."""
    return bool(host) and (host == approved or host.endswith("." + approved))


# ── Canonical URL ────────────────────────────────────────────────────────────
# Tracking parameters that identify HOW a reader arrived, never WHICH article
# they arrived at. Two URLs differing only by these are the same page.
#
# This exists because they were counted as separate sources in production.
# `yishun-python-escapes-drain-worksite-aug-2026` published with
# "⚡2 sources" while holding ONE Stomp article twice:
#
#   .../workers-yishun-worksite-uncover-slithery-surprise-later-vanishes-drain?ref=home-editors-picks
#   .../workers-yishun-worksite-uncover-slithery-surprise-later-vanishes-drain
#
# The count is a `Set` over source_urls, and those two strings are not equal, so
# a single report was advertised to readers as corroboration by two. Dedup
# missed it for the same reason — `dedup.is_duplicate` matched on the raw URL.
#
# DENYLIST, not an allowlist: a query string can genuinely identify an article
# (?id=, ?storyid=), so stripping everything would merge distinct pages — a far
# worse failure than leaving one duplicate. Only known-inert keys are removed.
_TRACKING_PARAMS = frozenset({
    "ref", "ref_src", "ref_url", "referrer",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "twclid",
    "mc_cid", "mc_eid", "_ga", "cmpid", "cmp", "spm",
    "at_medium", "at_campaign", "oc",
})


def canonical_url(url: str) -> str:
    """
    A comparison key for "is this the same article?".

    Lower-cases the scheme and host, drops a leading `www.`, removes tracking
    parameters, drops the fragment, and strips a trailing slash. Everything else
    — path case, remaining query keys — is preserved, because those can be
    load-bearing.

    Returns the input unchanged if it cannot be parsed: this feeds dedup, and
    failing open (treating an odd URL as distinct) risks a duplicate row, while
    failing closed could silently merge two real sources.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.netloc:
            return url.strip()
        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(kept), ""))
    except Exception as exc:                      # noqa: BLE001 — never break dedup
        logger.debug("canonical_url: could not parse %r: %s", url[:80], exc)
        return url.strip()


def same_article(a: str, b: str) -> bool:
    """True if two URLs point at the same article ignoring tracking noise."""
    return bool(a) and bool(b) and canonical_url(a) == canonical_url(b)


def dedupe_urls(urls: list[str]) -> list[str]:
    """Drop URLs that are the same article as an earlier one. Order preserved,
    and the FIRST spelling of each article is the one kept."""
    seen: set[str] = set()
    out: list[str] = []
    for u in urls or []:
        if not u:
            continue
        key = canonical_url(u)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def collapse_same_article(urls: list[str], fetch) -> tuple[list[str], list[str]]:
    """
    Collapse URLs that are the SAME article under different slugs, using each
    page's own `<link rel="canonical">`.

    `dedupe_urls` only removes tracking-parameter variants — the paths must
    match. But one publisher can serve one story at two slugs: AsiaOne carried
    the Yishun coffee-shop fire at both `/yishun-food-stall-free-buffet-fire-...`
    and `/yishun-food-stall-buffet-fire-...`, and the two entered as separate
    sources because the listing scraper and the sitemap adapter each linked a
    different one. URL-only logic cannot see they are one article; the publisher
    can, and both pages declare the SAME rel=canonical.

    Returns (kept, dropped), first spelling of each canonical kept, order
    preserved.

    `fetch(url) -> str` returns the article HTML (''/None on failure) and is
    INJECTED so this stays pure and unit-testable — no network in tests, and no
    import cycle with fetch_strategy. It is only ever called for URLs that share
    a host with another URL in the list, so a story with all-distinct hosts
    (the common case) costs zero fetches.
    """
    urls = [u for u in (urls or []) if u]
    if len(urls) < 2:
        return list(urls), []

    hosts: dict[str, int] = {}
    for u in urls:
        hosts[domain_of(u)] = hosts.get(domain_of(u), 0) + 1

    kept, dropped, seen_canon = [], [], {}
    for u in urls:
        # Unique host in this list can't be a same-publisher slug dupe: keep it
        # without spending a fetch.
        if hosts.get(domain_of(u), 0) < 2:
            kept.append(u)
            continue
        declared = u
        try:
            html = fetch(u) or ""
            m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
                          html, re.IGNORECASE)
            if m and m.group(1).startswith("http"):
                declared = m.group(1).strip()
        except Exception as exc:                  # noqa: BLE001 — never fail the row
            logger.debug("collapse_same_article: canonical fetch failed for %s: %s",
                         u[:80], exc)
        key = canonical_url(declared)
        if key in seen_canon:
            dropped.append(u)
            logger.info("collapse_same_article: %s is the same article as %s (canonical %s)",
                        domain_of(u), domain_of(seen_canon[key]), key[:80])
            continue
        seen_canon[key] = u
        kept.append(u)
    return kept, dropped


def load_source_domains(client=None) -> dict[str, dict]:
    """
    {normalised_domain: {"type": str, "approved": bool, "name": str}} from the
    `sources` table, cached for the life of the process (a run reads it once).

    Returns {} if the table can't be read — callers then treat everything as
    unknown, which flags rather than drops, so a DB hiccup can never silently
    strip sources.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        if client is None:
            from classifiers.corroboration import get_supabase_client
            client = get_supabase_client()
        rows = client.table("sources").select("name,url,type,approved_by_operator").execute().data or []
    except Exception as exc:
        logger.warning("source_allowlist: could not load sources table (%s) — treating all as unknown", exc)
        return {}
    _cache = {
        d: {"type": r.get("type", ""), "approved": bool(r.get("approved_by_operator")), "name": r.get("name", "")}
        for r in rows
        if (d := domain_of(r.get("url", "")))
    }
    return _cache


# ── Redirectors / aggregators ────────────────────────────────────────────────
# A URL on one of these hosts is a WRAPPER, not an article. It must never be
# stored as a citation, and it must never be used for dedupe.
#
# This exists because google_news_rss did exactly that in production. Its feed
# entries link to `news.google.com/rss/articles/CBMi<blob>`, which does not
# HTTP-redirect — decoding one needs a reverse-engineered `batchexecute` RPC
# that Google rotates. When that resolver failed it fell back to returning the
# wrapper, and the wrapper was then written to `war_room_queue.source_url` and
# into `source_urls`. Two rows on 2026-08-01 cited an opaque Google redirect
# instead of the Stomp article the reporting actually came from.
#
# The source that produced them was removed on 2026-08-02 (replaced by
# ingestion/sources/news_sitemap.py + wp_search.py, which read publishers'
# own sitemaps and search feeds). This check is the net under that: the
# historical backfill and source-discovery paths still touch Google News, so
# the rule is enforced at the point where a URL becomes a citation rather than
# trusted to hold at every call site.
#
# Matching is suffix-aware like the approved list, so `rss.news.google.com`
# is caught too. Add link shorteners here as they appear — the test is
# "does this host serve journalism, or point at someone who does".
REDIRECT_DOMAINS = frozenset({
    "news.google.com",
    "google.com",            # /url? and /search redirects
    "feedproxy.google.com",
    "news.url.google.com",
    "t.co",
    "bit.ly",
    "tinyurl.com",
    "ow.ly",
    "buff.ly",
    "lnkd.in",
    "flip.it",
    "apple.news",
})


def is_redirect_domain(url: str) -> bool:
    """True if `url` points at an aggregator/redirect wrapper, not a publisher."""
    host = domain_of(url)
    return any(_matches(host, d) for d in REDIRECT_DOMAINS)


def classify(url: str, domains: dict[str, dict] | None = None) -> str:
    """Return 'redirect' | 'signal' | 'approved' | 'unapproved' for a single URL.

    'redirect' is checked FIRST and does not consult the sources table: a
    wrapper host is disqualified on its own terms, and must stay disqualified
    even if someone adds news.google.com to `sources` by mistake.
    """
    if is_redirect_domain(url):
        return "redirect"
    domains = load_source_domains() if domains is None else domains
    host = domain_of(url)
    for approved_domain, meta in domains.items():
        if _matches(host, approved_domain):
            if meta["type"] == "signal":
                return "signal"
            return "approved" if meta["approved"] else "unapproved"
    return "unapproved"


# QA M14 vocab drift. Two spellings existed for one concept: the `sources` table
# CHECK and scrape_edmw say 'signal'; Candidate's contract and the orchestrator
# said 'edmw'. That mismatch silently breached guardrail #2.
#
# 'signal' is CANONICAL — it is the only spelling the database accepts
# (sources.type CHECK) and what the scraper emits. 'edmw' is a tolerated legacy
# alias: candidates are normalised to 'signal' at the adapter boundary, and
# is_signal_source still accepts both so no single component can reintroduce the
# breach by comparing the wrong string.
CANONICAL_SIGNAL_TYPE = "signal"
SIGNAL_TYPES = frozenset({CANONICAL_SIGNAL_TYPE, "edmw"})


def canonical_source_type(source_type: str | None) -> str:
    """
    Normalise a source_type to the canonical vocabulary (QA M14).

    Any signal spelling collapses to 'signal'; everything else is passed through
    lower-cased. Applied at the Source-adapter boundary so no Candidate ever
    carries the legacy alias downstream.
    """
    st = (source_type or "").strip().lower()
    return CANONICAL_SIGNAL_TYPE if st in SIGNAL_TYPES else st


def is_signal_source(source_type: str | None, url: str = "", domains: dict[str, dict] | None = None) -> bool:
    """
    True if this candidate is forum/signal material, whose URL may NEVER become a
    quoted source (guardrail #2).

    Deliberately belt-and-braces: a legal guardrail should not depend on one
    string comparison matching. Checks the declared type under BOTH vocabularies
    AND resolves the URL's domain against the sources table, so a mislabelled
    candidate from a known signal domain is still caught.

    This exact mismatch was live: scrape_edmw emits 'signal', the orchestrator
    tested == 'edmw', so an EDMW URL would have been written into source_urls.
    """
    if (source_type or "").strip().lower() in SIGNAL_TYPES:
        return True
    return bool(url) and classify(url, domains) == "signal"


def check_source_urls(urls: list[str], domains: dict[str, dict] | None = None) -> dict:
    """
    Apply the allowlist to a candidate's source_urls.

    Returns {"kept": [...], "dropped_signal": [...], "dropped_redirect": [...],
             "unapproved": [...]}.

    `kept` preserves order and drops signal and redirector URLs. Unapproved URLs
    stay in `kept` AND are listed in `unapproved` for operator review — see the
    module docstring for why they are not removed.

    Dropping can empty `kept`. That is intentional and is NOT special-cased
    here: a candidate whose only citation was a wrapper has no verifiable
    source, which is precisely the state guardrail #1 exists to catch. It lands
    in the queue as unverified, exactly like a signal-only candidate, and waits
    for an operator to attach a real one.
    """
    domains = load_source_domains() if domains is None else domains
    kept, dropped_signal, dropped_redirect, unapproved = [], [], [], []
    for url in urls or []:
        if not url:
            continue
        verdict = classify(url, domains)
        if verdict == "redirect":
            dropped_redirect.append(url)
            continue
        if verdict == "signal":
            dropped_signal.append(url)
            continue
        if verdict == "unapproved":
            unapproved.append(url)
        kept.append(url)

    if dropped_signal:
        logger.warning(
            "source_allowlist: removed %d signal URL(s) from source_urls (guardrail #2): %s",
            len(dropped_signal), ", ".join(domain_of(u) for u in dropped_signal),
        )
    if dropped_redirect:
        logger.warning(
            "source_allowlist: removed %d redirector URL(s) from source_urls — "
            "a citation must point at the publisher, not a wrapper: %s",
            len(dropped_redirect), ", ".join(domain_of(u) for u in dropped_redirect),
        )
    if unapproved:
        logger.info(
            "source_allowlist: %d source URL(s) from unapproved domain(s): %s",
            len(unapproved), ", ".join(sorted({domain_of(u) for u in unapproved})),
        )
    return {"kept": kept, "dropped_signal": dropped_signal,
            "dropped_redirect": dropped_redirect, "unapproved": unapproved}
