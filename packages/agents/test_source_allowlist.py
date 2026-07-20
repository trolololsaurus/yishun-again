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
      {"kept": ["https://mothership.sg/x"], "dropped_signal": [], "unapproved": []})
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

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
