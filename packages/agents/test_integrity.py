"""
Self-contained tests for the integrity agent (req #10). No pytest, no DB, no network.
Run: .venv/Scripts/python.exe test_integrity.py

Covers the two detectors as PURE predicates over fixture rows:
  1. double entries from the same source (exact URL + identical headline)
  2. hallucination signals (empty/dead sources, impossible dates, count drift,
     unknown domains, bad enums, slug date contradicting incident_date)

And the safety property the whole module is built around: nothing that touches a
PUBLISHED incident's text, dates or source_urls is ever marked auto-fixable, and
an unreachable URL is UNKNOWN rather than evidence of fabrication.
"""
from datetime import date
from unittest import mock
import importlib

ig = importlib.import_module("ops.integrity")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


TODAY = date(2026, 7, 20)

DOMAINS = {
    "channelnewsasia.com":        {"type": "msm",    "approved": True,  "name": "CNA"},
    "straitstimes.com":           {"type": "msm",    "approved": True,  "name": "ST"},
    "mothership.sg":              {"type": "msm",    "approved": True,  "name": "Mothership"},
    "forums.hardwarezone.com.sg": {"type": "signal", "approved": True,  "name": "HWZ EDMW"},
}


def q(qid, *, url, title="Yishun cat rescued from tree", summary="A cat was rescued.",
      source_name="CNA", d="2026-07-10", created="2026-07-10T00:00:00Z",
      urls=None, corroboration=1, slug="yishun-cat-rescue-jul-2026",
      classification="clown", severity=2, notification=False, date_fallback=False):
    """A war_room_queue row in the shape the DB actually returns."""
    rc = {"source_name": source_name, "date": d, "source_urls": urls if urls is not None else [url]}
    if notification:
        rc["notification_type"] = "pattern_alert"
    if date_fallback:
        rc["_date_fallback"] = True
    return {"id": qid, "created_at": created, "source_url": url, "source_type": "msm",
            "status": "pending", "processed_at": None, "raw_content": rc,
            "proposed_title": title, "proposed_summary": summary, "proposed_slug": slug,
            "proposed_classification": classification, "proposed_severity": severity,
            "agent_confidence": 0.8, "corroboration_count": corroboration}


def inc(iid, *, urls, title="Yishun cat rescued from tree", summary="A cat was rescued.",
        d="2026-07-10", slug="yishun-cat-rescue-jul-2026", corroboration=None,
        classification="clown", severity=2, published="2026-07-11T00:00:00Z"):
    """A published incidents row."""
    return {"id": iid, "created_at": published, "published_at": published,
            "incident_date": d, "title": title, "summary": summary, "slug": slug,
            "classification": classification, "severity": severity,
            "source_urls": urls,
            "corroboration_count": len(urls) if corroboration is None else corroboration}


def QQ(qid, **kw):
    """queue row -> the normalised record the detectors consume."""
    return ig.queue_record(q(qid, **kw))


def II(iid, **kw):
    """published incident row -> the same normalised record."""
    return ig.incident_record(inc(iid, **kw))


CNA_A = "https://www.channelnewsasia.com/singapore/yishun-cat-a"
CNA_B = "https://www.channelnewsasia.com/singapore/yishun-cat-b"
ST_A  = "https://www.straitstimes.com/singapore/yishun-cat-a"
MS_A  = "https://mothership.sg/2026/07/yishun-cat-a"

print("integrity agent tests:")

# ── URL normalisation: looser than dedup.py on purpose ──────────────────────
print("\n url normalisation:")
check("trailing slash collapses", ig.norm_url(CNA_A) == ig.norm_url(CNA_A + "/"))
check("www. stripped", ig.norm_url("https://www.cna.sg/a") == ig.norm_url("https://cna.sg/a"))
check("scheme normalised", ig.norm_url("http://cna.sg/a") == ig.norm_url("https://cna.sg/a"))
check("fragment ignored", ig.norm_url("https://cna.sg/a#top") == ig.norm_url("https://cna.sg/a"))
check("different paths stay different", ig.norm_url(CNA_A) != ig.norm_url(CNA_B))
check("empty url -> empty key", ig.norm_url("") == "" and ig.norm_url(None) == "")
check("title key ignores case + punctuation",
      ig.norm_title("Cat RESCUED, again!") == ig.norm_title("cat rescued again"))

# ── Detection 1a — exact duplicate source URLs ──────────────────────────────
print("\n detection 1a — duplicate source URLs:")

f = ig.find_duplicate_urls([QQ("q1", url=CNA_A, created="2026-07-10T01:00:00Z"),
                            QQ("q2", url=CNA_A + "/", created="2026-07-10T02:00:00Z")], [])
check("two queue rows, same URL -> one finding", len(f) == 1 and f[0].code == "dupe_url_queued")
check("keeps the OLDEST queue row", f[0].row_id == "q1")
check("dismisses only the newer row", f[0].fix["ids"] == ["q2"] and f[0].fix["keep"] == "q1")
check("queue duplicate IS auto-fixable", f[0].fix["op"] == "dismiss_queue_dupe")

f = ig.find_duplicate_urls([], [II("i1", urls=[CNA_A]), II("i2", urls=[CNA_A, ST_A])])
check("same URL in two PUBLISHED incidents -> anomaly",
      len(f) == 1 and f[0].code == "dupe_url_published" and f[0].level == "anomaly")
check("published duplicate is NEVER auto-fixable (which one is real is judgement)",
      f[0].fix is None and f[0].needs_human)

f = ig.find_duplicate_urls([QQ("q1", url=CNA_A)], [II("i1", urls=[CNA_A])])
check("queued URL already published -> dismiss the queue row",
      len(f) == 1 and f[0].code == "queued_url_already_published"
      and f[0].fix["ids"] == ["q1"])

f = ig.find_duplicate_urls([QQ("q1", url=CNA_A), QQ("q2", url=CNA_B)],
                           [II("i1", urls=[ST_A])])
check("distinct URLs produce no duplicate findings", f == [])

f = ig.find_duplicate_urls([QQ("q1", url=CNA_A, urls=[CNA_A, ST_A]),
                            QQ("q2", url=CNA_B, urls=[CNA_B, ST_A])], [])
check("shared corroborating URL across two queue rows is caught",
      any(x.code == "dupe_url_queued" for x in f))

f = ig.find_duplicate_urls([QQ("q1", url=CNA_A, notification=True),
                            QQ("q2", url=CNA_A, notification=True)], [])
check("sentinel rows are never dismissed as duplicates (they are operator to-dos)", f == [])

# ── Detection 1b — near-duplicate titles from the same source ───────────────
print("\n detection 1b — near-duplicate titles (LLM pre-filter):")

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree in Yishun", created="2026-07-10T01:00:00Z"),
    QQ("q2", url=CNA_B, title="CAT RESCUED FROM TREE IN YISHUN!", created="2026-07-10T02:00:00Z"),
])
check("identical headline, same source -> found with NO llm call",
      len(near["identical"]) == 1 and near["ambiguous"] == [])
check("identical queue pair is auto-dismissable", near["identical"][0].fix is not None)
check("identical pair keeps the oldest", near["identical"][0].fix["ids"] == ["q2"])

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree", source_name="CNA"),
    QQ("q2", url=MS_A, title="Cat rescued from tree", source_name="Mothership"),
])
check("identical headline from DIFFERENT outlets is not a same-source double entry",
      near["identical"] == [] and near["ambiguous"] == [])

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree", d="2026-07-01"),
    QQ("q2", url=CNA_B, title="Cat rescued from tree", d="2026-06-01"),
], window_days=7)
check("identical headline outside the date window is not paired", near["identical"] == [])

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree at Yishun Ring Road",
       summary="Firefighters rescued a tabby stuck in a tree."),
    QQ("q2", url=CNA_B, title="Firefighters free tabby stuck at Yishun Ring Road",
       summary="A tabby was freed from a tree by firefighters."),
])
check("different wording, high overlap -> ambiguous (needs the model)",
      near["identical"] == [] and len(near["ambiguous"]) == 1)

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree", summary="A cat."),
    QQ("q2", url=CNA_B, title="Lorry overturns on Sembawang flyover", summary="A lorry."),
])
check("unrelated stories from one source never reach the model", near["ambiguous"] == [])

near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued", notification=True),
    QQ("q2", url=CNA_B, title="Cat rescued", notification=True),
])
check("sentinel/notification rows are excluded from dedup", near["identical"] == [])

# published-side identical headline must not be auto-dismissed
near = ig.find_near_duplicate_titles([
    QQ("q1", url=CNA_A, title="Cat rescued from tree"),
    II("i1", urls=[CNA_B], title="Cat rescued from tree"),
])
check("queue-vs-published identical headline is reported, never auto-fixed",
      len(near["identical"]) == 1 and near["identical"][0].fix is None
      and near["identical"][0].level == "anomaly")

# pair-generation guard
many = [QQ(f"q{i}", url=f"https://cna.sg/{i}", title=f"Story {i} about a Yishun cat") for i in range(40)]
near = ig.find_near_duplicate_titles(many, max_pairs=10)
check("pair cap stops the quadratic sweep",
      near["pairs_capped"] and near["pairs_considered"] <= 10)

# ── LLM budget: only ambiguous pairs, only up to the cap ────────────────────
print("\n llm budget:")

def _verdict(same, conf):
    return {"same_incident": same, "same_incident_confidence": conf,
            "same_incident_reason": "same cat, same tree",
            "related": False, "related_confidence": 0.0,
            "related_reason": "", "link_type": None}

pairs = [(QQ("q1", url=CNA_A), QQ("q2", url=CNA_B), 4)]

cc = importlib.import_module("consolidation.check")
stats = {"llm_judgements": 0, "errors": 0}
with mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
     mock.patch.object(cc, "_judge_pair", return_value=_verdict(True, 0.9)) as judge:
    out = ig._judge_near_duplicates(pairs, mock.MagicMock(), stats)
check("confirmed near-duplicate becomes a finding",
      len(out) == 1 and out[0].code == "dupe_title_confirmed")
check("LLM-confirmed duplicate is NEVER auto-fixed", out[0].fix is None)
check("one pair -> exactly one model call", judge.call_count == 1 and stats["llm_judgements"] == 1)

stats = {"llm_judgements": 0, "errors": 0}
with mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
     mock.patch.object(cc, "_judge_pair", return_value=_verdict(True, 0.5)) as judge:
    out = ig._judge_near_duplicates(pairs, mock.MagicMock(), stats)
check("below the consolidation threshold -> no finding", out == [])

stats = {"llm_judgements": 0, "errors": 0}
big = [(QQ(f"a{i}", url=f"https://cna.sg/a{i}"), QQ(f"b{i}", url=f"https://cna.sg/b{i}"), 3)
       for i in range(ig.MAX_LLM_JUDGEMENTS + 25)]
with mock.patch.object(cc, "_get_anthropic_client", return_value=mock.MagicMock()), \
     mock.patch.object(cc, "_judge_pair", return_value=_verdict(False, 0.0)) as judge:
    ig._judge_near_duplicates(big, mock.MagicMock(), stats)
check("per-run judgement cap is hard",
      judge.call_count == ig.MAX_LLM_JUDGEMENTS and stats["llm_skipped"] == 25)

stats = {"llm_judgements": 0, "errors": 0}
with mock.patch.object(cc, "_get_anthropic_client", side_effect=EnvironmentError("no key")):
    out = ig._judge_near_duplicates(pairs, mock.MagicMock(), stats)
check("no Anthropic key -> pass continues, nothing judged", out == [])

check("zero pairs -> zero calls", ig._judge_near_duplicates([], mock.MagicMock(), stats) == [])

# ── Detection 2 — hallucination signals ─────────────────────────────────────
print("\n detection 2 — hallucination signals:")

def codes(rec, domains=DOMAINS):
    return {f.code for f in ig.check_row_integrity(rec, TODAY, domains)}

def finding(rec, code, domains=DOMAINS):
    return next((f for f in ig.check_row_integrity(rec, TODAY, domains) if f.code == code), None)

clean = II("i1", urls=[CNA_A])
check("a clean published incident produces no findings", ig.check_row_integrity(clean, TODAY, DOMAINS) == [])

check("empty source_urls flagged (guardrail #1)",
      "no_source_urls" in codes(II("i1", urls=[], corroboration=1)))
check("repeated URL inside source_urls flagged",
      "repeated_source_url" in codes(II("i1", urls=[CNA_A, CNA_A + "/"], corroboration=2)))
check("repeated URL inside a QUEUE row's raw_content.source_urls flagged too",
      "repeated_source_url" in codes(QQ("q1", url=CNA_A, urls=[CNA_A, CNA_A + "/"])))

check("future incident_date flagged",
      "future_incident_date" in codes(II("i1", urls=[CNA_A], d="2026-09-01")))
check("today's date is NOT future",
      "future_incident_date" not in codes(II("i1", urls=[CNA_A], d="2026-07-20",
                                                 slug="yishun-cat-rescue-jul-2026")))
check("pre-1980 date flagged",
      "ancient_incident_date" in codes(II("i1", urls=[CNA_A], d="1969-07-20",
                                              slug="yishun-cat-rescue-jul-1969")))
check("1985 is plausible history, not flagged",
      "ancient_incident_date" not in codes(II("i1", urls=[CNA_A], d="1985-07-20",
                                                  slug="yishun-cat-rescue-jul-1985")))
check("missing date flagged when unmarked",
      "missing_incident_date" in codes(QQ("q1", url=CNA_A, d="", slug="yishun-cat-rescue")))
check("missing date NOT flagged when _date_fallback marks it for the operator (QA H3)",
      "missing_incident_date" not in codes(QQ("q1", url=CNA_A, d="", slug="yishun-cat-rescue",
                                                date_fallback=True)))

# QA H8 — the drift that under-counted the lightning meter
f = finding(II("i1", urls=[CNA_A, ST_A], corroboration=1), "corroboration_drift")
check("corroboration_count drift detected", f is not None)
check("drift recomputed from distinct source_urls", f.fix["to"] == 2 and f.fix["from"] == 1)
check("corroboration fix targets the right row",
      f.fix["op"] == "corroboration_count" and f.fix["table"] == "incidents" and f.fix["id"] == "i1")
check("matching count is not flagged",
      "corroboration_drift" not in codes(II("i1", urls=[CNA_A, ST_A], corroboration=2)))
check("a repeated URL does not inflate the expected count",
      finding(II("i1", urls=[CNA_A, CNA_A], corroboration=2), "corroboration_drift").fix["to"] == 1)

check("severity out of range flagged",
      "bad_severity" in codes(QQ("q1", url=CNA_A, severity=9)))
check("severity 0 flagged", "bad_severity" in codes(QQ("q1", url=CNA_A, severity=0)))
check("severity 1..5 accepted",
      all("bad_severity" not in codes(QQ("q1", url=CNA_A, severity=s)) for s in (1, 3, 5)))
check("unknown classification flagged",
      "bad_classification" in codes(QQ("q1", url=CNA_A, classification="chaos")))
check("all four real classifications accepted",
      all("bad_classification" not in codes(QQ("q1", url=CNA_A, classification=c))
          for c in ("heart", "clown", "dagger", "custom")))

check("unapproved domain flagged",
      "unknown_source_domain" in codes(II("i1", urls=["https://8days.sg/x"])))
check("guardrail #2: a signal URL cited as a source is an anomaly",
      "signal_url_cited" in codes(II("i1", urls=["https://forums.hardwarezone.com.sg/t/1"])))
check("signal citation is reported, never auto-stripped (could break guardrail #1)",
      finding(II("i1", urls=["https://forums.hardwarezone.com.sg/t/1"]), "signal_url_cited").fix is None)
check("no domain map -> domain checks skipped, not mass-flagged",
      codes(II("i1", urls=["https://8days.sg/x"]), domains={}) == set())

# the real -jul-2020 shipping bug
print("\n slug date contradiction:")
check("wrong year in slug detected",
      ig.slug_date_conflict("yishun-woman-fall-from-height-blk257-jul-2020", "2026-07-11") == "jul-2026")
check("wrong month in slug detected",
      ig.slug_date_conflict("yishun-cat-rescue-jun-2026", "2026-07-11") == "jul-2026")
check("correct slug is clean", ig.slug_date_conflict("yishun-cat-rescue-jul-2026", "2026-07-11") is None)
check("bare wrong year detected", ig.slug_date_conflict("yishun-cat-rescue-2020", "2026-07-11") == "jul-2026")
check("slug with no date suffix is not a contradiction",
      ig.slug_date_conflict("yishun-cat-rescue", "2026-07-11") is None)
check("no incident_date -> nothing to contradict",
      ig.slug_date_conflict("yishun-cat-rescue-jul-2020", None) is None)
check("slug conflict surfaces as a finding",
      "slug_date_conflict" in codes(II("i1", urls=[CNA_A], d="2026-07-11",
                                           slug="yishun-cat-rescue-jul-2020")))

# ── URL liveness: unreachable must never mean fabricated ────────────────────
print("\n url liveness:")

class _Resp:
    def __init__(self, code): self.status_code = code

def _with_head(fn):
    httpx = importlib.import_module("httpx")
    return mock.patch.object(httpx, "head", side_effect=fn)

with _with_head(lambda *a, **k: _Resp(404)):
    check("404 -> dead", ig.url_status(CNA_A)[0] == "dead")
with _with_head(lambda *a, **k: _Resp(410)):
    check("410 -> dead", ig.url_status(CNA_A)[0] == "dead")
with _with_head(lambda *a, **k: _Resp(200)):
    check("200 -> ok", ig.url_status(CNA_A)[0] == "ok")
with _with_head(lambda *a, **k: _Resp(403)):
    check("403 (Cloudflare bot block) -> UNKNOWN, not dead", ig.url_status(CNA_A)[0] == "unknown")
with _with_head(lambda *a, **k: _Resp(429)):
    check("429 (rate limited) -> UNKNOWN", ig.url_status(CNA_A)[0] == "unknown")
with _with_head(lambda *a, **k: _Resp(405)):
    check("405 (HEAD unsupported) -> UNKNOWN", ig.url_status(CNA_A)[0] == "unknown")
with _with_head(lambda *a, **k: _Resp(500)):
    check("500 -> UNKNOWN", ig.url_status(CNA_A)[0] == "unknown")
with _with_head(lambda *a, **k: (_ for _ in ()).throw(OSError("connection reset"))):
    check("network failure -> UNKNOWN (never 'fabricated')", ig.url_status(CNA_A)[0] == "unknown")
check("empty url -> unknown", ig.url_status("")[0] == "unknown")

recs = [II(f"i{i}", urls=[f"https://www.channelnewsasia.com/a{i}"]) for i in range(10)]
stats = {"urls_checked": 0, "urls_dead": 0, "urls_unknown": 0}
with _with_head(lambda *a, **k: _Resp(404)):
    out = ig.check_url_liveness(recs, mock.MagicMock(), stats, cap=3)
check("liveness cap respected", stats["urls_checked"] == 3 and len(out) == 3)
check("cap is reported in stats", stats["url_check_cap"] == 3 and stats["urls_seen"] == 10)
check("dead published URL is an anomaly, never auto-fixed",
      out[0].level == "anomaly" and out[0].fix is None)

stats = {"urls_checked": 0, "urls_dead": 0, "urls_unknown": 0}
with _with_head(lambda *a, **k: (_ for _ in ()).throw(OSError("dns"))):
    out = ig.check_url_liveness(recs, mock.MagicMock(), stats, cap=5)
check("unreachable URLs produce ZERO findings", out == [] and stats["urls_unknown"] == 5)

# ── Corrections: what may and may not be written ────────────────────────────
print("\n corrections:")

all_codes = {"dupe_url_published", "dupe_title_confirmed", "dupe_title_published",
             "no_source_urls", "future_incident_date", "ancient_incident_date",
             "missing_incident_date", "repeated_source_url", "bad_severity",
             "bad_classification", "slug_date_conflict", "signal_url_cited",
             "unknown_source_domain", "dead_source_url"}
samples = [
    ig.find_duplicate_urls([], [II("i1", urls=[CNA_A]), II("i2", urls=[CNA_A])]),
    ig.check_row_integrity(II("i1", urls=[], corroboration=1), TODAY, DOMAINS),
    ig.check_row_integrity(II("i1", urls=[CNA_A], d="2027-01-01",
                                  slug="yishun-cat-rescue-jan-2027"), TODAY, DOMAINS),
    ig.check_row_integrity(II("i1", urls=["https://forums.hardwarezone.com.sg/t/1"]), TODAY, DOMAINS),
]
flat = [f for group in samples for f in group]
check("only corroboration_count is auto-fixable on a PUBLISHED incident",
      all(f.fix is None for f in flat if f.code in all_codes))
check("every fix carries one of exactly two whitelisted ops",
      {f.fix["op"] for f in flat if f.fix} <= {"corroboration_count", "dismiss_queue_dupe"})

client = mock.MagicMock()
run_ = mock.MagicMock()
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
fix_finding = finding(II("i1", urls=[CNA_A, ST_A], corroboration=1), "corroboration_drift")
ig.apply_fixes([fix_finding], {}, client, run_, stats)
check("corroboration fix writes exactly one update", stats["corrected"] == 1)
check("update targets incidents.corroboration_count",
      client.table.call_args_list[0][0][0] == "incidents"
      and client.table.return_value.update.call_args[0][0] == {"corroboration_count": 2})

client = mock.MagicMock()
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
queue_rows = {"q2": q("q2", url=CNA_A)}
dupe = ig.find_duplicate_urls([QQ("q1", url=CNA_A, created="2026-07-10T01:00:00Z"),
                               QQ("q2", url=CNA_A, created="2026-07-10T02:00:00Z")], [])[0]
ig.apply_fixes([dupe], queue_rows, client, mock.MagicMock(), stats)
tables = [c[0][0] for c in client.table.call_args_list]
check("queue duplicate dismissal touches only queue + training_signals",
      set(tables) == {"war_room_queue", "training_signals"} and stats["dismissed"] == 1)
signal = client.table.return_value.insert.call_args[0][0]
check("training signal is action=reject/decision=reject",
      signal["action"] == "reject" and signal["decision"] == "reject")
check("training signal records reject_reason=duplicate", signal["reject_reason"] == "duplicate")
check("training signal is attributed to the agent, not the operator",
      signal["decided_by"] == "agent")

client = mock.MagicMock()
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
ig.apply_fixes([dupe], {"q2": {**q("q2", url=CNA_A), "processed_at": "2026-07-11T00:00:00Z"}},
               client, mock.MagicMock(), stats)
check("a row a human already processed is left alone", stats["dismissed"] == 0)

# two findings can name the same queue row (it shares two URLs with one incident);
# dismissing twice would double-count that row in the learning loop.
client = mock.MagicMock()
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
ig.apply_fixes([dupe, dupe], queue_rows, client, mock.MagicMock(), stats)
check("the same queue row is never dismissed twice", stats["dismissed"] == 1)
check("and only one training signal is written",
      sum(1 for c in client.table.call_args_list if c[0][0] == "training_signals") == 1)

client = mock.MagicMock()
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
ig.apply_fixes([fix_finding, fix_finding], {}, client, mock.MagicMock(), stats)
check("the same corroboration fix is never written twice", stats["corrected"] == 1)

client = mock.MagicMock()
client.table.side_effect = RuntimeError("supabase down")
stats = {"corrected": 0, "dismissed": 0, "errors": 0}
ig.apply_fixes([fix_finding], {}, client, mock.MagicMock(), stats)
check("a failing write is counted, not raised", stats["errors"] == 1 and stats["corrected"] == 0)

# ── run(): report-only default, and it never raises ─────────────────────────
print("\n run() contract:")

def _client_with(queue_rows, incident_rows):
    """Minimal Supabase double: table().select()...execute() returns .data."""
    def table(name):
        t = mock.MagicMock()
        data = {"war_room_queue": queue_rows, "incidents": incident_rows}.get(name, [])
        t.select.return_value = t
        for m in ("eq", "gte", "is_", "in_", "order", "limit", "update", "insert", "contains"):
            getattr(t, m).return_value = t
        t.execute.return_value = mock.MagicMock(data=data)
        return t
    c = mock.MagicMock()
    c.table.side_effect = table
    return c

QUEUE = [q("q1", url=CNA_A, created="2026-07-10T01:00:00Z"),
         q("q2", url=CNA_A, created="2026-07-10T02:00:00Z")]
PUB = [inc("i1", urls=[CNA_A, ST_A], corroboration=1, slug="a-jul-2026")]

with mock.patch.object(ig, "check_url_liveness", return_value=[]), \
     mock.patch("classifiers.source_allowlist.load_source_domains", return_value=DOMAINS), \
     mock.patch.object(ig, "notify", return_value={"status": "disabled"}) as mailed:
    out = ig.run(supabase_client=_client_with(QUEUE, PUB))
check("report-only by default", out["apply"] is False)
check("finds the duplicate + the drift", out["findings"] >= 2)
check("report-only pass writes NOTHING", out["corrected"] == 0 and out["dismissed"] == 0)
check("no errors on a clean pass", out["errors"] == 0)
check("stats dict always carries an errors count", "errors" in out)
check("emails the operator as kind='anomaly'", mailed.call_args[0][0] == "anomaly")
check("dedup key is the calendar day, so repeat findings do not re-mail",
      mailed.call_args.kwargs["dedup_key"].startswith("integrity:")
      and len(mailed.call_args.kwargs["dedup_key"]) == len("integrity:2026-07-20"))

with mock.patch.object(ig, "check_url_liveness", return_value=[]), \
     mock.patch("classifiers.source_allowlist.load_source_domains", return_value=DOMAINS), \
     mock.patch.object(ig, "notify", return_value={"status": "disabled"}) as quiet:
    ig.run(supabase_client=_client_with([], [inc("i1", urls=[CNA_A])]))
check("a clean archive sends no email at all", quiet.call_count == 0)

with mock.patch.object(ig, "check_url_liveness", return_value=[]), \
     mock.patch("classifiers.source_allowlist.load_source_domains", return_value=DOMAINS), \
     mock.patch.object(ig, "notify", return_value={"status": "disabled"}) as mailed:
    out = ig.run(supabase_client=_client_with(QUEUE, PUB), apply=True)
check("apply=True performs the whitelisted corrections", out["corrected"] >= 1)

# every failure mode must still return a dict
broken = mock.MagicMock()
broken.table.side_effect = RuntimeError("supabase down")
out = ig.run(supabase_client=broken)
check("total DB failure returns a dict, does not raise", isinstance(out, dict) and out["errors"] >= 1)

with mock.patch.object(ig, "find_duplicate_urls", side_effect=RuntimeError("detector bug")), \
     mock.patch("classifiers.source_allowlist.load_source_domains", return_value=DOMAINS):
    out = ig.run(supabase_client=_client_with(QUEUE, PUB))
check("a crashing detector is contained", isinstance(out, dict) and out["errors"] == 1)

out = ig.run(supabase_client=_client_with([], []))
check("empty archive is a clean no-op", out["findings"] == 0 and out["errors"] == 0)

with mock.patch.object(ig, "agent_enabled", return_value=False):
    out = ig.run(supabase_client=_client_with(QUEUE, PUB))
check("AGENT_DISABLED kill switch short-circuits", out.get("disabled") is True and out["findings"] == 0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
