"""
classifiers/cf_analytics.py — pure aggregation logic. Offline.

Run: .venv/Scripts/python.exe test_cf_analytics.py

Does NOT test the live GraphQL call (needs network + a real token); it tests
the two pieces that turn raw per-day rows into the dashboard's daily/top10
shape, since that's the part a refactor is most likely to quietly break.
"""
from classifiers.cf_analytics import WINDOWS, _tally, _top10, get_traffic_summary

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


# ── _tally: sums visits per dimension value across rows ─────────────────────
rows = [
    {"dimensions": {"clientCountryName": "SG"}, "sum": {"visits": 5}},
    {"dimensions": {"clientCountryName": "SG"}, "sum": {"visits": 3}},
    {"dimensions": {"clientCountryName": "US"}, "sum": {"visits": 2}},
]
tally = _tally(rows, "clientCountryName")
check("tally sums repeated keys", tally["SG"] == 8, tally)
check("tally keeps distinct keys separate", tally["US"] == 2, tally)
check("tally has no extra keys", set(tally) == {"SG", "US"}, tally)

# A row with a null/empty dimension value (Cloudflare returns "" for unknown
# referrers/countries on some rows) must not silently vanish or KeyError.
rows_with_blank = [{"dimensions": {"clientRefererHost": ""}, "sum": {"visits": 4}}]
tally_blank = _tally(rows_with_blank, "clientRefererHost")
check("blank dimension value buckets as 'unknown'", tally_blank == {"unknown": 4}, tally_blank)

# ── _top10: sorts descending, caps at 10 ─────────────────────────────────────
big_tally = {f"c{i}": i for i in range(15)}
top = _top10(big_tally, "country")
check("top10 caps at 10 entries", len(top) == 10, len(top))
check("top10 sorts descending", [t["visits"] for t in top] == sorted((t["visits"] for t in top), reverse=True), top)
check("top10 highest value first", top[0] == {"country": "c14", "visits": 14}, top[0])
check("top10 uses the given key name", "country" in top[0] and "visits" in top[0], top[0])

small_tally = {"a": 1, "b": 2}
check("top10 returns fewer than 10 when input is smaller", len(_top10(small_tally, "x")) == 2)

# ── get_traffic_summary: rejects a bad window before touching the network ───
try:
    get_traffic_summary("3d")
    check("bad window raises ValueError", False, "did not raise")
except ValueError:
    check("bad window raises ValueError", True)
except Exception as exc:
    check("bad window raises ValueError", False, f"wrong exception type: {exc!r}")

# '30d' WAS rejected here (confirmed live 2026-08-27: this zone's plan capped
# retention at "1w1d", 8 days). Re-verified live 2026-09-21: retention is now
# "4w3d" (31 days) — the boundary was probed day-by-day, 30 days back succeeds
# and 31 fails with `code: "quota"` — so 30d is a genuinely retrievable window
# and belongs in WINDOWS. This guard now checks the opposite: that WINDOWS
# still rejects an UNSUPPORTED window ('3d') before touching the network,
# same as the bad-window check above. If retention ever regresses, catch it by
# re-verifying live (see cf_analytics.py's WINDOWS comment), not by editing
# this test to expect a raise again on faith.
check("'30d' is a supported window (live-verified 2026-09-21, see WINDOWS)",
      "30d" in WINDOWS)
try:
    get_traffic_summary("3d")
    check("an unsupported window ('3d') still raises ValueError", False, "did not raise")
except ValueError:
    check("an unsupported window ('3d') still raises ValueError", True)
except Exception as exc:
    check("an unsupported window ('3d') still raises ValueError", False, f"wrong exception type: {exc!r}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
