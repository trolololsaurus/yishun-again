"""
Telegram approve: claim, release, stale sweep, sender check, edit-card guard.

Run: ./.venv/Scripts/python.exe test_claim_release.py

Offline. Notifications are muted BEFORE any pipeline import: importing it loads the
repo .env (real bot token), and an unmocked send_card posts to the operator's chat.
"""
import os

os.environ["NOTIFY_ENABLED"] = "false"

import importlib
from unittest import mock

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    """Records the last update payload and the filters it was gated on; the
    configured `cas_hit` decides whether a status-gated update 'matches' a row."""

    def __init__(self, client, table):
        self.c, self.t, self.filters, self.payload, self.mode = client, table, {}, None, "select"

    def select(self, *_a, **_k): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def lt(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def update(self, payload, **_k): self.mode, self.payload = "update", payload; return self

    def execute(self):
        if self.mode == "update":
            self.c.updates.append((self.t, self.payload, dict(self.filters)))
            return _Res([{"id": "x"}] if self.c.cas_hit else [])
        return _Res(self.c.tables.get(self.t, []))


class Client:
    def __init__(self, tables=None, cas_hit=True):
        self.tables, self.cas_hit, self.updates = tables or {}, cas_hit, []

    def table(self, name): return _Q(self, name)


class Run:
    def __init__(self): self.events = []
    def warn(self, k, m, **kw): self.events.append(("warn", k))
    def anomaly(self, k, m, **kw): self.events.append(("anomaly", k))


ap = importlib.import_module("ops.auto_publish")
ta = importlib.import_module("ops.telegram_actions")

ITEM = {"id": "q1", "proposed_slug": "cat-rescued-jul-2026", "raw_content": {}}

print("release_claim:")
c = Client(tables={"incidents": []})
check("no published incident -> released", ap.release_claim(ITEM, c) is True)
table, payload, filters = c.updates[-1]
check("release goes back to pending and clears the claim",
      payload == {"status": "pending", "incident_id": None, "processed_at": None})
check("release is CAS-gated on approved_pending", filters.get("status") == "approved_pending")

c = Client(tables={"incidents": [{"id": "inc-1"}]})
check("a published incident for the slug -> NOT released (would double-publish)",
      ap.release_claim(ITEM, c) is False and not c.updates)

c = Client(tables={"incidents": []}, cas_hit=False)
check("row already actioned elsewhere -> not released", ap.release_claim(ITEM, c) is False)


class Boom(Client):
    def table(self, name):
        if name == "incidents":
            raise RuntimeError("db down")
        return super().table(name)


check("unreadable incidents table -> fails safe, row stays locked",
      ap.release_claim(ITEM, Boom()) is False)

print("release_stale_claims:")
c = Client(tables={"war_room_queue": [{**ITEM, "id": "q-stale"}], "incidents": []})
run = Run()
check("a stale claim with no incident is released", ap.release_stale_claims(c, run) == 1)
check("and is announced as a warning", ("warn", "stale_claim_released") in run.events)

c = Client(tables={"war_room_queue": [{**ITEM, "id": "q-live"}], "incidents": [{"id": "inc-1"}]})
run = Run()
check("a stale claim WITH a published incident is left alone",
      ap.release_stale_claims(c, run) == 0 and not c.updates)
check("and raised as an anomaly for a human", ("anomaly", "stale_claim_unreleased") in run.events)

print("hold label names the unverified specifics:")
row = {"_hold_reason": "ungrounded_specifics",
       "raw_content": {"_groundedness": {"flagged": True, "checked": True,
                                         "numbers": ["52"], "proper_nouns": ["Affendi Yusoff"]}}}
label = ap._hold_label(row)
check("flagged number is on the card", "52" in label)
check("flagged proper noun is on the card", "Affendi Yusoff" in label)
check("failed checker says so",
      "check itself failed" in ap._hold_label(
          {"_hold_reason": "ungrounded_specifics",
           "raw_content": {"_groundedness": {"flagged": True, "checked": False}}}))
check("no findings stored still gives the plain label",
      "couldn't be verified" in ap._hold_label({"_hold_reason": "ungrounded_specifics", "raw_content": {}}))

print("telegram sender check:")
UUID = "123e4567-e89b-42d3-a456-426614174000"


def payload(chat=None, user=None, data=f"approve:{UUID}"):
    cq = {"id": "cb1", "data": data, "message": {"message_id": 5, "chat": {"id": chat}},
          "from": {"id": user}}
    return {"callback_query": cq}


with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "999"}), \
     mock.patch.object(ta, "answer_callback") as answer, \
     mock.patch.object(ta, "_approve") as approve:
    ta.handle_callback(payload(chat=111, user=222), client=None)
    check("a tap from another chat/user is ignored (no approve)", not approve.called)
    check("and is not even acknowledged", not answer.called)
    ta.handle_callback(payload(chat=999, user=222), client=None)
    check("a tap from the operator's chat is processed", approve.called)
    approve.reset_mock()
    ta.handle_callback(payload(chat=111, user=999), client=None)
    check("a tap from the operator's user id is processed", approve.called)

with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": ""}), \
     mock.patch.object(ta, "answer_callback"), mock.patch.object(ta, "_approve") as approve:
    ta.handle_callback(payload(chat=999, user=999), client=None)
    check("chat id unset -> fails closed", not approve.called)

print("edit-card endpoint:")
main = importlib.import_module("main")
import asyncio
from fastapi import HTTPException

with mock.patch("ops.activity._client", return_value=Client(tables={"war_room_queue": [{"status": "rejected"}]})), \
     mock.patch("ops.notify.edit_card") as edit:
    ok = asyncio.run(main.notify_edit_card({"queue_id": UUID, "text": "x"}, None))
    check("a resolved card is edited", ok == {"ok": True} and edit.called)

with mock.patch("ops.activity._client", return_value=Client(tables={"war_room_queue": [{"status": "pending"}]})), \
     mock.patch("ops.notify.edit_card") as edit:
    ok = asyncio.run(main.notify_edit_card({"queue_id": UUID, "text": "x"}, None))
    check("a still-pending card is NOT edited (would strand a live card)",
          ok == {"ok": False} and not edit.called)

for bad, why in (({"queue_id": "not-a-uuid", "text": "x"}, "non-UUID queue_id"),
                 ({"queue_id": UUID, "text": ""}, "empty text"),
                 ({"queue_id": UUID, "text": "x" * 501}, "oversized text"),
                 ({"queue_id": 5, "text": "x"}, "non-string queue_id")):
    try:
        asyncio.run(main.notify_edit_card(bad, None))
        check(f"{why} rejected", False)
    except HTTPException as exc:
        check(f"{why} rejected with 400", exc.status_code == 400)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
