"""
Self-contained tests for the multi-source collapse fix. No pytest, no API.
Run: .venv/Scripts/python.exe test_backfill_sources.py

Bug: seed_backfill.build_candidates aggregates every fetched URL for a story,
but backfill_agent collapsed it back to a single [url] when building the Stage 2
input, and both build_queue_row and _build_incident_row hardcoded
corroboration_count=1. Every backfilled incident therefore published with one
source_url and zero lightning bolts (bolts = corroboration_count - 1),
regardless of how many sources were actually fetched.
"""
import importlib
from unittest import mock

qr = importlib.import_module("consolidation.queue_row")
bf = importlib.import_module("scrapers.backfill_agent")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

FIVE = [f"https://outlet{i}.example/story" for i in range(1, 6)]
ITEM = {
    "url": FIVE[0], "source_urls": list(FIVE), "source_type": "msm",
    "date": "2026-07-04", "title": "t", "content": "c",
    "source_timeline": [{"source_url": u, "date": "2026-07-04"} for u in FIVE],
}
DRAFT = {
    "title": "t", "summary": "s", "classification": "dagger", "severity": 3,
    "confidence": 0.9, "source_urls": list(FIVE), "slug": "yishun-x-jul-2026",
    "pixel_art_prompt": "p", "hype_meter": 3, "tags": [],
}

print("backfill multi-source tests:")

# ── war_room_queue row ──────────────────────────────────────────────────────
row = qr.build_queue_row(ITEM, DRAFT)
check("queue: corroboration_count = 5 (was hardcoded 1)", row["corroboration_count"] == 5)
check("queue: raw_content.source_urls keeps all 5",
      len(row["raw_content"].get("source_urls") or []) == 5)

check("queue: single source still yields 1",
      qr.build_queue_row({**ITEM, "source_urls": [FIVE[0]]},
                         {**DRAFT, "source_urls": [FIVE[0]]})["corroboration_count"] == 1)
check("queue: duplicate urls de-duplicated",
      qr.build_queue_row(ITEM, {**DRAFT, "source_urls": [FIVE[0], FIVE[0], FIVE[1]]})["corroboration_count"] == 2)
check("queue: empty source list floors at 1",
      qr.build_queue_row({**ITEM, "source_urls": []},
                         {**DRAFT, "source_urls": []})["corroboration_count"] == 1)
check("queue: falls back to item.source_urls when draft has none",
      qr.build_queue_row(ITEM, {k: v for k, v in DRAFT.items() if k != "source_urls"})["corroboration_count"] == 5)

# ── incidents row (auto-publish tier) ───────────────────────────────────────
with mock.patch("classifiers.geocoding.geocode_incident", return_value=None):
    inc = bf._build_incident_row(DRAFT, ITEM)
check("incident: source_urls keeps all 5 (was [item.url])", len(inc["source_urls"]) == 5)
check("incident: corroboration_count = 5 (was hardcoded 1)", inc["corroboration_count"] == 5)
check("incident: source_timeline preserved (was [])", len(inc["source_timeline"]) == 5)

with mock.patch("classifiers.geocoding.geocode_incident", return_value=None):
    inc1 = bf._build_incident_row({**DRAFT, "source_urls": [FIVE[0]]}, {**ITEM, "source_urls": [FIVE[0]], "source_timeline": []})
check("incident: single source -> count 1", inc1["corroboration_count"] == 1)
check("incident: lightning would render 0 bolts for a true single source",
      max(0, inc1["corroboration_count"] - 1) == 0)
check("incident: lightning renders 4 bolts for a 5-source story",
      max(0, inc["corroboration_count"] - 1) == 4)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
