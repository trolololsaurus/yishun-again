"""
The candidate date must reach the consolidation judge (regression).

Run: .venv/Scripts/python.exe test_consolidation_date.py

Bug: write_stage2()'s result dict carried no date, and consolidation.check runs
on that draft alone — so _judge_pair always saw the candidate date as 'unknown'
and lost the date-proximity signal on every source. It bit reddit hardest
(casual titles overlap MSM headlines weakly, so the date was the disambiguator),
surfacing as duplicate cards that failed to link to the existing incident.
"""
import importlib
from unittest import mock

cc = importlib.import_module("consolidation.check")
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


# ── 1. _judge_pair puts the real date in the prompt (not 'unknown') ─────────
print("consolidation date-threading:\n")

captured = {}


class _Resp:
    def __init__(self, text): self.content = [type("C", (), {"text": text})()]


def _capture_client(msg_sink):
    client = mock.MagicMock()

    def _create(**kwargs):
        msg_sink["user"] = kwargs["messages"][0]["content"]
        return _Resp('{"same_incident": false, "same_incident_confidence": 0.0, '
                     '"same_incident_reason": "x", "related": false, '
                     '"related_confidence": 0.0, "related_reason": "x", "link_type": null}')
    client.messages.create.side_effect = _create
    return client


existing = {"id": "p-1", "title": "T", "summary": "S", "incident_date": "2026-06-01"}

cap = {}
cc._judge_pair(_capture_client(cap),
               {"title": "Reddit post", "content": "body", "date": "2026-07-16"},
               existing)
check("candidate date appears in the judge prompt",
      "2026-07-16" in cap["user"], f"-> {cap['user'][:120]!r}")
check("date is not silently 'unknown' when present",
      "Date: 2026-07-16" in cap["user"])

# incident_date key (published incidents / queue rows use this) also works
cap = {}
cc._judge_pair(_capture_client(cap),
               {"title": "x", "content": "y", "incident_date": "2025-01-02"}, existing)
check("incident_date key is read too", "2025-01-02" in cap["user"])

# ── 2. a genuinely dateless candidate still reads 'unknown' ─────────────────
cap = {}
cc._judge_pair(_capture_client(cap), {"title": "x", "content": "y", "date": ""}, existing)
check("empty date reads 'unknown', not blank", "Date: unknown" in cap["user"])

cap = {}
cc._judge_pair(_capture_client(cap), {"title": "x", "content": "y"}, existing)
check("missing date reads 'unknown'", "Date: unknown" in cap["user"])

# ── 3. write_stage2 threads the date into its result (the source of the bug) ─
print("\nwrite_stage2 date pass-through:\n")


def _run_write(content_date):
    content = {"title": "t", "content": "c", "url": "u", "source_name": "s",
               "source_urls": ["u"]}
    if content_date is not None:
        content["date"] = content_date
    fake_classify = {"classification": "clown", "severity": 2, "confidence": 0.8,
                     "block_number": None, "area_name": None, "latitude": None,
                     "longitude": None, "tags": [], "deaths": None, "injuries": None,
                     "political": False}
    fake_draft = {"title": "T", "summary": "S", "slug": "t-jul-2026",
                  "seo_title": None, "seo_description": None, "pixel_art_prompt": ""}
    with mock.patch.object(sw, "_get_client", return_value=mock.MagicMock()), \
         mock.patch.object(sw, "_classify", return_value=fake_classify), \
         mock.patch.object(sw, "_write_draft", return_value=fake_draft):
        return sw.write_stage2(content)


res = _run_write("2026-07-16")
check("dated candidate -> draft carries date", res.get("date") == "2026-07-16",
      f"-> {res.get('date')!r}")

res = _run_write("")
check("dateless candidate -> no empty date key overriding item date",
      "date" not in res or not res.get("date"))

res = _run_write(None)
check("no date key on input -> none injected", "date" not in res)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
