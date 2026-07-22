"""
Consolidation cost controls — the ranking, the per-candidate cap, and the
early-exit (QA follow-up: consolidation was the pipeline's dominant cost).

Run: .venv/Scripts/python.exe test_consolidation_cost.py

These assert the number of Haiku calls, not just the decision — the whole point
of the change is call volume. Each test counts _judge_pair invocations.
"""
import importlib
from unittest import mock

cc = importlib.import_module("consolidation.check")
rules = importlib.import_module("consolidation.rules")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


def _judge(same, conf, related=False, rel_conf=0.0):
    return {"same_incident": same, "same_incident_confidence": conf,
            "same_incident_reason": "t", "related": related,
            "related_confidence": rel_conf, "related_reason": "t", "link_type": "related"}


# A candidate that shares the distinctive tokens with its true match, and one
# common token ("block") with a long tail of unrelated incidents.
CAND = {"title": "Durian seller stabbed at Yishun block 123 wet market",
        "summary": "A durian seller was stabbed in a dispute at the Yishun block 123 wet market.",
        "url": "https://example.com/a", "incident_date": "2026-07-01"}

TRUE_MATCH = {"id": "p-true",
              "title": "Durian seller stabbed at Yishun wet market",
              "summary": "Durian seller stabbed in a dispute at a Yishun block 123 market.",
              "incident_date": "2026-07-01"}


def _noise(n):
    """n unrelated incidents that share exactly one common token ('block')."""
    return [{"id": f"p-{i}", "title": f"Lift breakdown at block {i}",
             "summary": f"A lift broke down at block {i} for hours.",
             "incident_date": "2026-05-01"} for i in range(n)]


def _run(published, queued, judge_fn):
    calls = {"n": 0}

    def counting_judge(_client, _cand, rec):
        calls["n"] += 1
        return judge_fn(rec)

    with mock.patch.object(cc, "_fetch_recent_published", return_value=published), \
         mock.patch.object(cc, "_fetch_recent_queue", return_value=queued), \
         mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
         mock.patch.object(cc, "_judge_pair", side_effect=counting_judge):
        res = cc.check(CAND, supabase_client=mock.MagicMock())
    return res, calls["n"]


print("consolidation cost-control tests:\n")

# ── 1. Per-candidate cap bounds the call count ──────────────────────────────
# 80 noise incidents all share 'block'; without a cap that is 80 calls.
res, n = _run(_noise(80), [], lambda rec: _judge(False, 0.0))
check(f"cap bounds calls to MAX_JUDGEMENTS_PER_CANDIDATE ({rules.MAX_JUDGEMENTS_PER_CANDIDATE}); made {n}",
      n <= rules.MAX_JUDGEMENTS_PER_CANDIDATE, f"-> {n} calls")
check("still returns a decision under the cap", res.action == "new")

# ── 2. Ranking: the true match is judged despite being buried in noise ──────
# Put the true match LAST in the pool; overlap-ranking must still surface it
# within the cap. It returns a confident same_incident, so we expect UPDATE.
def _judge_by_id(rec):
    return _judge(True, 0.95) if rec["id"] == "p-true" else _judge(False, 0.0)

res, n = _run(_noise(40) + [TRUE_MATCH], [], _judge_by_id)
check("high-overlap true match is judged despite 40 noise records first",
      res.action == "update" and res.matched_incident_id == "p-true",
      f"-> action={res.action} matched={res.matched_incident_id}")

# ── 3. Early exit: a confident match stops the loop ─────────────────────────
# The true match ranks first (highest overlap). Once judged at >=0.9 we must
# stop — so far fewer than the cap of calls, even with plenty of noise behind it.
res, n = _run([TRUE_MATCH] + _noise(40), [], _judge_by_id)
check(f"early exit after a >=0.9 match: only {n} call(s), well under the cap",
      n < rules.MAX_JUDGEMENTS_PER_CANDIDATE, f"-> {n} calls")
check("early-exit decision is UPDATE to the true match",
      res.action == "update" and res.matched_incident_id == "p-true")

# ── 4. A weak match does NOT early-exit (keeps looking for a better one) ─────
# All records return same_incident at 0.5 (< EARLY_EXIT and < UPDATE threshold).
# The loop must run the full capped set, not stop on the first weak hit.
res, n = _run(_noise(30), [], lambda rec: _judge(True, 0.5))
check(f"weak matches do not trigger early exit: judged the full cap ({n})",
      n == rules.MAX_JUDGEMENTS_PER_CANDIDATE, f"-> {n} calls")
check("a below-threshold match still resolves to new", res.action == "new")

# ── 5. Small pool: everything is judged, nothing dropped ────────────────────
res, n = _run([TRUE_MATCH], [], lambda rec: _judge(False, 0.2))
check("small pool judges every record (no cap effect)", n == 1, f"-> {n} calls")

# ── 6. Cap is configurable via env-backed constant ──────────────────────────
with mock.patch.object(rules, "MAX_JUDGEMENTS_PER_CANDIDATE", 3), \
     mock.patch.object(cc, "MAX_JUDGEMENTS_PER_CANDIDATE", 3):
    res, n = _run(_noise(50), [], lambda rec: _judge(False, 0.0))
check(f"lowering the cap to 3 bounds calls to 3 (made {n})", n == 3, f"-> {n} calls")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
