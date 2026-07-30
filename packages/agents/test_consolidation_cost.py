"""
Consolidation cost + correctness controls, after the switch from a per-candidate
fan-out of pairwise Haiku calls to ONE batched judgement.

Run: .venv/Scripts/python.exe test_consolidation_cost.py

These assert the number of Haiku calls, not just the decision — call volume is
the point of the change. Each test counts _judge_batch invocations.

What replaced what:
  before — up to MAX_JUDGEMENTS_PER_CANDIDATE pairwise calls, overlap-ranked,
           with an early exit; the long tail past the cap was silently unjudged.
  after  — exactly one call carrying every keyword-eligible record. The ranking
           survives (likeliest match first in the prompt); the cap and the early
           exit are gone because there is no call count left to bound.
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


# A candidate that shares the distinctive tokens with its true match, and one
# common token ("block") with a long tail of unrelated incidents.
CAND = {"title": "Durian seller stabbed at Yishun block 123 wet market",
        "summary": "A durian seller was stabbed in a dispute at the Yishun block 123 wet market.",
        "url": "https://example.com/a", "incident_date": "2026-07-01"}

TRUE_MATCH = {"id": "p-true",
              "title": "Durian seller stabbed at Yishun wet market",
              "summary": "Durian seller stabbed in a dispute at a Yishun block 123 market.",
              "incident_date": "2026-07-01"}

QUEUED_MATCH = {"id": "q-true",
                "title": "Durian seller stabbed at Yishun wet market",
                "summary": "Durian seller stabbed in a dispute at a Yishun block 123 market.",
                "incident_date": "2026-07-01"}

UNRELATED = {"id": "p-zebra", "title": "Zebra parade delights crowd",
             "summary": "A parade brought cheer downtown.", "incident_date": "2020-01-01"}


def _noise(n):
    """n unrelated incidents that share exactly one common token ('block')."""
    return [{"id": f"p-{i}", "title": f"Lift breakdown at block {i}",
             "summary": f"A lift broke down at block {i} for hours.",
             "incident_date": "2026-05-01"} for i in range(n)]


def _no_match():
    return {"match_index": None, "match_confidence": 0.0, "match_reason": "", "related": []}


def _match_id(records, target_id, conf=0.95, related=None):
    idx = next((i for i, r in enumerate(records) if r["id"] == target_id), None)
    return {"match_index": idx, "match_confidence": conf,
            "match_reason": "same event", "related": related or []}


def _run(published, queued, verdict_fn):
    calls = {"n": 0, "records": None}

    def counting_batch(_client, _cand, records):
        calls["n"] += 1
        calls["records"] = records
        return verdict_fn(records)

    with mock.patch.object(cc, "_fetch_recent_published", return_value=published), \
         mock.patch.object(cc, "_fetch_recent_queue", return_value=queued), \
         mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
         mock.patch.object(cc, "_judge_batch", side_effect=counting_batch):
        res = cc.check(CAND, supabase_client=mock.MagicMock())
    return res, calls


print("consolidation batched-judgement tests:\n")

# ── 1. Call volume: one call, whatever the pool size ────────────────────────
res, c = _run(_noise(80), [], lambda recs: _no_match())
check(f"80 overlapping records cost exactly ONE Haiku call (made {c['n']})", c["n"] == 1)
check("all 80 records reach that one call — the old cap dropped 68 of them",
      len(c["records"]) == 80, f"-> {len(c['records'])}")
check("still returns a decision", res.action == "new")

res, c = _run(_noise(8), _noise(8), lambda recs: _no_match())
check("a mixed published+queue pool is still ONE call", c["n"] == 1 and len(c["records"]) == 16,
      f"-> {c['n']} call(s), {len(c['records'])} record(s)")

# ── 2. No plausible record -> no call at all ───────────────────────────────
res, c = _run([UNRELATED], [], lambda recs: _no_match())
check("zero keyword overlap -> zero Haiku calls", c["n"] == 0, f"-> {c['n']} calls")
check("and the candidate resolves to new", res.action == "new")

# ── 3. Overlap ranking survives: likeliest match first in the prompt ───────
res, c = _run(_noise(40) + [TRUE_MATCH], [], lambda recs: _no_match())
check("overlap ranking puts the true match FIRST in the batched prompt",
      c["records"][0]["id"] == "p-true", f"-> {c['records'][0]['id']}")

# ── 4. Decisions preserved ─────────────────────────────────────────────────
res, c = _run(_noise(40) + [TRUE_MATCH], [], lambda recs: _match_id(recs, "p-true"))
check("confident published match -> UPDATE",
      res.action == "update" and res.matched_incident_id == "p-true",
      f"-> action={res.action} matched={res.matched_incident_id}")

res, c = _run([], [QUEUED_MATCH], lambda recs: _match_id(recs, "q-true"))
check("confident queue match -> SKIP (no second pending row)",
      res.action == "skip" and res.matched_incident_id is None,
      f"-> action={res.action}")

res, c = _run([TRUE_MATCH], [], lambda recs: _match_id(recs, "p-true", conf=0.5))
check("weak published match -> new, surfaced as a related link",
      res.action == "new" and len(res.related_incidents) == 1,
      f"-> action={res.action} links={len(res.related_incidents)}")

res, c = _run([], [QUEUED_MATCH], lambda recs: _match_id(recs, "q-true", conf=0.5))
check("weak QUEUE match -> new, and no link (incident_links needs two published rows)",
      res.action == "new" and res.related_incidents == [])

# ── 5. Related links ───────────────────────────────────────────────────────
_REL = [{"index": 0, "confidence": 0.8, "reason": "same block", "link_type": "same_location"}]

res, c = _run([TRUE_MATCH], [], lambda recs: {**_no_match(), "related": _REL})
check("related link recorded for a published record",
      len(res.related_incidents) == 1 and res.related_incidents[0].link_type == "same_location",
      f"-> {res.related_incidents}")

res, c = _run([], [QUEUED_MATCH], lambda recs: {**_no_match(), "related": _REL})
check("related link against a QUEUE record is ignored", res.related_incidents == [])

res, c = _run([TRUE_MATCH], [], lambda recs: _match_id(recs, "p-true", related=_REL))
check("the matched record is not ALSO emitted as a related link",
      res.action == "update" and res.related_incidents == [],
      f"-> {res.related_incidents}")

res, c = _run([TRUE_MATCH], [],
              lambda recs: {**_no_match(),
                            "related": [{"index": 0, "confidence": 0.1, "reason": "x",
                                         "link_type": "related"}]})
check("a below-threshold related confidence is dropped", res.related_incidents == [])

# ── 6. Malformed verdicts never become a silent merge ──────────────────────
def _boom(recs):
    raise RuntimeError("model down")


res, c = _run([TRUE_MATCH], [], _boom)
check("batch judge failure -> new (fail-open), never raises", res.action == "new")

res, c = _run([TRUE_MATCH], [],
              lambda recs: {"match_index": 99, "match_confidence": 0.99,
                            "match_reason": "x", "related": []})
check("out-of-range match_index -> treated as no match", res.action == "new")

res, c = _run([TRUE_MATCH], [],
              lambda recs: {"match_index": True, "match_confidence": 0.99,
                            "match_reason": "x", "related": []})
check("a bool match_index is not index 1 -> no match", res.action == "new")

res, c = _run([TRUE_MATCH], [],
              lambda recs: {"match_index": 0, "match_confidence": "very high",
                            "match_reason": "x", "related": []})
check("unparseable confidence coerces to 0.0 -> new, never a silent UPDATE",
      res.action == "new", f"-> {res.action}")

res, c = _run([TRUE_MATCH], [],
              lambda recs: {"match_index": 0, "match_confidence": 0.95,
                            "match_reason": "m", "related": "not-a-list"})
check("a non-list 'related' is ignored, match still honoured",
      res.action == "update" and res.related_incidents == [])

res, c = _run([TRUE_MATCH], [], lambda recs: {})
check("an empty verdict object -> new", res.action == "new")

# ── 7. The retired cost knobs are still defined, just unread ───────────────
check("MAX_JUDGEMENTS_PER_CANDIDATE still exists for config compatibility",
      isinstance(rules.MAX_JUDGEMENTS_PER_CANDIDATE, int))
check("EARLY_EXIT_CONFIDENCE still exists for config compatibility",
      isinstance(rules.EARLY_EXIT_CONFIDENCE, float))
check("check.py no longer reads either of them",
      not hasattr(cc, "MAX_JUDGEMENTS_PER_CANDIDATE") and not hasattr(cc, "EARLY_EXIT_CONFIDENCE"))

# Lowering the old cap must NOT change the call count any more.
with mock.patch.object(rules, "MAX_JUDGEMENTS_PER_CANDIDATE", 3):
    res, c = _run(_noise(50), [], lambda recs: _no_match())
check("lowering the retired cap no longer bounds anything (still 1 call, 50 records)",
      c["n"] == 1 and len(c["records"]) == 50, f"-> {c['n']} call(s), {len(c['records'])} record(s)")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
