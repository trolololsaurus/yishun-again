"""
Guards ops/auto_publish.py::_notify_review_queue against the "silent merge"
bug: before this fix the function returned early whenever nothing was
`pending_for_review`, so a pass that auto-merged updates into live incidents
but also fully cleared the review queue sent NO Telegram message at all —
exactly the case AUTO_MERGE_ENABLED makes routine. It also hashed only
pending-card ids into the dedup signature, so two different merge-only passes
would have deduped against each other under the 1440-minute throttle.

Run: .venv/Scripts/python.exe test_notify_review_queue.py
"""
import importlib
import logging
from unittest import mock

logging.disable(logging.CRITICAL)

ap = importlib.import_module("ops.auto_publish")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def run(rows, stats, threshold, published_titles, merged_titles):
    with mock.patch.object(ap, "notify") as m:
        ap._notify_review_queue(rows, stats, threshold, published_titles, merged_titles, client=None)
        return m


empty_stats = {"reasons": {}}

# 1. Merge-only pass, nothing left pending -> must still notify.
m = run(rows=[], stats=empty_stats, threshold=0.95,
        published_titles=[], merged_titles=["Cyclist update merged"])
check("merge-only pass with empty queue still sends a notification", m.called)
if m.called:
    _, kwargs = m.call_args
    args = m.call_args.args
    subject = args[1] if len(args) > 1 else kwargs.get("subject")
    body = args[2] if len(args) > 2 else kwargs.get("body")
    check("subject mentions the merge, not '0 card(s) need review'",
          "merged" in subject.lower() and "0 card" not in subject.lower())
    check("body lists the merged title", "Cyclist update merged" in body)

# 2. Nothing pending, nothing merged, nothing published -> stays silent.
m = run(rows=[], stats=empty_stats, threshold=0.95,
        published_titles=[], merged_titles=[])
check("fully empty pass sends nothing", not m.called)

# 3. Two merge-only passes with DIFFERENT merged titles must not share a
#    dedup key (the old signature ignored merged/published titles entirely).
m1 = run(rows=[], stats=empty_stats, threshold=0.95,
         published_titles=[], merged_titles=["Story A merged"])
m2 = run(rows=[], stats=empty_stats, threshold=0.95,
         published_titles=[], merged_titles=["Story B merged"])
key1 = m1.call_args.kwargs.get("dedup_key")
key2 = m2.call_args.kwargs.get("dedup_key")
check("different merges this pass get different dedup keys", key1 != key2)

# 4. Pending-for-review behaviour is unchanged (regression guard).
pending_row = {"id": "q1", "status": "pending", "raw_content": {},
               "agent_confidence": 0.5, "proposed_title": "Low confidence draft"}
m = run(rows=[pending_row], stats=empty_stats, threshold=0.95,
        published_titles=[], merged_titles=[])
check("still-pending card triggers the review-queue notification", m.called)
if m.called:
    subject = m.call_args.args[1]
    check("subject still counts the pending card", "1 card" in subject)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
