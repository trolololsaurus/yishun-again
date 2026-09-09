"""
Image history cleanup partition (ops/image_cleanup.py). Pure, offline.

Run: .venv/Scripts/python.exe test_image_cleanup.py

Load-bearing properties:
  - an entry older than the TTL is expired even if within the keep-count
  - an entry beyond the newest `keep` is expired even if within the TTL
  - the live image is never in history, so "fail toward deletion" on a
    malformed created_at cannot delete a served picture
  - kept is returned newest-last (as stored) and never exceeds `keep`
"""
import importlib
from datetime import datetime, timedelta, timezone

ic = importlib.import_module("ops.image_cleanup")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def entry(url, hours_ago):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"key": f"pixel-art/{url}.png", "url": url, "prompt": "p", "created_at": ts}


print("age-based expiry (ttl=8h, keep=3):\n")

# Three fresh + two stale. Fresh kept, stale expired — even though total <= keep+2.
hist = [entry("a", 20), entry("b", 10), entry("c", 6), entry("d", 3), entry("e", 1)]
kept, expired = ic.partition(hist, now=NOW, ttl_hours=8, keep=3)
kept_urls = [e["url"] for e in kept]
exp_urls = sorted(e["url"] for e in expired)
check("entries older than 8h are expired", exp_urls == ["a", "b"], f"-> {exp_urls}")
check("entries younger than 8h are kept", sorted(kept_urls) == ["c", "d", "e"], f"-> {kept_urls}")
check("kept is newest-last", kept_urls == ["c", "d", "e"], f"-> {kept_urls}")

print("\ncount-based expiry (all fresh, keep=3):\n")

hist = [entry("a", 5), entry("b", 4), entry("c", 3), entry("d", 2), entry("e", 1)]
kept, expired = ic.partition(hist, now=NOW, ttl_hours=8, keep=3)
kept_urls = [e["url"] for e in kept]
exp_urls = sorted(e["url"] for e in expired)
check("only the newest 3 are kept", kept_urls == ["c", "d", "e"], f"-> {kept_urls}")
check("the older 2 are expired despite being fresh", exp_urls == ["a", "b"], f"-> {exp_urls}")
check("kept never exceeds keep", len(kept) <= 3)

print("\nfail toward deletion:\n")

# A malformed created_at sorts oldest and expires — never pins an object alive.
bad = {"key": "pixel-art/x.png", "url": "x", "prompt": "p", "created_at": "not-a-date"}
kept, expired = ic.partition([bad, entry("y", 1)], now=NOW, ttl_hours=8, keep=3)
check("an unparseable created_at is expired",
      [e["url"] for e in expired] == ["x"], f"-> {[e['url'] for e in expired]}")
k2, e2 = ic.partition(["junk", entry("z", 1)], now=NOW, ttl_hours=8, keep=3)
check("a non-dict entry is dropped from both",
      [e["url"] for e in k2] == ["z"] and e2 == [], f"-> kept={k2} expired={e2}")

print("\nedge cases:\n")

check("empty history -> nothing", ic.partition([], now=NOW, ttl_hours=8, keep=3) == ([], []))
kept, expired = ic.partition([entry("a", 1)], now=NOW, ttl_hours=8, keep=0)
check("keep=0 expires everything", [e["url"] for e in expired] == ["a"] and kept == [])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
