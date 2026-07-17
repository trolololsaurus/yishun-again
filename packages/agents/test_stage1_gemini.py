"""
Self-contained tests for the Stage 1 Groq->Gemini migration. No pytest.
Run: .venv/Scripts/python.exe test_stage1_gemini.py
Mocks the Gemini client so filter_content runs offline (no GEMINI_API_KEY needed).

Guards the migration's two silent-failure modes:
  1. usage mapping — Gemini reports usage_metadata.prompt_token_count /
     candidates_token_count, but orchestrator.py + backfill_agent.py feed
     result["usage"]["prompt_tokens"/"completion_tokens"] straight into
     quota.record(). Get this wrong and the budget records 0 forever and never
     halts — no crash, just a quota that silently does nothing.
  2. RPD-vs-RPM 429 — only RPM clears with backoff. Misclassify an RPD 429 and
     the pass hot-loops into a wall until midnight US/Pacific.
"""
import json
from unittest import mock
import importlib

s1 = importlib.import_module("filters.stage1_filter")
q = importlib.import_module("filters.stage1_quota")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def _fake_response(payload: dict, prompt_tokens=123, completion_tokens=45):
    r = mock.MagicMock()
    r.text = json.dumps(payload)
    r.usage_metadata = mock.MagicMock(
        prompt_token_count=prompt_tokens,
        candidates_token_count=completion_tokens,
    )
    return r


def _fake_client(response):
    c = mock.MagicMock()
    c.models.generate_content.return_value = response
    return c


def _client_error(code: int, message: str):
    """Build a genai ClientError without invoking its real HTTP-response parsing."""
    exc = s1.genai_errors.ClientError.__new__(s1.genai_errors.ClientError)
    Exception.__init__(exc, message)
    exc.code = code
    exc.message = message
    return exc


ITEM = {"title": "Man arrested after knife attack at Yishun void deck",
        "content": "A 34-year-old man was arrested.", "url": "https://x/y",
        "source_name": "CNA"}
RELEVANT = {"is_relevant": True, "confidence": 0.95, "reason": "specific incident"}

print("stage1 gemini migration tests:")

# ── contract: the exact dict shape downstream consumes ──────────────────────
with mock.patch.object(s1, "_get_client", return_value=_fake_client(_fake_response(RELEVANT))):
    r = s1.filter_content(dict(ITEM))
check("contract: all keys present", set(r) >= {
    "is_relevant", "confidence", "reason", "passes", "override",
    "usage", "rate_limiter_sleep_seconds"})
check("contract: usage maps prompt_token_count -> prompt_tokens",
      r["usage"]["prompt_tokens"] == 123)
check("contract: usage maps candidates_token_count -> completion_tokens",
      r["usage"]["completion_tokens"] == 45)
check("passes=True when relevant and confidence >= threshold", r["passes"] is True)

# ── threshold logic unchanged (PASS_THRESHOLD = 0.4) ────────────────────────
with mock.patch.object(s1, "_get_client", return_value=_fake_client(
        _fake_response({"is_relevant": True, "confidence": 0.39, "reason": "weak"}))):
    r = s1.filter_content(dict(ITEM))
check("confidence 0.39 -> rejected (below 0.4 threshold)", r["passes"] is False)

with mock.patch.object(s1, "_get_client", return_value=_fake_client(
        _fake_response({"is_relevant": False, "confidence": 0.99, "reason": "noise"}))):
    r = s1.filter_content({**ITEM, "title": "4-room HDB flat for sale", "content": "listing"})
check("is_relevant=False -> rejected even at high confidence", r["passes"] is False)

# ── override rule preserved (incident-signal keyword) ───────────────────────
with mock.patch.object(s1, "_get_client", return_value=_fake_client(_fake_response(RELEVANT))):
    r = s1.filter_content(dict(ITEM))
check("override fires on incident-signal keyword ('arrested')", r["override"] is True)
with mock.patch.object(s1, "_get_client", return_value=_fake_client(_fake_response(RELEVANT))):
    r = s1.filter_content({**ITEM, "title": "Yishun laksa review", "content": "tasty"})
check("override does not fire on benign content", r["override"] is False)

# ── empty response degrades exactly as the Groq path did (ValueError) ───────
empty = _fake_response(RELEVANT)
empty.text = None
try:
    with mock.patch.object(s1, "_get_client", return_value=_fake_client(empty)):
        s1.filter_content(dict(ITEM))
    check("empty response -> ValueError (unchanged failure mode)", False)
except ValueError:
    check("empty response -> ValueError (unchanged failure mode)", True)

# ── 429 classification: RPD must never be retried ───────────────────────────
check("RPD 429 detected from quota message",
      s1._is_rpd_429(_client_error(429, "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel")))
check("RPM 429 not misread as RPD",
      not s1._is_rpd_429(_client_error(429, "Quota exceeded: GenerateRequestsPerMinutePerProject")))

rpd_client = mock.MagicMock()
rpd_client.models.generate_content.side_effect = _client_error(
    429, "Resource exhausted: requests per day limit")
try:
    with mock.patch.object(s1, "_get_client", return_value=rpd_client):
        s1.filter_content(dict(ITEM))
    check("RPD 429 raises RpdExhaustedError (no retry)", False)
except q.RpdExhaustedError:
    check("RPD 429 raises RpdExhaustedError (no retry)", True)
check("RPD 429 attempted exactly once (did not hot-loop)",
      rpd_client.models.generate_content.call_count == 1)

# ── daily quota halts on request count, not tokens ──────────────────────────
quota = q.Stage1DailyQuota()
for _ in range(q.RPD_HARD_LIMIT - 1):
    quota.record(1, 1)
check("quota not halted just below RPD limit", quota.should_halt() is False)
quota.record(1, 1)
check("quota halts at RPD limit (request-based, not token-based)", quota.should_halt() is True)

quota2 = q.Stage1DailyQuota()
quota2.record(999_999, 999_999)   # huge tokens, 1 request
check("huge token count does NOT halt (tokens are observability only)",
      quota2.should_halt() is False)

quota3 = q.Stage1DailyQuota()
quota3.mark_rpd_exhausted()
check("mark_rpd_exhausted halts regardless of local count", quota3.should_halt() is True)

# ── RPM throttle admits up to the limit without sleeping ────────────────────
t = q.Stage1RpmThrottle(rpm=3)
slept = 0.0
for _ in range(3):
    slept += t.wait_if_needed()
    t.record()
check("RPM throttle admits up to limit with no sleep", slept == 0.0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
