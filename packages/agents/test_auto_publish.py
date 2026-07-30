"""
Auto-publish gate + publish path (req #3).

Run: ./.venv/Scripts/python.exe test_auto_publish.py

The mock client mirrors postgrest's REAL builder surface rather than accepting
any attribute. That is deliberate: the first version of _publish() chained
.single() after .insert(), which postgrest's insert builder does not have. A
permissive mock passes that happily; the live path raised AttributeError, which
_publish's own except swallowed as "publish_failed" — auto-publish would have
looked like it was running while never publishing a single card. A mock that
lies about its interface is worse than no mock.
"""

import importlib
from unittest import mock

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# ── Mock Supabase matching postgrest's real builder surface ────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _InsertBuilder:
    """Mirrors SyncQueryRequestBuilder: has .select() and .execute(), NO .single()."""

    def __init__(self, table, payload, sink):
        self._table, self._payload, self._sink = table, payload, sink

    def select(self, *_a, **_kw):
        return self

    def execute(self):
        self._sink.setdefault(self._table, []).append(self._payload)
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        return _Result([{**r, "id": f"new-{self._table}-id"} for r in rows])


class _UpdateBuilder:
    def __init__(self, table, payload, sink):
        self._table, self._payload, self._sink = table, payload, sink

    def eq(self, *_a, **_kw):
        return self

    def execute(self):
        self._sink.setdefault(f"{self._table}:updates", []).append(self._payload)
        return _Result([{"id": "x"}])


class _SelectBuilder:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_kw):      return self
    def eq(self, *_a, **_kw):          return self
    def in_(self, *_a, **_kw):         return self
    def is_(self, *_a, **_kw):         return self
    def gte(self, *_a, **_kw):         return self
    def lt(self, *_a, **_kw):          return self
    def order(self, *_a, **_kw):       return self
    def limit(self, *_a, **_kw):       return self
    def execute(self):                 return _Result(self._rows)


class _Table:
    def __init__(self, name, rows, sink):
        self._name, self._rows, self._sink = name, rows, sink

    def select(self, *_a, **_kw):  return _SelectBuilder(self._rows.get(self._name, []))
    def insert(self, payload, **_kw): return _InsertBuilder(self._name, payload, self._sink)
    def update(self, payload, **_kw): return _UpdateBuilder(self._name, payload, self._sink)


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.writes = {}

    def table(self, name):
        return _Table(name, self.rows, self.writes)


def _row(**over):
    row = {
        "id": "queue-1",
        "status": "pending",
        "agent_confidence": 0.97,
        "proposed_title": "Cat rescued from Yishun void deck",
        "proposed_summary": "A cat was rescued.",
        "proposed_classification": "heart",
        "proposed_severity": 2,
        "proposed_slug": "cat-rescued-jul-2026",
        "source_url": "https://mothership.sg/2026/07/cat",
        "source_type": "msm",
        "corroboration_count": 1,
        "edmw_signal_count": 0,
        "raw_content": {
            "source_urls": ["https://mothership.sg/2026/07/cat"],
            "date": "2026-07-19",
            "block_number": "123",
            "area_name": "Yishun Ring Road",
        },
    }
    row.update(over)
    return row


ap = importlib.import_module("ops.auto_publish")

# Deterministic stand-in for check_source_urls. Mirrors the real contract
# exactly: signal URLs are DROPPED from kept, but unapproved URLs stay IN kept
# and are merely flagged (stripping one could take an incident's last source and
# break guardrail #1 — see source_allowlist's module docstring).
def _fake_allowlist(urls, domains=None):
    kept, dropped_signal, unapproved = [], [], []
    for u in urls or []:
        if "hardwarezone" in u:
            dropped_signal.append(u)
            continue
        if "mothership.sg" not in u:
            unapproved.append(u)
        kept.append(u)
    return {"kept": kept, "dropped_signal": dropped_signal, "unapproved": unapproved}


print("\n=== eligibility gates ===")
with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist):
    ok, reason = ap.check_eligibility(_row(), 0.95)
    check("clean high-confidence row is eligible", ok and reason == "eligible")

    # ── Oversized merge: held for review, but only until it is EARNED ────────
    _big = _row()
    _big["raw_content"] = {**_big["raw_content"], "_oversized_cluster": 9}

    ok, reason = ap.check_eligibility(_big, 0.95)
    check("an oversized merge is held for review by default",
          not ok and reason == "oversized_cluster_unproven")

    ok, reason = ap.check_eligibility(_big, 0.95, oversized_merges_trusted=True)
    check("...and auto-publishes once the grouper has earned it",
          ok and reason == "eligible")

    ok, reason = ap.check_eligibility(_row(), 0.95, oversized_merges_trusted=True)
    check("earned trust does not weaken any other gate",
          ok and reason == "eligible")

    # Trust must not let an oversized row bypass a real guardrail.
    _big_bad = _row(agent_confidence=0.10)
    _big_bad["raw_content"] = {**_big_bad["raw_content"], "_oversized_cluster": 9}
    ok, reason = ap.check_eligibility(_big_bad, 0.95, oversized_merges_trusted=True)
    check("a trusted oversized row still fails the confidence gate",
          not ok and reason == "below_threshold")

    ok, reason = ap.check_eligibility(_row(agent_confidence=0.94), 0.95)
    check("0.94 is below a 0.95 threshold", not ok and reason == "below_threshold")

    ok, reason = ap.check_eligibility(_row(agent_confidence=0.95), 0.95)
    check("0.95 exactly clears the bar (>=, not >)", ok)

    ok, reason = ap.check_eligibility(_row(agent_confidence=None), 0.95)
    check("null confidence never auto-publishes", not ok and reason == "no_confidence")

    ok, reason = ap.check_eligibility(_row(status="update"), 0.95)
    check("update rows route to review", not ok and reason == "not_pending")

    r = _row(); r["raw_content"] = {**r["raw_content"], "notification_type": "pattern_alert"}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("sentinel/notification rows never publish", not ok and reason == "notification_row")

    # Guardrail #1
    r = _row(source_url=None); r["raw_content"] = {**r["raw_content"], "source_urls": []}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("guardrail #1: no source URL blocks", not ok and reason == "no_source_url")

    # Guardrail #2 — an EDMW-only row has no quotable source once filtered
    r = _row(source_url="https://forums.hardwarezone.com.sg/t/1")
    r["raw_content"] = {**r["raw_content"],
                        "source_urls": ["https://forums.hardwarezone.com.sg/t/1"]}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("guardrail #2: signal-only row blocks", not ok and reason == "no_approved_source_after_filter")

    # Guardrail #4
    r = _row(proposed_summary="[POLITICAL CONTENT DETECTED — REJECT] something")
    ok, reason = ap.check_eligibility(r, 0.95)
    check("guardrail #4: political marker blocks", not ok and reason == "political_marker")

    # QA H3
    r = _row(); r["raw_content"] = {**r["raw_content"], "date": ""}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("QA H3: dateless row blocks", not ok and reason == "no_real_date")

    r = _row(); r["raw_content"] = {**r["raw_content"], "_date_fallback": True}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("QA H3: _date_fallback row blocks", not ok and reason == "date_fallback")

    # Verifiability
    r = _row(source_url="https://unknown-blog.example/x")
    r["raw_content"] = {**r["raw_content"], "source_urls": ["https://unknown-blog.example/x"]}
    ok, reason = ap.check_eligibility(r, 0.95)
    check("unapproved domain routes to review", not ok and reason == "unapproved_source_domain")

    r = _row(proposed_title="   ")
    ok, reason = ap.check_eligibility(r, 0.95)
    check("blank title blocks", not ok and reason == "missing_title")

print("\n=== publish path (the .single() regression) ===")
with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist), \
     mock.patch("classifiers.geocoding.geocode_incident", lambda *a, **kw: (1.43, 103.83)), \
     mock.patch("ops.activity._client", lambda *_a, **_kw: None), \
     mock.patch("ops.notify.notify", lambda *a, **kw: {"status": "disabled"}):

    client = FakeClient({"war_room_queue": [_row()]})
    stats = ap.run(supabase_client=client, dry_run=False, trigger="manual")

    check("exactly one incident published", stats["published"] == 1)
    check("no failures on the happy path", stats["failed"] == 0)

    incidents = client.writes.get("incidents", [])
    check("incident row was actually inserted", len(incidents) == 1)
    if incidents:
        inc = incidents[0]
        check("published live", inc.get("is_published") is True)
        check("real article date, not today", inc.get("incident_date") == "2026-07-19")
        check("only allowlisted URLs survive", inc.get("source_urls") == ["https://mothership.sg/2026/07/cat"])
        check("geocoded coordinates carried through", inc.get("latitude") == 1.43)
        check("confidence preserved on the incident", inc.get("agent_confidence") == 0.97)

    q_updates = client.writes.get("war_room_queue:updates", [])
    check("queue row closed out (idempotency guard)",
          len(q_updates) == 1 and q_updates[0].get("status") == "approved")

    signals = client.writes.get("training_signals", [])
    check("autonomous decision logged as a training signal", len(signals) == 1)
    if signals:
        check("action=auto_approve", signals[0].get("action") == "auto_approve")
        check("decided_by=agent (excluded from operator agreement rate)",
              signals[0].get("decided_by") == "agent")

print("\n=== dry run writes nothing ===")
with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist), \
     mock.patch("ops.activity._client", lambda *_a, **_kw: None), \
     mock.patch("ops.notify.notify", lambda *a, **kw: {"status": "disabled"}):
    client = FakeClient({"war_room_queue": [_row()]})
    stats = ap.run(supabase_client=client, dry_run=True, trigger="manual")
    check("dry run reports what it would publish", stats["published"] == 1)
    check("dry run inserts no incident", "incidents" not in client.writes)
    check("dry run does not close the queue row", "war_room_queue:updates" not in client.writes)

print("\n=== refuses to act when it cannot record the decision ===")


class _BlockedSignalsClient(FakeClient):
    """training_signals has no `decided_by` — i.e. migration 011 not applied."""

    def table(self, name):
        if name == "training_signals":
            class _Blocked:
                def select(self, *_a, **_kw): return self
                def limit(self, *_a, **_kw):  return self
                def execute(self):
                    raise RuntimeError("column training_signals.decided_by does not exist")
            return _Blocked()
        return super().table(name)


with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist), \
     mock.patch("ops.activity._client", lambda *_a, **_kw: None), \
     mock.patch("ops.notify.notify", lambda *a, **kw: {"status": "disabled"}):
    client = _BlockedSignalsClient({"war_room_queue": [_row()]})
    stats = ap.run(supabase_client=client, dry_run=False, trigger="manual")
    check("publishes nothing when the decision cannot be logged", stats["published"] == 0)
    check("no incident row written", "incidents" not in client.writes)
    check("queue row left pending for the operator", "war_room_queue:updates" not in client.writes)
    check("reports why it stood down", "skipped_all" in stats)

print("\n=== blast radius ===")
with mock.patch("classifiers.source_allowlist.check_source_urls", _fake_allowlist), \
     mock.patch("classifiers.geocoding.geocode_incident", lambda *a, **kw: None), \
     mock.patch("ops.activity._client", lambda *_a, **_kw: None), \
     mock.patch("ops.notify.notify", lambda *a, **kw: {"status": "disabled"}):
    many = [_row(id=f"q-{i}", proposed_slug=f"s-{i}") for i in range(60)]
    client = FakeClient({"war_room_queue": many})
    stats = ap.run(supabase_client=client, dry_run=False, trigger="manual")
    check(f"per-run cap holds at {ap.MAX_AUTO_PUBLISH_PER_RUN}",
          stats["published"] == ap.MAX_AUTO_PUBLISH_PER_RUN)

print("\n=== oversized-merge trust (the exit condition) ===")


class _TrustClient:
    """Returns a fixed processed-queue history for oversized_merge_trust()."""
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom

    def table(self, name):
        outer = self

        class _Q:
            def select(self, *a, **k):  return self
            def in_(self, *a, **k):     return self
            def order(self, *a, **k):   return self
            def limit(self, *a, **k):   return self
            def execute(self):
                if outer._boom:
                    raise RuntimeError("supabase down")
                return type("R", (), {"data": outer._rows})()
        return _Q()


def _hist(n_ok, n_no, noise=0):
    rows = [{"status": "approved", "raw_content": {"_oversized_cluster": 8}} for _ in range(n_ok)]
    rows += [{"status": "rejected", "raw_content": {"_oversized_cluster": 8}} for _ in range(n_no)]
    # Ordinary rows must not count towards the grouper's record either way.
    rows += [{"status": "approved", "raw_content": {"source_urls": ["u"]}} for _ in range(noise)]
    return rows


def _trusted(n_ok, n_no, noise=0):
    t, a, r = ap.oversized_merge_trust(_TrustClient(_hist(n_ok, n_no, noise)))
    return ap.oversized_merges_trusted(t, a, r), t, a, r


ok_, t, a, r = _trusted(0, 0)
print(f"    record {a}-{r}  trust {t:.2f}")
check("cold start: nothing on record -> held for review", not ok_)

ok_, t, a, r = _trusted(2, 0)
print(f"    record {a}-{r}  trust {t:.2f}")
check("2 approvals clear the RATE but not the sample floor -> still held",
      not ok_ and t >= 0.70)

ok_, t, a, r = _trusted(4, 0)
print(f"    record {a}-{r}  trust {t:.2f}")
check("4 clean approvals: one short of the sample floor -> still held", not ok_)

ok_, t, a, r = _trusted(5, 0)
print(f"    record {a}-{r}  trust {t:.2f}")
check("5 clean approvals: EARNED — gate lifts itself, no human action", ok_)

ok_, t, a, r = _trusted(5, 1)
print(f"    record {a}-{r}  trust {t:.2f}")
check("one rejection after that: gate RE-ARMS automatically", not ok_)

ok_, t, a, r = _trusted(14, 1)
print(f"    record {a}-{r}  trust {t:.2f}")
check("a long good record outweighs one bad call: trusted again", ok_)

ok_, t, a, r = _trusted(5, 0, noise=40)
check("ordinary (non-oversized) rows do not inflate the grouper's record", (a, r) == (5, 0))

t, a, r = ap.oversized_merge_trust(_TrustClient([], boom=True))
check("unreadable history -> trust 0.0, held for review, never raises",
      t == 0.0 and (a, r) == (0, 0) and not ap.oversized_merges_trusted(t, a, r))

check("granting merge autonomy uses a stricter bar than a confidence nudge",
      ap.OVERSIZED_TRUST_THRESHOLD >= 0.80 and ap.OVERSIZED_MIN_SAMPLES >= 5)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
