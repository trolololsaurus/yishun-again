"""
Shared Google News RSS helpers (INGESTION_DESIGN.md §10b step 10).

Extracted from scrapers/backfill_agent.py so both the deprecation-guarded
backfill agent and ingestion/sources/google_news_rss.py can import them
without pulling in backfill_agent's module-level state.

URL resolution
--------------
Modern Google News RSS items link to

    https://news.google.com/rss/articles/CBMi...<base64url protobuf>...

These do NOT HTTP-redirect — the path blob is an opaque signed token, not the
article URL, so a plain follow_redirects GET lands back on news.google.com and
returns the wrapper unresolved (the old bug). Resolving them requires Google's
`batchexecute` RPC: GET the article page to read the per-article signature
(`data-n-a-sg`) + timestamp (`data-n-a-ts`), then POST a constructed `f.req` to
`/_/DotsSplashUi/data/batchexecute` and parse the publisher URL out of the
response.

This is fragile (Google rotates the format periodically), so EVERY step is
exception-guarded: any failure — network, missing attrs, unexpected response
shape, parse error — degrades to returning `raw_url`. That makes the resolver
strictly no-worse than the previous plain-redirect behaviour and guarantees a
resolver failure can never break an ingestion pass. The pure parse helpers
(`_extract_article_id`, `_parse_decoding_params`, `_parse_batchexecute_url`)
are factored out so they can be unit-tested offline against captured fixtures.
"""

import json
import re
import urllib.parse

import httpx

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Total budget for the two-request batchexecute dance (GET params + POST decode).
_RESOLVE_TIMEOUT = 8.0

_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

# Matches both the modern RSS form (.../rss/articles/<blob>) and the bare
# consumer form (.../articles/<blob>). The blob is base64url (no padding).
_ARTICLE_ID_RE = re.compile(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_\-]+)")


def _gnews_source_name(entry) -> str:
    """Extract outlet name from a Google News RSS entry."""
    source = getattr(entry, "source", None)
    if source and hasattr(source, "title"):
        return str(source.title)
    # Fallback: Google News RSS appends " - Source Name" to the article title
    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return "Google News"


# ── Pure parse helpers (unit-tested offline) ─────────────────────────────────

def _extract_article_id(url: str) -> str | None:
    """Return the base64url article-id blob from a Google News article URL,
    or None if `url` is not a news.google.com /articles/ wrapper."""
    m = _ARTICLE_ID_RE.search(url or "")
    return m.group(1) if m else None


def _parse_decoding_params(html: str) -> tuple[str, str] | None:
    """Extract (signature, timestamp) from the article page's c-wiz div.

    Google renders the article shell with `data-n-a-sg` (signature) and
    `data-n-a-ts` (timestamp) attributes; both are required to sign the
    batchexecute request. Returns None if either is absent.
    """
    sig = re.search(r'data-n-a-sg="([^"]+)"', html or "")
    ts = re.search(r'data-n-a-ts="([^"]+)"', html or "")
    if sig and ts:
        return sig.group(1), ts.group(1)
    return None


def _build_freq(article_id: str, signature: str, timestamp: str) -> str:
    """Construct the `f.req` body for the Fbv4je (garturlreq) RPC."""
    inner = json.dumps([
        "garturlreq",
        [
            ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
             None, None, None, None, None, 0, 1],
            "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
        ],
        article_id,
        int(timestamp),
        signature,
    ])
    return json.dumps([[["Fbv4je", inner, None, "generic"]]])


def _parse_batchexecute_url(response_text: str) -> str | None:
    """Pull the resolved publisher URL out of a batchexecute response.

    The response is the usual `)]}'`-prefixed, double-newline-delimited
    envelope; the data chunk is a list of RPC results where each result's
    third element is a JSON string of the form `["garturlres", "<url>", ...]`.
    Returns None on any structural surprise (Google rotates this format).
    """
    if not response_text:
        return None
    parts = response_text.split("\n\n")
    # The data envelope is the chunk that contains the RPC id; scan defensively
    # rather than hard-coding an index, since the prefix framing can shift.
    for chunk in parts:
        chunk = chunk.strip()
        if "Fbv4je" not in chunk:
            continue
        try:
            rows = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        for row in rows:
            # A data row looks like ["wrb.fr","Fbv4je","[\"garturlres\",...]",...]
            if not isinstance(row, list) or len(row) < 3:
                continue
            if row[1] != "Fbv4je" or not isinstance(row[2], str):
                continue
            try:
                payload = json.loads(row[2])
            except (ValueError, TypeError):
                continue
            if isinstance(payload, list) and len(payload) > 1 \
                    and isinstance(payload[1], str) and payload[1].startswith("http"):
                return payload[1]
    return None


# ── Resolver ─────────────────────────────────────────────────────────────────

def _resolve_via_batchexecute(article_id: str, client: httpx.Client) -> str | None:
    """Run the GET-params → POST-decode dance. Returns the publisher URL or
    None on any failure (caller degrades to raw_url)."""
    page = client.get(f"https://news.google.com/rss/articles/{article_id}")
    page.raise_for_status()
    params = _parse_decoding_params(page.text)
    if not params:
        return None
    signature, timestamp = params

    body = "f.req=" + urllib.parse.quote(_build_freq(article_id, signature, timestamp))
    resp = client.post(
        _BATCHEXECUTE_URL,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    resp.raise_for_status()
    return _parse_batchexecute_url(resp.text)


def _resolve_redirect(raw_url: str) -> str:
    """
    Resolve a Google News wrapper URL to the real publisher article URL.

    Two strategies, both fully guarded — any failure returns `raw_url`, so this
    is never worse than the original plain-redirect behaviour and can never
    break an ingestion pass:

    1. Modern /articles/<blob> wrappers: decode via the batchexecute RPC.
    2. Anything else (legacy consumer-redirect URLs, other wrappers): a plain
       follow_redirects GET, rejecting results that land back on
       news.google.com.
    """
    try:
        article_id = _extract_article_id(raw_url)
        with httpx.Client(
            follow_redirects=True,
            timeout=_RESOLVE_TIMEOUT,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            if article_id:
                resolved = _resolve_via_batchexecute(article_id, client)
                if resolved and "news.google.com" not in resolved:
                    return resolved
                # batchexecute didn't yield a usable URL — fall through to the
                # plain-redirect attempt below as a last resort.

            resp = client.get(raw_url)
            final = str(resp.url)
            return raw_url if "news.google.com" in final else final
    except Exception:
        return raw_url
