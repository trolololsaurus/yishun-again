"""
Self-contained tests for source_timeline synthesis in build_queue_row
(no pytest, no network, no DB).
Run: .venv/Scripts/python.exe test_queue_row_source_dates.py

WHY THIS FILE EXISTS
--------------------
The public incident page prints each citation's publication date, read from
`incidents.source_timeline`. But clustering.build_cluster_stage2_input only
attaches a source_timeline when a cluster holds MORE THAN ONE article:

    if len(articles) > 1:
        stage2["source_timeline"] = timeline

so every single-source story published with `source_timeline: []` and rendered
its one citation with no date. An audit on 2026-08-03 found 163 undated source
links across the 163 published incidents, the majority of them "1/1 undated".

Backfilling the existing rows does not close this — the next single-source
incident would be undated again. build_queue_row is the one funnel BOTH the
live ingestion pipeline and the historical backfill agent pass through, which
is why the synthesis lives there.

The rules pinned here: never overwrite an existing entry, never admit a signal
URL (guardrail #2), and never invent a date for a dateless candidate.
"""
from unittest import mock

import consolidation.queue_row as qr

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


ST = "https://www.straitstimes.com/singapore/man-hurt-in-yishun-fight"
CNA = "https://www.channelnewsasia.com/singapore/yishun-fight-12345"
REDDIT = "https://www.reddit.com/r/singapore/comments/abc/man_hurt_in_yishun/"

BASE_DRAFT = {
    "title": "Man hurt in Yishun fight",
    "summary": "A man was hurt.",
    "classification": "dagger",
    "severity": 3,
    "confidence": 0.8,
}


def allowlist(kept, dropped_signal=(), dropped_redirect=(), unapproved=()):
    """Stub check_source_urls so these tests need no `sources` table."""
    return mock.patch.object(
        qr, "check_source_urls",
        lambda urls, *a, **kw: {
            "kept": [u for u in urls if u in kept],
            "dropped_signal": list(dropped_signal),
            "dropped_redirect": list(dropped_redirect),
            "unapproved": list(unapproved),
        },
    )


def timeline_of(row):
    return row["raw_content"].get("source_timeline") or []


print("queue_row source date tests:")

# -- The core regression: one source, one date --------------------------------
item = {"url": ST, "date": "2026-07-25", "source_name": "The Straits Times",
        "title": "Man taken unconscious to hospital", "source_type": "msm"}
with allowlist({ST}):
    row = qr.build_queue_row(item, {**BASE_DRAFT, "source_urls": [ST]})
tl = timeline_of(row)
check("single-source story gets a source_timeline", len(tl) == 1)
check("the entry carries the candidate's own publication date",
      tl and tl[0]["date"] == "2026-07-25")
check("the entry points at the source URL", tl and tl[0]["source_url"] == ST)
check("the entry carries the publisher name",
      tl and tl[0]["source_name"] == "The Straits Times")
check("synthesised entries carry NO role (cannot outrank a real label)",
      tl and "role" not in tl[0])

# -- A dateless candidate must not get an invented date -----------------------
with allowlist({ST}):
    row = qr.build_queue_row({**item, "date": ""}, {**BASE_DRAFT, "source_urls": [ST]})
check("a dateless candidate produces NO timeline entry", timeline_of(row) == [])

# -- Guardrail #2: a signal URL can never enter the timeline ------------------
sig_item = {**item, "url": REDDIT, "source_name": "Reddit Singapore", "source_type": "signal"}
with allowlist({ST}, dropped_signal=[REDDIT]):
    row = qr.build_queue_row(
        sig_item,
        {**BASE_DRAFT, "source_urls": [ST, REDDIT],
         "source_articles": [
             {"url": ST, "date": "2026-07-25", "source_name": "ST", "title": "a"},
             {"url": REDDIT, "date": "2026-07-25", "source_name": "Reddit", "title": "b"},
         ]},
    )
urls = [e["source_url"] for e in timeline_of(row)]
check("signal URL is absent from the timeline (guardrail #2)", REDDIT not in urls)
check("the surviving MSM source is still dated", ST in urls)

# -- Multi-source: every kept article contributes its own date ----------------
with allowlist({ST, CNA}):
    row = qr.build_queue_row(
        item,
        {**BASE_DRAFT, "source_urls": [ST, CNA],
         "source_articles": [
             {"url": ST,  "date": "2026-07-25", "source_name": "ST",  "title": "first"},
             {"url": CNA, "date": "2026-07-28", "source_name": "CNA", "title": "later"},
         ]},
    )
tl = timeline_of(row)
check("each kept source gets its own entry", len(tl) == 2)
check("entries are sorted earliest-first",
      [e["date"] for e in tl] == ["2026-07-25", "2026-07-28"])

# -- Never overwrite what the pipeline/operator already recorded --------------
EXISTING = [{"date": "2026-07-25", "source_url": ST, "source_name": "ST",
             "headline": "original headline", "role": "initial"}]
with allowlist({ST, CNA}):
    row = qr.build_queue_row(
        item,
        {**BASE_DRAFT, "source_urls": [ST, CNA], "source_timeline": EXISTING,
         "source_articles": [
             {"url": ST,  "date": "2026-01-01", "source_name": "WRONG", "title": "wrong"},
             {"url": CNA, "date": "2026-07-28", "source_name": "CNA",   "title": "later"},
         ]},
    )
tl = timeline_of(row)
kept_entry = next(e for e in tl if e["source_url"] == ST)
check("an existing entry is preserved byte-for-byte", kept_entry == EXISTING[0])
check("its role survives", kept_entry.get("role") == "initial")
check("the missing source is still added alongside it", len(tl) == 2)

# -- A URL that is not in source_urls must not sneak in -----------------------
with allowlist({ST}):
    row = qr.build_queue_row(
        item,
        {**BASE_DRAFT, "source_urls": [ST],
         "source_articles": [
             {"url": ST,  "date": "2026-07-25", "source_name": "ST",  "title": "a"},
             {"url": CNA, "date": "2026-07-28", "source_name": "CNA", "title": "b"},
         ]},
    )
check("an article outside the kept source_urls is not timelined",
      [e["source_url"] for e in timeline_of(row)] == [ST])

# -- Nothing knowable => no timeline key forced onto the row ------------------
with allowlist(set()):
    row = qr.build_queue_row({**item, "date": ""}, {**BASE_DRAFT, "source_urls": []})
check("no dates anywhere leaves the timeline empty", timeline_of(row) == [])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
