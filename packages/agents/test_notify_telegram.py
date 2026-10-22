"""
Self-contained test for the Telegram notify transport (ops/notify.py). No pytest.
Run: .venv/Scripts/python.exe test_notify_telegram.py

Offline: mocks httpx.post so no real Telegram call is made, and uses a fake
Supabase-shaped client so the ledger writes are asserted without a DB.
"""
import importlib
import logging
import os
from unittest import mock

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


class _FakeTable:
    """Mimics the postgrest-py builder chain used by _record/_update/_recently_sent."""
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._filters = {}
        self._in_filters = {}

    def insert(self, row):
        row = dict(row)
        row["id"] = f"row{len(self.store[self.name]) + 1}"
        self.store[self.name].append(row)
        self._last = row
        return self

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self._filters[key] = val
        return self

    def in_(self, key, values):
        self._in_filters[key] = values
        return self

    def gte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def execute(self):
        if hasattr(self, "_patch"):
            for row in self.store[self.name]:
                if all(row.get(k) == v for k, v in self._filters.items()):
                    row.update(self._patch)
            del self._patch
            return mock.MagicMock(data=None)
        if self._filters:
            rows = [r for r in self.store[self.name]
                    if all(r.get(k) == v for k, v in self._filters.items())
                    and all(r.get(k) in values for k, values in self._in_filters.items())]
            return mock.MagicMock(data=rows)
        return mock.MagicMock(data=[self._last] if hasattr(self, "_last") else [])


class FakeClient:
    def __init__(self):
        self.store = {"notifications": []}

    def table(self, name):
        return _FakeTable(self.store, name)


def _fake_response(status_code=200, message_id=42, text="error body"):
    r = mock.MagicMock()
    r.status_code = status_code
    r.text = text
    r.json.return_value = {"result": {"message_id": message_id}}
    return r


notify_mod = importlib.import_module("ops.notify")

print("telegram transport tests:")

# ── disabled when env vars missing ──────────────────────────────────────────
with mock.patch.dict(os.environ, {}, clear=False):
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    client = FakeClient()
    result = notify_mod.notify("anomaly", "subj", "body", client=client, dedup_key="k1")
    check("missing token -> disabled, never raises", result["status"] == "disabled")
    check("disabled row still recorded in ledger", len(client.store["notifications"]) == 1)
    check("disabled row status is 'disabled'", client.store["notifications"][0]["status"] == "disabled")

# ── sent path: ledger-first, then Telegram, then status update ─────────────
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    with mock.patch("httpx.post", return_value=_fake_response(200, message_id=555)) as post:
        result = notify_mod.notify("anomaly", "subj", "hello", client=client, dedup_key="k2")
    check("sent -> status sent", result["status"] == "sent")
    row = client.store["notifications"][0]
    check("ledger written BEFORE send (row exists at all)", row["subject"] == "subj")
    check("ledger updated to sent with provider_id", row["status"] == "sent" and row["provider_id"] == "555")
    check("posts to the Telegram sendMessage endpoint", "sendMessage" in post.call_args.args[0])
    check("chat_id passed through to Telegram", post.call_args.kwargs["json"]["chat_id"] == "999")
    sent = post.call_args.kwargs["json"]
    check("subject sent as a bold HTML headline", sent["text"].startswith("<b>subj</b>"))
    check("parse_mode is HTML", sent.get("parse_mode") == "HTML")

# ── same-window report coalescing edits one Telegram message ────────────────
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    with mock.patch("httpx.post", side_effect=[
        _fake_response(200, message_id=556), _fake_response(200, message_id=556),
    ]) as post:
        first = notify_mod.notify(
            "review_queue", "Yishun Again — auto-publish report (morning SGT window)",
            "First pass merged: Incident A\n\nReview: https://example.test/queue",
            client=client, dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="event-a",
        )
        second = notify_mod.notify(
            "review_queue", "Yishun Again — auto-publish report (morning SGT window)",
            "Second pass merged: Incident B\n\nReview: https://example.test/queue",
            client=client, dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="event-b",
        )
        third = notify_mod.notify(
            "review_queue", "Yishun Again — auto-publish report (morning SGT window)",
            "Second pass merged: Incident B\n\nReview: https://example.test/queue",
            client=client, dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="event-b",
        )
    check("first window report sends one Telegram message", first["status"] == "sent")
    check("later pass edits the existing Telegram message", second["status"] == "updated")
    check("coalescing uses editMessageText, not a second sendMessage",
          post.call_count == 2 and post.call_args.args[0].endswith("/editMessageText"))
    master = next(row for row in client.store["notifications"] if row["status"] == "sent")
    check("one sent ledger report retains both distinct pass results",
          len([row for row in client.store["notifications"] if row["status"] == "sent"]) == 1
          and "Incident A" in master["body"] and "Incident B" in master["body"])
    event_rows = [row for row in client.store["notifications"]
                  if ":event:" in row["dedup_key"] and row["status"] == "suppressed"]
    check("unique pass-event fingerprints make retries idempotent",
          len(event_rows) == 2
          and any(row["dedup_key"].endswith(":event:event-a") for row in event_rows)
          and any(row["dedup_key"].endswith(":event:event-b") for row in event_rows))
    check("an identical repeat does not edit or send again", third["status"] == "suppressed"
          and post.call_count == 2)

with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    same_title = "Auto-merged updates: 1.\nMerged updates:\n  • Cat rescued from Yishun void deck"
    with mock.patch("httpx.post", side_effect=[
        _fake_response(200, message_id=557), _fake_response(200, message_id=557),
    ]) as post:
        notify_mod.notify(
            "review_queue", "Yishun Again — auto-publish report (morning SGT window)",
            same_title, client=client, dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="queue-a",
        )
        result = notify_mod.notify(
            "review_queue", "Yishun Again — auto-publish report (morning SGT window)",
            same_title, client=client, dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="queue-b",
        )
    master = next(row for row in client.store["notifications"] if row["status"] == "sent")
    check("distinct identical-headline updates are both kept in one report",
          result["status"] == "updated"
          and master["body"].count("Cat rescued from Yishun void deck") == 2
          and post.call_count == 2)

with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    client.store["notifications"].append({
        "id": "inflight", "kind": "review_queue", "dedup_key": "review_queue:2026-10-22:morning",
        "recipient": "999", "subject": "report", "body": "first result",
        "status": "pending", "provider_id": None,
    })
    with mock.patch("httpx.post") as post:
        result = notify_mod.notify(
            "review_queue", "report", "second result", client=client,
            dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="event-pending",
        )
    check("an in-flight report cannot trigger a second Telegram bubble",
          result["status"] == "pending" and not post.called)

with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999",
                                  "NOTIFY_ENABLED": "false"}):
    client = FakeClient()
    client.store["notifications"].append({
        "id": "sent-report", "kind": "review_queue",
        "dedup_key": "review_queue:2026-10-22:morning", "recipient": "999",
        "subject": "report", "body": "first result", "status": "sent", "provider_id": "558",
    })
    with mock.patch("httpx.post") as post:
        result = notify_mod.notify(
            "review_queue", "report", "second result", client=client,
            dedup_key="review_queue:2026-10-22:morning",
            throttle_minutes=720, coalesce_existing=True, coalesce_event_id="event-disabled",
        )
    check("NOTIFY_ENABLED=false also blocks report edits", result["status"] == "disabled"
          and not post.called)

# HTTPX includes request URLs in INFO logs; bot credentials must never reach logs.
record = logging.LogRecord(
    "httpx", logging.INFO, "test", 1,
    "HTTP Request: POST https://api.telegram.org/botsecret-token/sendMessage 200",
    (), None,
)
notify_mod._TelegramUrlRedactionFilter().filter(record)
check("Telegram bot token is redacted from request logs",
      "secret-token" not in record.getMessage()
      and "https://api.telegram.org/bot[redacted]/sendMessage" in record.getMessage())

# ── readability: body is HTML-escaped, HTML failure falls back to plain text ──
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    with mock.patch("httpx.post", return_value=_fake_response(200)) as post:
        notify_mod.notify("anomaly", "A & B <script>", "body with <tag> & amp",
                          client=client, dedup_key="esc")
    text = post.call_args.kwargs["json"]["text"]
    check("subject html-escaped inside bold tags", "<b>A &amp; B &lt;script&gt;</b>" in text)
    check("body html-escaped", "&lt;tag&gt; &amp; amp" in text)

    # HTML rejected (400) -> one plain-text retry, no parse_mode, still lands.
    client = FakeClient()
    responses = [_fake_response(400, text="can't parse entities"), _fake_response(200, message_id=77)]
    with mock.patch("httpx.post", side_effect=responses) as post:
        r = notify_mod.notify("anomaly", "subj", "body", client=client, dedup_key="fallback")
    check("HTML failure falls back to plain text and still sends", r["status"] == "sent")
    check("fallback retried exactly once (2 posts)", post.call_count == 2)
    check("fallback send drops parse_mode", "parse_mode" not in post.call_args.kwargs["json"])

# ── failed send is recorded, never raises ───────────────────────────────────
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    with mock.patch("httpx.post", return_value=_fake_response(403, text="Forbidden: bot was blocked")):
        result = notify_mod.notify("anomaly", "subj", "hello", client=client, dedup_key="k3")
    check("HTTP 403 from Telegram -> status failed (not raised)", result["status"] == "failed")
    check("failure reason captured", "403" in (result["error"] or ""))

# ── dedup / throttle unaffected by the transport swap ───────────────────────
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    with mock.patch("httpx.post", return_value=_fake_response(200)):
        notify_mod.notify("anomaly", "subj", "hello", client=client, dedup_key="dup", throttle_minutes=60)
    with mock.patch("httpx.post", return_value=_fake_response(200)) as post2:
        result2 = notify_mod.notify("anomaly", "subj", "hello", client=client, dedup_key="dup", throttle_minutes=60)
    check("second send with same dedup_key is suppressed", result2["status"] == "suppressed")
    check("suppressed send never calls Telegram", not post2.called)

# ── truncation: a body over TELEGRAM_MAX_CHARS is trimmed, not rejected ─────
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    client = FakeClient()
    huge = "x" * 5000
    with mock.patch("httpx.post", return_value=_fake_response(200)) as post3:
        result3 = notify_mod.notify("anomaly", "subj", huge, client=client, dedup_key="k4")
    sent_text = post3.call_args.kwargs["json"]["text"]
    check("oversized body still sends (degrades, doesn't block)", result3["status"] == "sent")
    check("sent text truncated to Telegram's cap", len(sent_text) <= notify_mod.TELEGRAM_MAX_CHARS)
    check("truncation marker present", "truncated" in sent_text)
    ledger_body = client.store["notifications"][0]["body"]
    check("full untruncated body still kept in the ledger", ledger_body == huge)

# ── Muting: recorded to the ledger but never pushed ─────────────────────────
print("mute tests:")

# Default muted prefixes cover integrity / maintenance-digest / political.
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999"}):
    importlib.reload(notify_mod)  # re-read MUTED_PREFIXES from env (default)
    for dedup in ("integrity:2026-08-28", "maintenance:2026-08-28", "political:https://x/a"):
        client = FakeClient()
        with mock.patch("httpx.post") as post:
            r = notify_mod.notify("anomaly", "subj", "body", client=client, dedup_key=dedup)
        row = client.store["notifications"][0]
        check(f"{dedup.split(':')[0]} muted -> status muted, not pushed",
              r["status"] == "muted" and not post.called)
        check(f"{dedup.split(':')[0]} still recorded in ledger (as disabled)",
              row["status"] == "disabled" and "muted" in (row["error"] or ""))

    # Push-worthy classes are NOT muted — including schema_blocked, which shares
    # kind='maintenance' with the muted digest but has its own dedup prefix.
    for dedup in ("review_queue:2026-08-28", "supervisor:2026-08-28:cna",
                  "health:supabase", "schema_blocked:auto_publish"):
        client = FakeClient()
        with mock.patch("httpx.post", return_value=_fake_response(200)) as post:
            r = notify_mod.notify("maintenance", "subj", "body", client=client, dedup_key=dedup)
        check(f"{dedup.split(':')[0]} NOT muted -> actually sent",
              r["status"] == "sent" and post.called)

# Env override can clear muting entirely.
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "999",
                                  "NOTIFY_MUTED_PREFIXES": ""}):
    importlib.reload(notify_mod)
    client = FakeClient()
    with mock.patch("httpx.post", return_value=_fake_response(200)) as post:
        r = notify_mod.notify("anomaly", "subj", "body", client=client, dedup_key="integrity:2026-08-28")
    check("empty NOTIFY_MUTED_PREFIXES un-mutes integrity", r["status"] == "sent" and post.called)

importlib.reload(notify_mod)  # restore module defaults for any later import

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
