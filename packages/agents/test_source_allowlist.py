"""
Self-contained tests for the source allowlist. No pytest, no DB.
Run: .venv/Scripts/python.exe test_source_allowlist.py

Nothing validated a source_url against the operator-approved `sources` table, so
Google News RSS could put arbitrary publishers into a published incident's
sources. Two severities on purpose:
  signal (EDMW/HWZ) -> removed outright (guardrail #2)
  unapproved        -> KEPT and flagged (removing it could take an incident's
                       last source and break guardrail #1)
"""
import importlib

sa = importlib.import_module("classifiers.source_allowlist")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

DOMAINS = {
    "channelnewsasia.com": {"type": "msm",    "approved": True,  "name": "CNA"},
    "straitstimes.com":    {"type": "msm",    "approved": True,  "name": "ST"},
    "mothership.sg":       {"type": "msm",    "approved": True,  "name": "Mothership"},
    "forums.hardwarezone.com.sg": {"type": "signal", "approved": True, "name": "HWZ EDMW"},
    "reddit.com":          {"type": "reddit", "approved": True,  "name": "Reddit"},
    "notyet.example":      {"type": "msm",    "approved": False, "name": "Pending outlet"},
}
C = lambda u: sa.classify(u, DOMAINS)

print("source allowlist tests:")

# ── domain normalisation ────────────────────────────────────────────────────
check("strips www.", sa.domain_of("https://www.asiaone.com/x") == "asiaone.com")
check("lowercases + drops port", sa.domain_of("https://WWW.Example.COM:443/x") == "example.com")
check("unparseable url -> ''", sa.domain_of("not a url") == "")

# ── classification ──────────────────────────────────────────────────────────
check("approved host", C("https://www.channelnewsasia.com/singapore/story") == "approved")
check("subdomain of approved host inherits approval (cnalifestyle)",
      C("https://cnalifestyle.channelnewsasia.com/dining/x") == "approved")
check("unknown outlet -> unapproved", C("https://8days.sg/entertainment/x") == "unapproved")
check("in table but approved_by_operator=false -> unapproved",
      C("https://notyet.example/story") == "unapproved")
check("signal host -> signal (guardrail #2)",
      C("https://forums.hardwarezone.com.sg/threads/abc.123/") == "signal")
check("lookalike domain is NOT approved (suffix match must not be substring)",
      C("https://evil-straitstimes.com/x") == "unapproved")
check("approved-name-inside-path doesn't approve",
      C("https://spam.example/straitstimes.com/x") == "unapproved")

# ── filtering ───────────────────────────────────────────────────────────────
r = sa.check_source_urls([
    "https://www.straitstimes.com/a",
    "https://forums.hardwarezone.com.sg/t/1",   # signal -> dropped
    "https://8days.sg/b",                       # unapproved -> kept + flagged
], DOMAINS)
check("signal URL dropped from kept", "https://forums.hardwarezone.com.sg/t/1" not in r["kept"])
check("signal URL recorded", r["dropped_signal"] == ["https://forums.hardwarezone.com.sg/t/1"])
check("unapproved URL KEPT (never strip the last source)", "https://8days.sg/b" in r["kept"])
check("unapproved URL flagged", r["unapproved"] == ["https://8days.sg/b"])
check("approved URL kept", "https://www.straitstimes.com/a" in r["kept"])
check("kept preserves order", r["kept"] == ["https://www.straitstimes.com/a", "https://8days.sg/b"])

check("all-approved -> nothing flagged",
      sa.check_source_urls(["https://mothership.sg/x"], DOMAINS) ==
      {"kept": ["https://mothership.sg/x"], "dropped_signal": [],
       "dropped_redirect": [], "unapproved": []})

# ── redirectors: a citation must point at the publisher, not a wrapper ───────
# google_news_rss put unresolved news.google.com/rss/articles/<blob> URLs into
# war_room_queue.source_url and source_urls in production (2026-08-01). The
# source is gone; this rule is the net under the paths that remain (historical
# backfill, source discovery).
GNEWS = ("https://news.google.com/rss/articles/CBMizAFBVV95cUxORm9BVGZoT09RZTFM"
         "Z0hnMUZzSEkyRk9tV3dGMXJvbTUzNmRHVGFz?oc=5")

check("google news wrapper classifies as redirect", sa.classify(GNEWS, DOMAINS) == "redirect")
check("is_redirect_domain catches the wrapper", sa.is_redirect_domain(GNEWS))
check("is_redirect_domain catches a subdomain",
      sa.is_redirect_domain("https://rss.news.google.com/x"))
check("is_redirect_domain catches a shortener", sa.is_redirect_domain("https://t.co/abc"))
check("publisher URL is not a redirector",
      not sa.is_redirect_domain("https://www.straitstimes.com/a"))
check("blank URL is not a redirector", not sa.is_redirect_domain(""))

rr = sa.check_source_urls([GNEWS, "https://www.straitstimes.com/a"], DOMAINS)
check("redirector dropped from kept", GNEWS not in rr["kept"])
check("redirector recorded separately", rr["dropped_redirect"] == [GNEWS])
check("real publisher survives alongside it", rr["kept"] == ["https://www.straitstimes.com/a"])

# A redirect-only list empties `kept`. That is correct: the candidate has no
# verifiable source, which is exactly what guardrail #1 is for. It must NOT be
# rescued by keeping the wrapper.
only = sa.check_source_urls([GNEWS], DOMAINS)
check("redirect-only list yields empty kept", only["kept"] == [])
check("redirect-only list is not silently kept", only["dropped_redirect"] == [GNEWS])

# The redirect rule must not be defeatable by adding the host to `sources`.
check("redirect wins over an approved sources-table row",
      sa.classify(GNEWS, {"news.google.com": {"type": "msm", "approved": True,
                                              "name": "Google News"}}) == "redirect")
check("empty input -> empty result", sa.check_source_urls([], DOMAINS)["kept"] == [])
check("None input handled", sa.check_source_urls(None, DOMAINS)["kept"] == [])
check("blank urls skipped", sa.check_source_urls(["", None], DOMAINS)["kept"] == [])

# a signal-only list empties `kept` — callers must fall back so guardrail #1 holds
sig = sa.check_source_urls(["https://forums.hardwarezone.com.sg/t/1"], DOMAINS)
check("signal-only list yields empty kept (caller must fall back)", sig["kept"] == [])

# ── DB unavailable must never silently strip sources ────────────────────────
r2 = sa.check_source_urls(["https://www.straitstimes.com/a"], {})
check("empty domain map keeps everything (flags, never strips)",
      r2["kept"] == ["https://www.straitstimes.com/a"] and r2["dropped_signal"] == [])

# ── guardrail #2: signal detection must survive the vocab drift ─────────────
# scrape_edmw emits source_type 'signal'; the orchestrator and Candidate's
# contract say 'edmw'. orchestrator.py tested == "edmw", so a real EDMW candidate
# (which says 'signal') slipped through and its forum URL was written into
# source_urls. is_signal_source accepts both spellings AND falls back to a domain
# lookup, so no single string mismatch can breach the guardrail.
EDMW_URL = "https://forums.hardwarezone.com.sg/threads/yishun-again.123/"

check("source_type 'signal' detected (what scrape_edmw actually emits)",
      sa.is_signal_source("signal", EDMW_URL, DOMAINS) is True)
check("source_type 'edmw' detected (what the contract/orchestrator say)",
      sa.is_signal_source("edmw", "https://x.example/a", DOMAINS) is True)
check("case/whitespace tolerated", sa.is_signal_source("  SIGNAL ", "", DOMAINS) is True)
check("mislabelled 'msm' still caught via signal DOMAIN (defence in depth)",
      sa.is_signal_source("msm", EDMW_URL, DOMAINS) is True)
check("missing source_type still caught via domain",
      sa.is_signal_source(None, EDMW_URL, DOMAINS) is True)
check("a genuine MSM candidate is NOT signal",
      sa.is_signal_source("msm", "https://www.straitstimes.com/a", DOMAINS) is False)
check("unknown outlet is not signal just because it is unapproved",
      sa.is_signal_source("msm", "https://8days.sg/x", DOMAINS) is False)

# ── QA M14: one canonical vocabulary ────────────────────────────────────────
check("'edmw' normalises to canonical 'signal'", sa.canonical_source_type("edmw") == "signal")
check("'signal' stays 'signal'", sa.canonical_source_type("signal") == "signal")
check("case/whitespace normalised", sa.canonical_source_type("  EDMW ") == "signal")
check("non-signal types pass through", sa.canonical_source_type("msm") == "msm")
check("None -> empty string", sa.canonical_source_type(None) == "")
check("canonical value is what the sources-table CHECK accepts",
      sa.CANONICAL_SIGNAL_TYPE == "signal")

# the end-to-end consequence the bug had
r_edmw = sa.check_source_urls([EDMW_URL], DOMAINS)
check("an EDMW-only candidate yields NO quoted source_urls",
      r_edmw["kept"] == [] and r_edmw["dropped_signal"] == [EDMW_URL])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
