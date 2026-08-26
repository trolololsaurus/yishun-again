"""
Self-contained test for the autonomous auto-merge gate + merge math. No pytest.
Run: .venv/Scripts/python.exe test_auto_merge_eligibility.py

Offline: seeds source_allowlist._cache so check_source_urls needs no DB, and
tests _compute_merge as a pure function. _compute_merge mirrors applyUpdate() in
apps/war-room/lib/utils.ts — the fixtures here match utils.updateMerge.test.ts so
the two ports stay in step.
"""
import importlib

sa = importlib.import_module("classifiers.source_allowlist")
ap = importlib.import_module("ops.auto_publish")

# Fake sources table: ST approved MSM, reddit a signal, nothing else known.
sa._cache = {
    "straitstimes.com":     {"type": "msm",    "approved": True,  "name": "ST"},
    "channelnewsasia.com":  {"type": "msm",    "approved": True,  "name": "CNA"},
    "reddit.com":           {"type": "signal", "approved": False, "name": "Reddit"},
}

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def item(**over):
    base = {
        "status": "update",
        "update_target_incident_id": "inc-1",
        "agent_confidence": 0.97,
        "source_url": "https://www.channelnewsasia.com/b",
        "proposed_title": "Follow-up",
        "raw_content": {"_match_confidence": 0.97, "source_name": "CNA"},
    }
    base.update(over)
    return base


DRAFT_T, MATCH_T = 0.95, 0.95
print("auto-merge eligibility tests:")

ok, r = ap.check_update_eligibility(item(), DRAFT_T, MATCH_T)
check("both confidences high + approved source -> eligible", ok and r == "eligible")

ok, r = ap.check_update_eligibility(item(agent_confidence=0.90), DRAFT_T, MATCH_T)
check("draft confidence below threshold -> held", not ok and r == "below_threshold")

ok, r = ap.check_update_eligibility(
    item(raw_content={"_match_confidence": 0.80}), DRAFT_T, MATCH_T)
check("match confidence below threshold -> held", not ok and r == "match_below_threshold")

ok, r = ap.check_update_eligibility(item(raw_content={}), DRAFT_T, MATCH_T)
check("missing _match_confidence -> held (never auto-merge blind)", not ok and r == "no_match_confidence")

ok, r = ap.check_update_eligibility(
    item(source_url="https://www.reddit.com/r/singapore/x"), DRAFT_T, MATCH_T)
check("signal source url -> not verifiable", not ok and r == "source_not_verifiable")

ok, r = ap.check_update_eligibility(
    item(source_url="https://news.google.com/rss/articles/CBMiblob"), DRAFT_T, MATCH_T)
check("redirect wrapper url -> not verifiable", not ok and r == "source_not_verifiable")

ok, r = ap.check_update_eligibility(
    item(source_url="https://www.some-unknown-blog.example/x"), DRAFT_T, MATCH_T)
check("unapproved domain -> held for domain approval", not ok and r == "unapproved_source_domain")

ok, r = ap.check_update_eligibility(item(status="pending"), DRAFT_T, MATCH_T)
check("a pending row is not an update -> not_update", not ok and r == "not_update")

ok, r = ap.check_update_eligibility(item(update_target_incident_id=None), DRAFT_T, MATCH_T)
check("no target incident -> held", not ok and r == "no_update_target")

ok, r = ap.check_update_eligibility(
    item(raw_content={"_match_confidence": 0.97, "notification_type": "pattern_alert"}),
    DRAFT_T, MATCH_T)
check("notification sentinel row -> held", not ok and r == "notification_row")


# ── _compute_merge parity with applyUpdate() (utils.updateMerge.test.ts) ─────
print("merge math tests:")

existing = {
    "source_urls": ["https://www.straitstimes.com/a"],
    "source_timeline": [{"date": "2026-08-01", "source_url": "https://www.straitstimes.com/a", "role": "initial"}],
    "update_count": 0,
    "incident_date": "2026-08-01",
    "first_reported_at": "2026-08-01",
    "is_developing": False,
    "summary": "Original.",
}

upd, snap = ap._compute_merge(existing, "https://www.channelnewsasia.com/b", "CNA", "Follow-up", "2026-08-10")
check("newer source appended", upd["source_urls"] == ["https://www.straitstimes.com/a", "https://www.channelnewsasia.com/b"])
check("incident_date moves to the later date", upd["incident_date"] == "2026-08-10")
check("first_reported_at stays earliest", upd["first_reported_at"] == "2026-08-01")
check("update_count bumped", upd["update_count"] == 1)
check("is_developing set", upd["is_developing"] is True)
check("timeline entry appended", len(upd["source_timeline"]) == 2)

# Snapshot captures the PRE-merge arrays verbatim (the undo restores these).
check("snapshot holds pre-merge source_urls", snap["source_urls"] == ["https://www.straitstimes.com/a"])
check("snapshot holds pre-merge update_count", snap["update_count"] == 0)
check("snapshot holds pre-merge summary", snap["summary"] == "Original.")

# Older source must not push incident_date backwards.
upd2, _ = ap._compute_merge(existing, "https://www.channelnewsasia.com/old", "CNA", "earlier", "2026-07-20")
check("older source keeps incident_date", upd2["incident_date"] == "2026-08-01")

# A source already present is not appended twice (undo would otherwise drop it).
existing_dup = {**existing, "source_urls": ["https://www.straitstimes.com/a", "https://www.channelnewsasia.com/b"]}
upd3, _ = ap._compute_merge(existing_dup, "https://www.channelnewsasia.com/b", "CNA", "dup", "2026-08-05")
check("duplicate source not re-appended", upd3["source_urls"] == existing_dup["source_urls"])

# A dateless candidate never corrupts the dates.
upd4, _ = ap._compute_merge(existing, "https://www.channelnewsasia.com/undated", "CNA", "no date", None)
check("dateless merge keeps incident_date", upd4["incident_date"] == "2026-08-01")

# auto-merge never touches the summary.
check("merge never writes summary", "summary" not in upd)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
