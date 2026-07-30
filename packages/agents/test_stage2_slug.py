"""
Self-contained tests for the Stage 2 slug-date fix. No pytest, no API.
Run: .venv/Scripts/python.exe test_stage2_slug.py

Bug: the model was never given the incident date, so it guessed the slug's
year (2026 incidents shipped at -2020/-2024/-2025 URLs). _stamp_slug_date now
stamps the trailing -month-year from the authoritative content["date"].
"""
import importlib

sw = importlib.import_module("filters.stage2_writer")
stamp = sw._stamp_slug_date

passed = failed = 0
def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1

print("stage2 slug-date tests:")

# The exact production bug: 2026 death shipped at -jul-2020.
check("wrong year corrected",
      stamp("yishun-woman-fall-from-height-death-blk257-jul-2020", "2026-07-11")
      == "yishun-woman-fall-from-height-death-blk257-jul-2026")
check("wrong month+year corrected",
      stamp("white-car-mounts-divider-yishun-st81-jun-2024", "2026-06-19")
      == "white-car-mounts-divider-yishun-st81-jun-2026")

# Model omitted the date entirely -> append the correct one.
check("missing date appended",
      stamp("yishun-hdb-bedroom-fire-block-844", "2026-06-18")
      == "yishun-hdb-bedroom-fire-block-844-jun-2026")

# Model appended only a bare year -> replace with month-year.
check("bare year replaced",
      stamp("yishun-something-2024", "2026-01-05") == "yishun-something-jan-2026")

# Already correct -> unchanged.
check("correct slug unchanged",
      stamp("yishun-wild-chicken-chase-boardwalk-jul-2026", "2026-07-16")
      == "yishun-wild-chicken-chase-boardwalk-jul-2026")

# All 12 months map correctly.
months = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]
check("all months map correctly",
      all(stamp("x-1999", f"2026-{i+1:02d}-15") == f"x-{m}-2026" for i, m in enumerate(months)))

# Dateless: strip the guessed year rather than fabricate one.
check("dateless strips guessed year",
      stamp("yishun-man-found-dead-void-deck-jul-2020", "") == "yishun-man-found-dead-void-deck")
check("dateless (None) strips guessed year",
      stamp("yishun-x-jan-2024", None) == "yishun-x")
check("unparseable date strips guessed year",
      stamp("yishun-x-jan-2024", "not-a-date") == "yishun-x")

# Length: result stays <= 70 and keeps the full date suffix intact.
long_slug = "yishun-" + "a"*90 + "-jan-2020"
out = stamp(long_slug, "2026-07-11")
check("length clamped to <=70", len(out) <= 70)
check("date suffix survives truncation", out.endswith("-jul-2026"))

# Bad month guard (defensive).
check("invalid month falls back to base", stamp("yishun-x-jan-2024", "2026-13-01") == "yishun-x")

# Charset guard: the LLM slug must be forced into ^[a-z0-9-]+$ before it can
# reach incidents.slug — a '/' or unicode slug publishes an unroutable page
# (the frontend's sanitiser strips those chars, so it 404s forever).
check("slash is squashed", stamp("yishun/void-deck", "2026-07-11") == "yishun-void-deck-jul-2026")
check("spaces are squashed", stamp("yishun void deck", "2026-07-11") == "yishun-void-deck-jul-2026")
check("unicode is squashed", stamp("yishun-café-噪音", "2026-07-11") == "yishun-caf-jul-2026")
check("uppercase is lowered", stamp("Yishun-Fire", "2026-07-11") == "yishun-fire-jul-2026")
check("charset result matches ^[a-z0-9-]+$",
      __import__("re").fullmatch(r"[a-z0-9-]+", stamp("Yishun / Café @ Blk 123!", "2026-07-11")) is not None)

# _build_user_message now includes the date line (feeds prose too).
msg = sw._build_user_message({"source_name":"CNA","url":"u","title":"t","content":"c","date":"2026-07-16"})
check("user message includes Date line", "Date: 2026-07-16" in msg)
check("user message dateless -> 'unknown'",
      "Date: unknown" in sw._build_user_message({"title":"t","content":"c"}))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
