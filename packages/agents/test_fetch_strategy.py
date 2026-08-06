"""
Self-contained tests for the pluggable fetch strategy + Wayback fallback.
No pytest, no real network, no DB. Run:
    .venv/Scripts/python.exe test_fetch_strategy.py

The seam exists so a blocked ARTICLE fetch recovers a date/body from the Wayback
snapshot instead of going dateless-and-unapprovable (QA H3). These tests pin the
contract that matters: direct wins the happy path and never touches Wayback; a
blocked direct fetch falls through to Wayback; both failing degrades to None
(never a raise); and the browser rung is a stub kept out of the default chain.
"""
import importlib
from unittest import mock

import httpx

fs = importlib.import_module("scrapers.fetch_strategy")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


class FakeResp:
    """Minimal stand-in for an httpx.Response."""
    def __init__(self, status_code, text, url, json_data=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


ART  = "https://www.asiaone.com/singapore/yishun-cat-saga"
SNAP = "http://web.archive.org/web/20260716/https://www.asiaone.com/singapore/yishun-cat-saga"

print("fetch strategy tests:")

# ── happy path: direct succeeds, Wayback is never consulted ─────────────────
def _direct_only(url, **kw):
    return FakeResp(200, "<html>live body</html>", ART)

with mock.patch.object(fs.httpx, "get", side_effect=_direct_only) as g, \
     mock.patch.object(fs.WaybackSnapshot, "_resolve_snapshot") as wb:
    res = fs.fetch_with_fallback(ART)
check("direct success -> result", res is not None)
check("direct success -> via='direct'", res is not None and res.via == "direct")
check("direct success -> html preserved", res is not None and res.html == "<html>live body</html>")
check("direct success -> final_url set", res is not None and res.final_url == ART)
check("direct success -> only one HTTP call", g.call_count == 1)
check("direct success -> Wayback NEVER consulted", wb.call_count == 0)

# ── direct blocked -> Wayback recovers, via='wayback' ───────────────────────
def _direct_blocked_wayback_ok(url, **kw):
    if url == ART:
        return FakeResp(403, "", ART)                 # bot wall / blocked
    if url == SNAP:
        return FakeResp(200, "<html>archived body</html>", SNAP)
    raise AssertionError(f"unexpected fetch: {url}")

with mock.patch.object(fs.httpx, "get", side_effect=_direct_blocked_wayback_ok), \
     mock.patch.object(fs.WaybackSnapshot, "_resolve_snapshot", return_value=SNAP):
    res = fs.fetch_with_fallback(ART)
check("blocked direct -> Wayback result", res is not None)
check("blocked direct -> via='wayback'", res is not None and res.via == "wayback")
check("wayback -> archived html", res is not None and res.html == "<html>archived body</html>")
check("wayback -> url stays canonical publisher URL", res is not None and res.url == ART)
check("wayback -> final_url is the snapshot", res is not None and res.final_url == SNAP)

# a direct fetch that RAISES (transport error) must also fall through to Wayback
def _direct_raise_wayback_ok(url, **kw):
    if url == ART:
        raise httpx.ConnectError("connection reset")
    if url == SNAP:
        return FakeResp(200, "<html>archived body</html>", SNAP)
    raise AssertionError(f"unexpected fetch: {url}")

with mock.patch.object(fs.httpx, "get", side_effect=_direct_raise_wayback_ok), \
     mock.patch.object(fs.WaybackSnapshot, "_resolve_snapshot", return_value=SNAP):
    res = fs.fetch_with_fallback(ART)
check("direct transport error -> recovered via Wayback", res is not None and res.via == "wayback")

# ── both rungs fail -> None, and NO raise ───────────────────────────────────
raised = False
res = "SENTINEL"
try:
    with mock.patch.object(fs.httpx, "get", side_effect=httpx.ConnectError("boom")), \
         mock.patch.object(fs.WaybackSnapshot, "_resolve_snapshot", return_value=None):
        res = fs.fetch_with_fallback(ART)
except Exception:
    raised = True
check("both fail -> never raises", raised is False)
check("both fail -> None", res is None)

# no snapshot available -> WaybackSnapshot returns None (not a raise)
with mock.patch.object(fs.WaybackSnapshot, "_resolve_snapshot", return_value=None):
    check("no snapshot -> WaybackSnapshot.fetch None", fs.WaybackSnapshot().fetch(ART) is None)

# ── individual rung behaviour ───────────────────────────────────────────────
with mock.patch.object(fs.httpx, "get", side_effect=lambda url, **kw: FakeResp(429, "", ART)):
    check("DirectHttpx non-200 -> None (falls through)", fs.DirectHttpx().fetch(ART) is None)

# The 429 above TRIPS that host for the rest of the process — a host that just
# refused is not asked again (see test_fetch_throttle.py for why). Reset before
# asserting the happy path, or this reads as a mysterious failure rather than
# the throttle doing its job.
fs.reset_host_throttle()

with mock.patch.object(fs.httpx, "get", side_effect=lambda url, **kw: FakeResp(200, "<html>ok</html>", ART)):
    r = fs.DirectHttpx().fetch(ART)
    check("DirectHttpx 200 -> via='direct'", r is not None and r.via == "direct")

check("empty url -> None (DirectHttpx)", fs.DirectHttpx().fetch("") is None)
check("empty url -> None (WaybackSnapshot)", fs.WaybackSnapshot().fetch("") is None)
check("empty url -> None (fetch_with_fallback)", fs.fetch_with_fallback("") is None)

# ── the chain never lets a strategy crash the pass ──────────────────────────
class _Boom(fs.FetchStrategy):
    def fetch(self, url, *, timeout=None):
        raise ValueError("half-built strategy blew up")

_good = fs.FetchResult(url=ART, final_url=ART, status=200, html="ok", via="direct")
class _Good(fs.FetchStrategy):
    def fetch(self, url, *, timeout=None):
        return _good

check("a raising strategy is swallowed; next rung still used",
      fs.fetch_with_fallback(ART, chain=[_Boom(), _Good()]) is _good)
check("all-raising chain -> None (no raise)",
      fs.fetch_with_fallback(ART, chain=[_Boom(), _Boom()]) is None)

# ── browser stub: NOT in the default chain, and a no-op ─────────────────────
check("DEFAULT_CHAIN is exactly [DirectHttpx, WaybackSnapshot]",
      [type(s).__name__ for s in fs.DEFAULT_CHAIN] == ["DirectHttpx", "WaybackSnapshot"])
check("BrowserService NOT in DEFAULT_CHAIN",
      not any(isinstance(s, fs.BrowserService) for s in fs.DEFAULT_CHAIN))
check("BrowserService stub returns None (seam, not implemented)",
      fs.BrowserService().fetch(ART) is None)

# ── Wayback snapshot resolution reuses backfill_agent.get_wayback_url ────────
# The reuse is the point (single source of truth for the availability API); the
# lazy import keeps backfill_agent's heavy deps off the module-load/happy paths.
# Guard the import so a backfill_agent import problem can't fail the whole suite.
try:
    bf = importlib.import_module("scrapers.backfill_agent")
except Exception as exc:  # pragma: no cover — only if deps are missing
    print(f"  [SKIP] backfill_agent reuse checks (import failed: {exc})")
    bf = None

if bf is not None:
    with mock.patch.object(bf, "get_wayback_url", return_value=SNAP) as reused:
        snap = fs.WaybackSnapshot._resolve_snapshot(ART)
    check("reuses backfill_agent.get_wayback_url when importable", snap == SNAP)
    check("reuse helper actually called", reused.call_count == 1)

    # When the reused helper raises, resolution degrades to the inline
    # availability query rather than crashing.
    def _avail(url, **kw):
        return FakeResp(200, "", fs._WAYBACK_API, json_data={
            "archived_snapshots": {"closest": {"available": True, "url": SNAP}}
        })
    with mock.patch.object(bf, "get_wayback_url", side_effect=RuntimeError("reuse down")), \
         mock.patch.object(fs.httpx, "get", side_effect=_avail):
        snap = fs.WaybackSnapshot._resolve_snapshot(ART)
    check("reuse raises -> inline availability resolver takes over", snap == SNAP)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
