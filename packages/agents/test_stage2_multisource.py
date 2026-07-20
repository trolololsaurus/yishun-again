"""
Self-contained tests for multi-source Stage 2 input. No pytest, no API.
Run: .venv/Scripts/python.exe test_stage2_multisource.py

Multi-source stories used to reach Stage 2 as a single article: the aggregator
kept every URL and timeline entry but only the primary's TEXT, so a block number
in one report, a charge in another and a quote in a third never reached the
writer. build_candidates now carries source_articles and _build_user_message
renders them.
"""
import importlib

sw = importlib.import_module("filters.stage2_writer")
qr = importlib.import_module("consolidation.queue_row")

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

PRIMARY = "https://st.example/a"
BASE = {
    "source_name": "The Straits Times", "url": PRIMARY, "date": "2026-07-04",
    "title": "Man arrested", "content": "Primary report text.",
}
def art(n, url, content, stype="msm", date="2026-07-04"):
    return {"source_name": n, "source_type": stype, "url": url,
            "date": date, "title": f"{n} headline", "content": content}

print("stage2 multi-source tests:")

# ── no extra sources: message unchanged ─────────────────────────────────────
msg1 = sw._build_user_message(dict(BASE))
check("single source: no ADDITIONAL block", "ADDITIONAL REPORTS" not in msg1)
check("single source: primary content still present", "Primary report text." in msg1)

# ── extra sources rendered ──────────────────────────────────────────────────
multi = {**BASE, "source_articles": [
    art("The Straits Times", PRIMARY, "Primary report text."),          # primary, skipped
    art("Stomp", "https://stomp.example/b", "Witness saw the man flee."),
    art("AsiaOne", "https://asiaone.example/c", "Charged under section 20."),
]}
msg2 = sw._build_user_message(multi)
check("multi: ADDITIONAL block present", "ADDITIONAL REPORTS OF THE SAME INCIDENT (2)" in msg2)
check("multi: second outlet's unique detail included", "Witness saw the man flee." in msg2)
check("multi: third outlet's unique detail included", "Charged under section 20." in msg2)
check("multi: outlet names labelled", "Stomp" in msg2 and "AsiaOne" in msg2)
check("multi: primary not duplicated as an extra", msg2.count("Primary report text.") == 1)

# ── guardrail #2: signal sources never quoted ───────────────────────────────
sig = {**BASE, "source_articles": [
    art("HWZ EDMW", "https://forums.hardwarezone.com.sg/t/1", "forum chatter here", stype="signal"),
    art("Stomp", "https://stomp.example/b", "legit msm detail"),
]}
msg3 = sw._build_user_message(sig)
check("signal source content EXCLUDED (guardrail #2)", "forum chatter here" not in msg3)
check("signal excluded but msm sibling kept", "legit msm detail" in msg3)
check("signal-only yields no ADDITIONAL block",
      "ADDITIONAL REPORTS" not in sw._build_user_message({**BASE, "source_articles": [
          art("HWZ EDMW", "https://f/1", "chatter", stype="signal")]}))

# ── caps ────────────────────────────────────────────────────────────────────
many = {**BASE, "source_articles": [art(f"O{i}", f"https://o{i}.example/x", f"detail{i}") for i in range(20)]}
msg4 = sw._build_user_message(many)
check(f"caps extra sources at {sw.MAX_EXTRA_SOURCES}",
      f"ADDITIONAL REPORTS OF THE SAME INCIDENT ({sw.MAX_EXTRA_SOURCES})" in msg4)

long_txt = "x" * 9000
msg5 = sw._build_user_message({**BASE, "source_articles": [art("Long", "https://l/1", long_txt)]})
check("truncates each extra source's text",
      ("x" * sw.EXTRA_SOURCE_CHARS) in msg5 and ("x" * (sw.EXTRA_SOURCE_CHARS + 1)) not in msg5)

# ── empty-content sources skipped ───────────────────────────────────────────
check("extra source with empty content skipped",
      "ADDITIONAL REPORTS" not in sw._build_user_message(
          {**BASE, "source_articles": [art("Empty", "https://e/1", "   ")]}))

# ── queue row must not persist the article bodies ───────────────────────────
draft = {"title": "t", "summary": "s", "classification": "dagger", "severity": 3,
         "confidence": 0.9, "slug": "x", "pixel_art_prompt": "p",
         "source_urls": [PRIMARY, "https://stomp.example/b"]}
row = qr.build_queue_row(multi, draft)
check("queue row drops source_articles (no ~12KB text bloat)",
      "source_articles" not in row["raw_content"])
check("queue row still records corroboration from source_urls",
      row["corroboration_count"] == 2)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
