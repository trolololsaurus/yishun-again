"""
Self-contained tests for per-host fetch politeness. No pytest, no network.
Run: .venv/Scripts/python.exe test_fetch_throttle.py

WHY THIS FILE EXISTS
--------------------
Article fetching used to be rare. It is not any more: the news-sitemap adapters
fetch every keyword-matching article and enrich_thin_content fetches one per
thin candidate, so a pass can issue a dozen requests to one publisher within
seconds, from one datacenter IP.

On 2026-08-05 the 14:58 pass — the first with enrichment — returned with EVERY
SPH Media property 403ing at once (Straits Times, Stomp, Zaobao, Shin Min,
Berita Harian, Tamil Murasu) plus HWZ. The same URLs answered 200 from a
residential IP minutes later, and the whole fleet was `ok` again by the next
pass. Not an outage, not a ban: one operator's bot defence throttling a burst.

Two rules, both per-HOST so a slow publisher cannot starve the others:
  1. a minimum interval between requests to the same host;
  2. a 403/429 trips that host for the rest of the pass — continuing to hammer
     a host that just refused is how a throttle becomes a ban.
"""
import time
from unittest import mock

import scrapers.fetch_strategy as fs

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


class _Resp:
    def __init__(self, status=200, text="<html>ok</html>", url="https://x/1"):
        self.status_code, self.text, self.url = status, text, url


print("fetch throttle tests:")

# ── A refusing host is not asked again this pass ────────────────────────────
fs.reset_host_throttle()
calls = []
with mock.patch.object(fs.httpx, "get", side_effect=lambda u, **k: (calls.append(u), _Resp(403))[1]), \
     mock.patch.object(fs.time, "sleep"):
    a = fs.DirectHttpx().fetch("https://www.straitstimes.com/a")
    b = fs.DirectHttpx().fetch("https://www.straitstimes.com/b")
check("a 403 yields no result", a is None and b is None)
check("...and the host is asked only ONCE, not hammered", len(calls) == 1)
check("the host is recorded as tripped", "www.straitstimes.com" in fs._HOST_TRIPPED)

# A different host must be unaffected — one publisher refusing may not
# blind the pass to the rest of the fleet.
with mock.patch.object(fs.httpx, "get", return_value=_Resp(200)), \
     mock.patch.object(fs.time, "sleep"):
    other = fs.DirectHttpx().fetch("https://mothership.sg/a")
check("a different host still fetches", other is not None and other.via == "direct")

# 429 trips too.
fs.reset_host_throttle()
with mock.patch.object(fs.httpx, "get", return_value=_Resp(429)), \
     mock.patch.object(fs.time, "sleep"):
    fs.DirectHttpx().fetch("https://www.stomp.sg/a")
check("a 429 also trips the host", "www.stomp.sg" in fs._HOST_TRIPPED)

# A 500 is a server hiccup, not a refusal — it must NOT trip the host.
fs.reset_host_throttle()
with mock.patch.object(fs.httpx, "get", return_value=_Resp(500)), \
     mock.patch.object(fs.time, "sleep"):
    fs.DirectHttpx().fetch("https://www.zaobao.com.sg/a")
check("a 500 does not trip the host (transient, not a refusal)",
      "www.zaobao.com.sg" not in fs._HOST_TRIPPED)

# ── Requests to one host are spaced out ─────────────────────────────────────
fs.reset_host_throttle()
slept = []
with mock.patch.object(fs.httpx, "get", return_value=_Resp(200)), \
     mock.patch.object(fs.time, "sleep", side_effect=lambda s: slept.append(s)):
    fs.DirectHttpx().fetch("https://www.straitstimes.com/1")
    fs.DirectHttpx().fetch("https://www.straitstimes.com/2")
    fs.DirectHttpx().fetch("https://www.straitstimes.com/3")
check("back-to-back requests to one host sleep between them", len(slept) >= 2)
check("...for about the configured interval",
      all(0 < s <= fs._HOST_MIN_INTERVAL + 0.01 for s in slept))

fs.reset_host_throttle()
slept = []
with mock.patch.object(fs.httpx, "get", return_value=_Resp(200)), \
     mock.patch.object(fs.time, "sleep", side_effect=lambda s: slept.append(s)):
    fs.DirectHttpx().fetch("https://a.example/1")
    fs.DirectHttpx().fetch("https://b.example/1")
check("different hosts are not made to wait for each other", not slept)

# ── Housekeeping ────────────────────────────────────────────────────────────
fs.reset_host_throttle()
check("reset clears both maps", not fs._HOST_TRIPPED and not fs._HOST_LAST_REQUEST)
check("the interval is polite but not pass-breaking",
      0.5 <= fs._HOST_MIN_INTERVAL <= 5.0)

with mock.patch.object(fs.httpx, "get", side_effect=RuntimeError("boom")), \
     mock.patch.object(fs.time, "sleep"):
    check("a transport error still degrades to None, never raises",
          fs.DirectHttpx().fetch("https://c.example/1") is None)
check("empty url is a no-op", fs.DirectHttpx().fetch("") is None)

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
