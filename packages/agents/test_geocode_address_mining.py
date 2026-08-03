"""
Self-contained tests for address mining in the geocoder (no pytest, no network).
Run: .venv/Scripts/python.exe test_geocode_address_mining.py

Only build_geocode_queries() is exercised — it is pure string work and issues no
HTTP. The OneMap lookup itself is not tested here.

WHY THIS FILE EXISTS
--------------------
An audit on 2026-08-03 found 71 of 163 published incidents with no map pin, and
a dry run showed 68 of those built NO geocode query at all. The cause was not
OneMap: build_geocode_queries only ever looked for a block or street in the
`block_number` and `area_name` COLUMNS. Stage 2 leaves those null often enough,
and when it does the address is usually sitting in the headline:

    block_number = NULL
    area_name    = 'Yishun'
    title        = 'NSF dies after being pinned down at Block 279 Yishun
                    Street 22 by childhood friend and stepfather'

Zero queries, no pin, for a story that names its own address twice.

The rule this file pins has two halves, and they are NOT the same rule:
  - an ADDRESS ("Block 279 Yishun Street 22") may be mined from prose;
  - a POI NAME ("Khoo Teck Puat Hospital") may NOT be mined from the summary,
    because every dagger story ends with the victim being taken to KTPH and
    mining it would pin them all at the hospital.
Collapsing those two into one rule in either direction is the regression this
file exists to stop.
"""
from classifiers.geocoding import build_geocode_queries, _find_block_in_text

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def methods(queries):
    return [m for m, _ in queries]


def texts(queries):
    return [q for _, q in queries]


print("geocode address mining tests:")

# -- The live regression: address in the title, columns empty -----------------
SHAWN_TITLE = ("NSF dies after being pinned down at Block 279 Yishun Street 22 "
               "by childhood friend and stepfather")
q = build_geocode_queries(None, "Yishun", SHAWN_TITLE)
check("block+street mined from title produces queries", len(q) > 0)
check("title mining yields a block-level query", "block" in methods(q))
check("block number is read out of the title", any("279" in t for t in texts(q)))
check("street is read out of the title", any("YISHUN ST 22" in t for t in texts(q)))

# -- The columns are authoritative; prose is only a fallback ------------------
q = build_geocode_queries("349", "Yishun Avenue 11", "Fire at Block 999 Yishun Street 81")
check("block column wins over a block mentioned in the title",
      any("349" in t for t in texts(q)) and not any("999" in t for t in texts(q)))

# -- Prose that is not an address must not become one -------------------------
check("'a block of flats' is not a block number",
      _find_block_in_text("Fire guts a block of flats in Yishun") is None)
check("'Yishun HDB block (near Chong Pang City)' is not a block number",
      _find_block_in_text("Yishun HDB block (near Chong Pang City)") is None)
check("bare 'block' with no digits is not a block number",
      _find_block_in_text("The whole block was evacuated") is None)
check("'Blk 342B' is mined with its letter suffix",
      _find_block_in_text("Stabbing at Blk 342B") == "342B")

# 'YISHUN ST' must not match inside 'Yishun stabbing' -- the trailing word
# boundary is what prevents it, and titles are now scanned, so it matters.
q = build_geocode_queries(None, "Yishun", "Yishun stabbing leaves one man injured")
check("'Yishun stabbing' does not parse as a street", q == [])

# -- The POI guard: title yes, summary no ------------------------------------
q = build_geocode_queries(None, "Yishun", "Fight breaks out at Northpoint City")
check("POI is still mined from the title", "poi" in methods(q))

KTPH_SUMMARY = ("A man was found motionless at the foot of an HDB block. He was "
                "conveyed to Khoo Teck Puat Hospital and pronounced dead.")
q = build_geocode_queries(None, "Yishun", "Man found motionless at foot of Yishun HDB block",
                          KTPH_SUMMARY)
check("POI is NOT mined from the summary (the KTPH trap)", "poi" not in methods(q))
check("a summary with no address yields no query at all", q == [])

# -- Address mining from the summary when the title has none -----------------
q = build_geocode_queries(
    None, "Yishun", "Man dies after assault at lift landing",
    "The victim was attacked at Block 279, Yishun Street 22 on 9 July 2016.")
check("address is mined from the summary as a fallback", "block" in methods(q))
check("summary-mined block reaches the query", any("279" in t for t in texts(q)))

# -- Nothing usable stays nothing usable -------------------------------------
check("bare 'Yishun' with no address yields no pin",
      build_geocode_queries(None, "Yishun", "Cat rescued in Yishun") == [])
check("all-empty input yields no pin", build_geocode_queries(None, None, None, None) == [])
check("None title/summary do not raise", build_geocode_queries(None, "Yishun") == [])

# -- Existing column behaviour is unchanged ----------------------------------
q = build_geocode_queries("349", "Yishun Avenue 11")
check("column block+street still produces block queries first",
      methods(q)[0] == "block" and "349 YISHUN AVE 11" in texts(q))
q = build_geocode_queries(None, "Yishun Ring Road")
check("street column alone still produces a street query", methods(q) == ["street"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
