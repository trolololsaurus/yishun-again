"""
Checks that auto-publish Telegram reports use current review cards, group
repeated titles, and deduplicate using stable queue ids.

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


def run(rows, merged_titles=None, published_ids=None, merged_ids=None):
    with mock.patch.object(ap, "notify") as m:
        ap._notify_review_queue(
            rows, merged_titles or [], client=None,
            published_queue_ids=published_ids or [], merged_queue_ids=merged_ids or [],
        )
        return m


print("\n=== concise pass summary ===")

# A live-incident mutation must be reported even after the queue is empty.
m = run(rows=[], merged_titles=["Cyclist update merged"], merged_ids=["q-merge"])
check("merge-only pass with empty queue sends a notification", m.called)
if m.called:
    subject, body = m.call_args.args[1:3]
    check("title does not claim cards need review", "need review" not in subject)
    check("summary reports current empty War Room queue", "Needs review now: 0" in body)
    check("merged title is included for audit", "Cyclist update merged" in body)

m = run(rows=[], merged_titles=["Cyclist update merged", "Cyclist update merged"],
        merged_ids=["q-merge-1", "q-merge-2"])
check("repeated merge titles are grouped with their action count",
      "Cyclist update merged (2 updates)" in m.call_args.args[2])

m = run(rows=[], published_ids=["q-published"])
check("successful publication is reported", m.called)
if m.called:
    body = m.call_args.args[2]
    check("publication count is reported without repeating incident titles",
          "Published this pass: 1" in body and "Auto-published without review" not in body)
    check("empty live review queue is stated explicitly", "Needs review now: 0" in body)

m = run(rows=[])
check("empty pass with no actions stays silent", not m.called)

print("\n=== current queue and deduplication ===")

pending = {"id": "q1", "status": "pending", "raw_content": {},
           "agent_confidence": 0.97, "proposed_title": "High confidence draft",
           "_hold_reason": "possible_published_duplicate"}
m = run(rows=[pending])
check("current pending card triggers a review notification", m.called)
if m.called:
    subject, body = m.call_args.args[1:3]
    check("subject counts current queue cards", "1 card" in subject)
    check("specific hold reason is readable", "possible duplicate of a published incident" in body)
    check("summary does not include historical eligibility totals", "eligible:" not in body)

m_same = run(rows=[pending])
m_new = run(rows=[pending, {**pending, "id": "q2", "proposed_title": "Another draft"}])
key_same = m_same.call_args.kwargs["dedup_key"]
key_unchanged = run(rows=[pending]).call_args.kwargs["dedup_key"]
key_new = m_new.call_args.kwargs["dedup_key"]
check("same pending-card state gets the same dedup key", key_same == key_unchanged)
check("new queue id changes the dedup key", key_same != key_new)

duplicates = [
    {"id": "q1", "status": "pending", "raw_content": {},
     "agent_confidence": 0.97, "proposed_title": "Cat rescued from Yishun void deck",
     "_hold_reason": "per_run_cap_hit"},
    {"id": "q2", "status": "pending", "raw_content": {},
     "agent_confidence": 0.96, "proposed_title": "Cat rescued from Yishun void deck",
     "_hold_reason": "per_run_cap_hit"},
]
m = run(rows=duplicates)
body = m.call_args.args[2]
check("repeated card titles are grouped into one human-readable line",
      body.count("Cat rescued from Yishun void deck") == 1 and "(2 cards)" in body)
check("grouped line retains the hold reason", "per-pass auto-publish limit reached" in body)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
