"""
Self-contained tests for the Google News URL resolver (no pytest needed).
Run: .venv/Scripts/python.exe test_gnews_resolve.py

Covers the batchexecute decoder in scrapers/_gnews_helpers.py:
  Pure parse helpers (offline, deterministic against captured fixtures):
    1. _extract_article_id on rss/articles, bare articles, and publisher URLs
    2. _parse_decoding_params extracts (sig, ts); None when attrs missing
    3. _parse_batchexecute_url pulls the publisher URL out of the envelope
    4. _parse_batchexecute_url returns None on a rotated/garbage response
  Full _resolve_redirect with httpx mocked (no network):
    5. Modern /articles/ wrapper -> resolved publisher URL
    6. GET params page raises            -> degrades to raw_url
    7. Missing data-n-a-sg/ts             -> degrades to raw_url
    8. batchexecute returns garbage       -> degrades to raw_url
    9. Non-gnews wrapper that redirects    -> followed final URL
   10. Redirect that lands back on g.news -> degrades to raw_url
"""
from unittest import mock
import importlib

h = importlib.import_module("scrapers._gnews_helpers")
gn = importlib.import_module("ingestion.sources.google_news_rss")
from ingestion.contracts import SourceBlockedError, SourceUnavailableError

PUBLISHER_URL = "https://www.channelnewsasia.com/singapore/yishun-fatal-accident-1234567"
ARTICLE_ID = "CBMiZ2h0dHBzOi8vZXhhbXBsZS5vcmcvYXJ0aWNsZQ"  # opaque blob, not decodable
RSS_WRAPPER = f"https://news.google.com/rss/articles/{ARTICLE_ID}?oc=5"

# A realistic batchexecute envelope: )]}'-prefixed, \n\n-delimited; the data row
# is ["wrb.fr","Fbv4je","[\"garturlres\",\"<url>\"]",null,null,null,"generic"],
# trailed by ["di",..] / ["af.httprm",..] metadata rows.
def _envelope(url: str) -> str:
    import json
    data_row = ["wrb.fr", "Fbv4je", json.dumps(["garturlres", url]),
                None, None, None, "generic"]
    body = json.dumps([data_row, ["di", 24], ["af.httprm", 24, "xyz", 17]])
    return ")]}'\n\n" + body

PAGE_HTML = (
    '<c-wiz><div data-n-a-ts="1718900000" '
    'data-n-a-sg="AU_yqLOcSomeSignatureValue123==">x</div></c-wiz>'
)
PAGE_HTML_NO_ATTRS = "<c-wiz><div>nothing useful here</div></c-wiz>"

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if cond:
        passed += 1
    else:
        failed += 1


class _Resp:
    """Minimal httpx.Response stand-in."""
    def __init__(self, *, text="", url=None, raise_exc=None):
        self.text = text
        self.url = url
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise:
            raise self._raise


class _FakeClient:
    """Drop-in for httpx.Client used as a context manager.

    get_handler / post_handler receive the URL and return a _Resp (or raise).
    """
    def __init__(self, get_handler=None, post_handler=None):
        self._get = get_handler
        self._post = post_handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, *a, **k):
        return self._get(url)

    def post(self, url, *a, **k):
        return self._post(url)


def _patch_client(get_handler=None, post_handler=None):
    return mock.patch.object(
        h, "httpx",
        mock.MagicMock(Client=lambda *a, **k: _FakeClient(get_handler, post_handler)),
    )


print("gnews resolver tests:")

# ── 1. _extract_article_id ───────────────────────────────────────────────────
check("1a rss/articles blob extracted",
      h._extract_article_id(RSS_WRAPPER) == ARTICLE_ID)
check("1b bare articles blob extracted",
      h._extract_article_id(f"https://news.google.com/articles/{ARTICLE_ID}") == ARTICLE_ID)
check("1c publisher URL -> None",
      h._extract_article_id(PUBLISHER_URL) is None)

# ── 2. _parse_decoding_params ────────────────────────────────────────────────
check("2a sig+ts extracted",
      h._parse_decoding_params(PAGE_HTML) == ("AU_yqLOcSomeSignatureValue123==", "1718900000"))
check("2b missing attrs -> None",
      h._parse_decoding_params(PAGE_HTML_NO_ATTRS) is None)

# ── 3/4. _parse_batchexecute_url ─────────────────────────────────────────────
check("3 publisher URL parsed from envelope",
      h._parse_batchexecute_url(_envelope(PUBLISHER_URL)) == PUBLISHER_URL)
check("4a garbage response -> None",
      h._parse_batchexecute_url("totally not json") is None)
check("4b empty response -> None",
      h._parse_batchexecute_url("") is None)

# ── 5. happy path ────────────────────────────────────────────────────────────
with _patch_client(
    get_handler=lambda url: _Resp(text=PAGE_HTML),
    post_handler=lambda url: _Resp(text=_envelope(PUBLISHER_URL)),
):
    check("5 modern wrapper resolves to publisher URL",
          h._resolve_redirect(RSS_WRAPPER) == PUBLISHER_URL)

# ── 6. params GET raises -> raw_url ──────────────────────────────────────────
def _boom(url):
    raise RuntimeError("network down")

with _patch_client(get_handler=_boom):
    check("6 params GET error degrades to raw_url",
          h._resolve_redirect(RSS_WRAPPER) == RSS_WRAPPER)

# ── 7. missing attrs -> falls through, plain GET lands on g.news -> raw_url ───
with _patch_client(
    get_handler=lambda url: _Resp(text=PAGE_HTML_NO_ATTRS, url=RSS_WRAPPER),
):
    check("7 missing sig/ts degrades to raw_url",
          h._resolve_redirect(RSS_WRAPPER) == RSS_WRAPPER)

# ── 8. batchexecute garbage -> falls through, plain GET on g.news -> raw_url ──
with _patch_client(
    get_handler=lambda url: _Resp(text=PAGE_HTML, url=RSS_WRAPPER),
    post_handler=lambda url: _Resp(text="rotated format!!"),
):
    check("8 garbage batchexecute degrades to raw_url",
          h._resolve_redirect(RSS_WRAPPER) == RSS_WRAPPER)

# ── 9. non-gnews wrapper, plain redirect followed ────────────────────────────
LEGACY = "https://news.url.google.com/__r/abc?u=foo"
with _patch_client(get_handler=lambda url: _Resp(url=PUBLISHER_URL)):
    check("9 legacy wrapper follows redirect to publisher",
          h._resolve_redirect(LEGACY) == PUBLISHER_URL)

# ── 10. redirect lands back on news.google.com -> raw_url ────────────────────
with _patch_client(get_handler=lambda url: _Resp(url="https://news.google.com/foo")):
    check("10 redirect back to g.news degrades to raw_url",
          h._resolve_redirect(LEGACY) == LEGACY)

# ── 11/12. fetch() keyword-query failure isolation ───────────────────────────
# WHY: Google News 503s from the Cloud Run datacenter IP intermittently and is
# the DOMINANT discovery channel. One flaky keyword raising out of the bare loop
# used to discard every candidate already gathered from earlier keywords (a
# single 503 lost all 11). fetch() must now keep the successful candidates and
# only raise SourceBlockedError when EVERY query fails.


def _feed(idx):
    """A one-entry feedparser-style stand-in whose entry matches YISHUN_KEYWORDS
    and carries a unique wrapper URL so each keyword contributes one candidate.
    No published_parsed -> published_at is None; with since=None nothing is
    skipped as stale, so each success reliably yields exactly one candidate."""
    entry = {
        "link": f"https://news.google.com/rss/articles/BLOB{idx}?oc=5",
        "title": f"Yishun incident {idx} - CNA",
        "summary": "yishun something happened",
    }
    return mock.MagicMock(entries=[entry])


def _fetch_factory(fail_at, exc_type):
    """Return a _fetch_feed replacement that raises exc_type at the given
    zero-based call indices and returns a fresh feed otherwise."""
    state = {"n": 0}

    def _fetch(query):
        i = state["n"]
        state["n"] += 1
        if i in fail_at:
            raise exc_type(f"boom on {query!r}")
        return _feed(i)

    return _fetch


def _run_fetch(fail_at, exc_type):
    """Run gn.fetch(None) fully offline: _fetch_feed / _resolve_redirect / sleep
    all patched (no network, no real backoff)."""
    with mock.patch.object(gn, "_fetch_feed", side_effect=_fetch_factory(fail_at, exc_type)), \
         mock.patch.object(gn, "_resolve_redirect", side_effect=lambda u: u), \
         mock.patch.object(gn.time, "sleep", lambda *a, **k: None):
        return gn.GoogleNewsRSSSource().fetch(None)


n_kw = len(gn.YISHUN_KEYWORDS)

# 11: one MIDDLE keyword (index 1) blocks; the rest succeed. fetch() must NOT
# raise, must keep every candidate from the successful keywords, and must not
# lose the one gathered BEFORE the failing keyword (index 0).
try:
    got = _run_fetch(fail_at={1}, exc_type=SourceBlockedError)
    raised_11 = False
except Exception:
    got, raised_11 = [], True
check("11a one blocked keyword does not raise", raised_11 is False)
check("11b successful keywords all kept (n-1 candidates)", len(got) == n_kw - 1)
check("11c candidate gathered BEFORE the failure is preserved",
      any(c.url == "https://news.google.com/rss/articles/BLOB0?oc=5" for c in got))

# 12a: EVERY query fails with a transient error -> SourceBlockedError. Transient
# errors don't trip the first-query fast path, so this exercises the
# end-of-loop "nothing succeeded" raise.
try:
    _run_fetch(fail_at=set(range(n_kw)), exc_type=SourceUnavailableError)
    err_12a = None
except Exception as e:
    err_12a = e
check("12a all queries fail -> SourceBlockedError",
      isinstance(err_12a, SourceBlockedError))

# 12b: a hard block on the very FIRST query (nothing collected yet) fails fast
# with SourceBlockedError — the full datacenter-IP-block case.
try:
    _run_fetch(fail_at={0}, exc_type=SourceBlockedError)
    err_12b = None
except Exception as e:
    err_12b = e
check("12b hard block on first query fails fast (SourceBlockedError)",
      isinstance(err_12b, SourceBlockedError))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
