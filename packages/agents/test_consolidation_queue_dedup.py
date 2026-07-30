"""
Self-contained test for the consolidation queue-dedup change (no pytest needed).
Run: .venv/Scripts/python.exe test_consolidation_queue_dedup.py

Covers:
  1. Candidate duplicates an UNPROCESSED queue item        -> action='skip'
  2. Candidate matches a PUBLISHED incident                -> action='update'
  3. Candidate matches nothing                             -> action='new'
  4. Queue match but only WEAK confidence                  -> action='new'
"""
from unittest import mock
import importlib
cc = importlib.import_module("consolidation.check")


def _judge(same, conf):
    """
    Batched-judge verdict. Each case below puts the record under test at index 0
    of the comparison pool, so a match is match_index=0.
    """
    return {
        "match_index": 0 if same else None,
        "match_confidence": conf if same else 0.0,
        "match_reason": "test",
        "related": [],
    }


CAND = {"title": "Motorcyclist, 36, dies after accident in Yishun",
        "summary": "A motorcyclist died after skidding on a Yishun road.",
        "url": "https://example.com/a", "incident_date": "2026-06-07"}

QUEUE_ITEM = {"id": "q-1", "title": "Motorcyclist dies in Yishun accident",
              "summary": "Motorcyclist, 36, died after a crash in Yishun.", "incident_date": "2026-06-05"}

PUB_ITEM = {"id": "p-1", "title": "Motorcyclist dies in Yishun accident",
            "summary": "Motorcyclist, 36, died after a crash in Yishun.", "incident_date": "2026-06-05"}

passed = failed = 0


def case(name, *, published, queued, judgement, expect_action, expect_matched=None):
    global passed, failed
    with mock.patch.object(cc, "_fetch_recent_published", return_value=published), \
         mock.patch.object(cc, "_fetch_recent_queue", return_value=queued), \
         mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
         mock.patch.object(cc, "_judge_batch", return_value=judgement):
        res = cc.check(CAND, supabase_client=mock.MagicMock())
    ok = res.action == expect_action and (expect_matched is None or res.matched_incident_id == expect_matched)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: action={res.action} matched={res.matched_incident_id}")
    if ok:
        passed += 1
    else:
        failed += 1


print("consolidation queue-dedup tests:")
case("1 queue duplicate -> skip",
     published=[], queued=[QUEUE_ITEM], judgement=_judge(True, 0.9), expect_action="skip")
case("2 published match -> update",
     published=[PUB_ITEM], queued=[], judgement=_judge(True, 0.9), expect_action="update", expect_matched="p-1")
case("3 no match -> new",
     published=[PUB_ITEM], queued=[QUEUE_ITEM], judgement=_judge(False, 0.1), expect_action="new")
case("4 weak queue match -> new (not skip)",
     published=[], queued=[QUEUE_ITEM], judgement=_judge(True, 0.5), expect_action="new")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
