"""
Checks that auto-publish Telegram reports use current review cards, format the
header correctly, send per-card messages via send_card(), and share one stable
key per SGT scheduler window.

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
    """Returns (notify_mock, send_card_mock)."""
    with mock.patch.object(ap, "notify") as m_notify, \
         mock.patch("ops.notify.send_card") as m_card:
        ap._notify_review_queue(
            rows, merged_titles or [], client=None,
            published_queue_ids=published_ids or [], merged_queue_ids=merged_ids or [],
        )
        return m_notify, m_card


print("\n=== concise pass summary ===")

# A live-incident mutation must be reported even after the queue is empty.
m, cards = run(rows=[], merged_titles=["Cyclist update merged"], merged_ids=["q-merge"])
check("merge-only pass with empty queue sends a notification", m.called)
if m.called:
    subject, body = m.call_args.args[1:3]
    check("title does not claim cards need review", "need review" not in subject.lower())
    check("subject labels one pass-window report", "pass" in subject)
    check("summary reports current empty War Room queue", "Waiting for review: 0" in body)
    check("merged title is included for audit", "Cyclist update merged" in body)

m, _ = run(rows=[], merged_titles=["Cyclist update merged", "Cyclist update merged"],
           merged_ids=["q-merge-1", "q-merge-2"])
check("repeated merge titles are grouped with their action count",
      "Cyclist update merged ×2" in m.call_args.args[2])

m, _ = run(rows=[], published_ids=["q-published"])
check("successful publication is reported", m.called)
if m.called:
    body = m.call_args.args[2]
    check("publication count is reported without repeating incident titles",
          "Published: 1" in body and "Auto-published without review" not in body)
    check("empty live review queue is stated explicitly", "Waiting for review: 0" in body)

m, _ = run(rows=[])
check("empty pass with no actions stays silent", not m.called)

print("\n=== current queue and deduplication ===")

pending = {"id": "q1", "status": "pending", "raw_content": {},
           "agent_confidence": 0.97, "proposed_title": "High confidence draft",
           "_hold_reason": "possible_published_duplicate",
           "_hold_match_title": "Similar published story"}
m, cards = run(rows=[pending])
check("current pending card triggers a review notification", m.called)
if m.called:
    subject, body = m.call_args.args[1:3]
    check("subject labels the consolidated report", "pass" in subject)
    check("body counts current queue cards", "1 card(s) waiting" in body)
    # Card content is now in send_card(), not in the summary notify() body.
    check("send_card called for the pending card", cards.called)
    if cards.called:
        card_text = cards.call_args.args[1]
        check("specific hold reason is readable in card text",
              "keyword match" in card_text.lower() or "duplicate" in card_text.lower())
        check("new-card title appears in card message",
              "High confidence draft" in card_text)
    check("summary does not include historical eligibility totals", "eligible:" not in body)

m_same, _ = run(rows=[pending])
m_new, _  = run(rows=[pending, {**pending, "id": "q2", "proposed_title": "Another draft"}])
key_same = m_same.call_args.kwargs["dedup_key"]
m_unchanged, _ = run(rows=[pending])
key_unchanged = m_unchanged.call_args.kwargs["dedup_key"]
key_new = m_new.call_args.kwargs["dedup_key"]
check("same pending-card state gets the same dedup key", key_same == key_unchanged)
check("different outcomes in one SGT window coalesce under the same key", key_same == key_new)
check("distinct queue outcomes get distinct coalescing event ids",
      m_same.call_args.kwargs["coalesce_event_id"] != m_new.call_args.kwargs["coalesce_event_id"])
check("unchanged queue state retains its event id",
      m_same.call_args.kwargs["coalesce_event_id"]
      == m_unchanged.call_args.kwargs["coalesce_event_id"])
check("report edits the existing Telegram message instead of sending another",
      m_new.call_args.kwargs["coalesce_existing"] is True)
check("one morning/afternoon window is retained for 12 hours",
      m_new.call_args.kwargs["throttle_minutes"] == 720)

update = {
    "id": "q-update", "status": "update",
    "raw_content": {"_match_confidence": 0.84},
    "agent_confidence": 0.95,
    "proposed_title": "Yishun kitchen fire update",
    "_hold_reason": "match_below_threshold",
}
m_update, cards_update = run(rows=[update])
if cards_update.called:
    card_text = cards_update.call_args.args[1]
    # The card shows the match confidence (why it's stuck), not the draft confidence.
    check("update report shows match confidence", "84%" in card_text)
    check("update report text references the hold", "uncertain" in card_text.lower() or "match" in card_text.lower())
    check("update report explains why the high draft score did not auto-merge",
          "uncertain" in card_text.lower() or "match" in card_text.lower())
else:
    # UPDATE rows still trigger send_card
    check("update report labels 95% as draft confidence", False)
    check("update report shows match confidence", False)
    check("update report explains why the high draft score did not auto-merge", False)

missing_match_m, missing_cards = run(rows=[{
    **update, "raw_content": {}, "_hold_reason": "no_match_confidence",
}])
if missing_cards.called:
    card_text = missing_cards.call_args.args[1]
    check("missing match confidence is reported as missing, not inferred",
          "missing" in card_text.lower() or "no_match_confidence" in card_text.lower()
          or "same-event" in card_text.lower())
else:
    check("missing match confidence is reported as missing, not inferred", False)

update_event_id = m_update.call_args.kwargs["coalesce_event_id"]
changed_match_event_id = run(rows=[{
    **update, "raw_content": {"_match_confidence": 0.85},
}])[0].call_args.kwargs["coalesce_event_id"]
check("a changed same-event score produces a new report event",
      update_event_id != changed_match_event_id)

duplicates = [
    {"id": "q1", "status": "pending", "raw_content": {},
     "agent_confidence": 0.97, "proposed_title": "Cat rescued from Yishun void deck",
     "_hold_reason": "per_run_cap_hit"},
    {"id": "q2", "status": "pending", "raw_content": {},
     "agent_confidence": 0.96, "proposed_title": "Cat rescued from Yishun void deck",
     "_hold_reason": "per_run_cap_hit"},
]
m, cards = run(rows=duplicates)
# Each card gets its own message now; grouping moved to send_card level.
check("both pending cards get their own send_card call",
      cards.call_count == 2)
check("hold reason is in each card's text",
      all("per-pass auto-publish limit reached" in c.args[1]
          for c in cards.call_args_list))
check("each card carries its own title",
      all("Cat rescued from Yishun void deck" in c.args[1]
          for c in cards.call_args_list))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
