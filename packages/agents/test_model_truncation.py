"""
max_tokens truncation guard (filters/model_call). Offline.

Run: .venv/Scripts/python.exe test_model_truncation.py

Before this, `stop_reason` was read nowhere in the repo. A reply cut off at
max_tokens produced invalid JSON, and _parse_json reported "No JSON object in
model response" — the same message a model returning prose produces. The
trivially fixable fault was disguised as the hard one.

Measured headroom when this was written (largest real inputs in the archive):
    _write_draft   763/2048   _classify      167/512
    _judge_batch   128/1024   _make_grouper  132/1024
Nothing truncates today; the guard is for when the inputs grow.
"""
import importlib
from unittest import mock

mc = importlib.import_module("filters.model_call")
sw = importlib.import_module("filters.stage2_writer")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class _Resp:
    def __init__(self, text, stop_reason="end_turn", output_tokens=100):
        self.content = [type("C", (), {"text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"output_tokens": output_tokens})()


class _Client:
    """Truncates while max_tokens < `succeeds_at`, then returns valid JSON."""
    def __init__(self, succeeds_at=None, text='{"ok": true}'):
        self.succeeds_at, self.text, self.calls = succeeds_at, text, []

        class _M:
            def create(_s, **kw):
                self.calls.append(kw["max_tokens"])
                if self.succeeds_at is None or kw["max_tokens"] < self.succeeds_at:
                    return _Resp("{" + '"partial": "cut off mid-str', "max_tokens", kw["max_tokens"])
                return _Resp(self.text)
        self.messages = _M()


print("create_checked — a truncated reply is never returned:\n")

c = _Client(succeeds_at=None)
try:
    mc.create_checked(c, call="t", env_var="T_MAX", model="m", max_tokens=100,
                      messages=[], system="s")
    raised = None
except mc.TruncatedResponse as exc:
    raised = exc
check("truncation raises TruncatedResponse, not a JSON error", raised is not None)
check("...naming the call", raised and raised.call == "t")
check("...the cap that was hit", raised and raised.max_tokens == 100)
check("...and the env var that fixes it", raised and "T_MAX" in str(raised), f"-> {raised}")
check("...and it says the JSON was NOT parsed", raised and "NOT parsed" in str(raised))

c = _Client(succeeds_at=1)
r = mc.create_checked(c, call="t", model="m", max_tokens=100, messages=[], system="s")
check("a complete reply passes straight through", r.content[0].text == '{"ok": true}')


class _NoStopReason:
    """Older SDK / test stub with no stop_reason attribute."""
    class _M:
        def create(self, **kw):
            return type("R", (), {"content": [type("C", (), {"text": "{}"})()]})()
    messages = _M()


r = mc.create_checked(_NoStopReason(), call="t", model="m", max_tokens=10,
                      messages=[], system="s")
check("a response with no stop_reason is treated as complete (back-compat)", r is not None)

print("\ncreate_with_headroom — recovery is one retry at double:\n")

c = _Client(succeeds_at=200)
r, retried = mc.create_with_headroom(c, call="t", env_var="T", model="m",
                                     max_tokens=100, messages=[], system="s")
check("a truncated call is retried", retried is True)
check("...exactly once", len(c.calls) == 2, f"-> {c.calls}")
check("...at DOUBLE the original cap", c.calls == [100, 200], f"-> {c.calls}")
check("...and returns the recovered reply", r.content[0].text == '{"ok": true}')

c = _Client(succeeds_at=1)
r, retried = mc.create_with_headroom(c, call="t", model="m", max_tokens=100,
                                     messages=[], system="s")
check("a call that fits is not retried", retried is False and len(c.calls) == 1)

c = _Client(succeeds_at=None)      # truncates at every cap
try:
    mc.create_with_headroom(c, call="t", env_var="T", model="m", max_tokens=100,
                            messages=[], system="s")
    raised = None
except mc.TruncatedResponse as exc:
    raised = exc
check("still truncated after the retry -> raises loudly", raised is not None)
check("...and does NOT loop (2 calls total, never more)", len(c.calls) == 2, f"-> {c.calls}")

c = _Client(succeeds_at=None)
try:
    mc.create_with_headroom(c, call="t", model="m", max_tokens=mc.MAX_TOKENS_CEILING,
                            messages=[], system="s")
except mc.TruncatedResponse:
    pass
check("at the ceiling it raises without a pointless retry", len(c.calls) == 1, f"-> {c.calls}")

print("\nwired into the real call sites:\n")

for mod, name, const, env in (
    (sw, "stage2_writer", "WRITE_MAX_TOKENS", "STAGE2_WRITE_MAX_TOKENS"),
    (sw, "stage2_writer", "CLASSIFY_MAX_TOKENS", "STAGE2_CLASSIFY_MAX_TOKENS"),
):
    check(f"{name}.{const} exists and is env-overridable ({env})",
          isinstance(getattr(mod, const, None), int) and env in open(mod.__file__, encoding="utf-8").read())

cc = importlib.import_module("consolidation.check")
orch = importlib.import_module("ingestion.orchestrator")
check("consolidation caps are env-overridable",
      isinstance(cc.PAIR_MAX_TOKENS, int) and isinstance(cc.BATCH_MAX_TOKENS, int))
check("grouper cap is env-overridable", isinstance(orch.CLUSTER_GROUPER_MAX_TOKENS, int))

src = (open(sw.__file__, encoding="utf-8").read()
       + open(cc.__file__, encoding="utf-8").read()
       + open(orch.__file__, encoding="utf-8").read())
check("no JSON-parsing model call bypasses the guard any more",
      "client.messages.create(" not in src, f"-> unguarded call still present")

# End to end: _write_draft recovers and records the retry on the draft.
DRAFT = '{"title": "Yishun t", "summary": "s", "slug": "s", "seo_title": "s", "seo_description": "s"}'
c = _Client(succeeds_at=sw.WRITE_MAX_TOKENS * 2, text=DRAFT)
d = sw._write_draft(c, {"title": "t", "content": "c", "date": "2026-07-01"},
                    {"classification": "clown", "severity": 1})
check("_write_draft recovers from a truncation instead of failing the candidate",
      d.get("title") == "Yishun t")
check("...and records the retry on the draft for visibility",
      d.get("_write_truncation_retry", {}).get("cap") == sw.WRITE_MAX_TOKENS,
      f"-> {d.get('_write_truncation_retry')}")

c = _Client(succeeds_at=1, text=DRAFT)
d = sw._write_draft(c, {"title": "t", "content": "c", "date": "2026-07-01"},
                    {"classification": "clown", "severity": 1})
check("a normal draft carries no retry marker", "_write_truncation_retry" not in d)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
