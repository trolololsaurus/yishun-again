"""
Self-contained test: ops/supervisor.py's zero-streak check must be tier-aware.
Discovery-tier sources (`_sitemap`/`_search` ids) have `fetched` already
post-keyword-filter, so a long real zero-match streak is normal for a small
town and must not be flagged anomalous — while a primary source (or a genuine
discovery outage past its much longer leash) still must be.
Run: .venv/Scripts/python.exe test_supervisor_discovery_tier.py
"""
import importlib

sup = importlib.import_module("ops.supervisor")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

print("supervisor discovery-tier zero-streak tests:")

def codes_for(streaks):
    findings = sup.classify_findings(pipeline_state=[], streaks=streaks)
    return {f["code"] for f in findings}

# A discovery source with a long real zero-match streak (below its raised
# threshold) must NOT trigger zero_streak.
streaks = [{"source_name": "cna_sitemap", "consecutive_zeros": sup.ZERO_STREAK_ANOMALY + 5}]
check("discovery source below its own threshold stays quiet",
      "zero_streak" not in codes_for(streaks))

# The same streak length on a PRIMARY source must still fire.
streaks = [{"source_name": "cna", "consecutive_zeros": sup.ZERO_STREAK_ANOMALY + 5}]
check("primary source at the primary threshold still fires",
      "zero_streak" in codes_for(streaks))

# A discovery source that finally clears ITS OWN (higher) threshold still fires.
streaks = [{"source_name": "mustsharenews_search",
            "consecutive_zeros": sup.DISCOVERY_ZERO_STREAK_ANOMALY}]
check("discovery source past its raised threshold fires",
      "zero_streak" in codes_for(streaks))

# _is_discovery only matches the documented suffixes.
check("_is_discovery matches _sitemap", sup._is_discovery("straits_times_sitemap"))
check("_is_discovery matches _search", sup._is_discovery("the_independent_search"))
check("_is_discovery rejects primary ids", not sup._is_discovery("stomp"))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
