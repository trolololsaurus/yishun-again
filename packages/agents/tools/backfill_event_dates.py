"""
Re-date published incidents from what the article SAYS, not when it ran.

WHY THIS EXISTS
---------------
`incidents.incident_date` is contractually the date the event HAPPENED. Until
2026-08-04 nothing ever extracted one: the pipeline carried the candidate's
`published_at` straight through, so an incident was filed on the day it was
REPORTED. Measured against the source articles that morning:

    python worksite   "on July 30 at about 3.57pm"   event Jul 30, filed Aug 3
    high-beam chase   "Last Friday (31 July)"        event Jul 31, filed Aug 3
    pliers assault    "on Sunday (Aug 2)"            event Aug 2,  filed Aug 3

`filters/stage2_writer` now extracts an event date going forward. This tool
repairs the rows written before that.

WHICH ROWS IT TOUCHES — the bug has a signature
------------------------------------------------
It does NOT re-derive every incident. Historical backfill rows had their dates
set deliberately (`seed_backfill._date_from_text` reads the date out of the
article with an LLM), and re-deriving a 1988 MRT opening from a Wikipedia page
is far more likely to corrupt a good date than fix a bad one.

The live-pipeline bug leaves a fingerprint: `incident_date` is EQUAL to one of
the row's own source publication dates, because it literally was that date.
Measured 2026-08-04 over 166 published incidents:

    115  incident_date == a source publication date   <- candidates
     15  differs from every source pub date           <- already a real event date
     36  no dated sources at all                      <- cannot judge, skipped

Only the 115 are considered. `--include-undated` opts the 36 in, but they are
mostly .gov.sg and wiki pages where there is no article text to read.

WHAT IT WILL NOT DO
-------------------
- It never changes the SLUG. A slug is a permanent public URL and its
  `-mon-yyyy` suffix is a naming convention, not data; re-stamping it would 404
  every existing link and every share card already in the wild.
- It never invents a date. If the article states no event date, the row keeps
  its publication date — which is the correct fallback, not a failure.
- It applies the same guards as the live path (`_sanitise_event_date`): a date
  after publication, or more than 5 years before it, is rejected rather than
  written.

USAGE
-----
    ./.venv/Scripts/python.exe tools/backfill_event_dates.py             # DRY RUN
    ./.venv/Scripts/python.exe tools/backfill_event_dates.py --limit 10  # sample
    ./.venv/Scripts/python.exe tools/backfill_event_dates.py --apply     # writes

Dry run is the default and performs the fetches and model calls but no writes,
so the report is exactly what --apply would do.
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifiers.corroboration import get_supabase_client          # noqa: E402
from filters.stage2_writer import _sanitise_event_date, MODEL_CLASSIFY  # noqa: E402
from scrapers import BROWSER_HEADERS, strip_html                   # noqa: E402

_ARTICLE_CAP = 400_000
_TEXT_CAP    = 6_000

_SYSTEM = (
    "You extract the date an incident HAPPENED from a Singapore news article.\n"
    "The article's own publication date is given. The event is usually BEFORE "
    "it and never after.\n"
    'Return JSON only: {"event_date": "YYYY-MM-DD"} or {"event_date": null}.\n'
    "Resolve relative references against the publication date:\n"
    '  "on July 30 at about 3.57pm" -> that July 30\n'
    '  "Last Friday (31 July)"      -> 2026-07-31\n'
    '  "on Sunday (Aug 2)"          -> 2026-08-02\n'
    '  "yesterday", "on Tuesday"    -> resolve against the publication date\n'
    "If the article gives a court/charge date AND an offence date, return the "
    "OFFENCE date — that is the incident.\n"
    "Return null if the article states no event date. Never guess."
)


def _fetch_text(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read(_ARTICLE_CAP).decode("utf-8", errors="ignore")
        return strip_html(html)[:_TEXT_CAP]
    except Exception:
        return ""


def _ask(client, text: str, published: str) -> str | None:
    try:
        resp = client.messages.create(
            model=MODEL_CLASSIFY, max_tokens=128, temperature=0.0,
            system=_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Publication date: {published}\n\nArticle:\n{text}"}],
        )
        raw = resp.content[0].text.strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else raw
        return json.loads(raw).get("event_date")
    except Exception as exc:
        print(f"      model error: {str(exc)[:80]}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--limit", type=int, default=0, help="process at most N incidents")
    ap.add_argument("--include-undated", action="store_true",
                    help="also process rows with no dated sources (rarely useful)")
    args = ap.parse_args()

    import anthropic
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env"))
    ai = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    db = get_supabase_client()

    rows = (db.table("incidents")
            .select("id,slug,incident_date,source_urls,source_timeline")
            .eq("is_published", True).execute()).data or []

    targets = []
    for r in rows:
        pubs = {e.get("date") for e in (r.get("source_timeline") or [])
                if isinstance(e, dict) and e.get("date")}
        if not pubs:
            if args.include_undated:
                targets.append((r, None))
            continue
        if r.get("incident_date") in pubs:
            targets.append((r, sorted(pubs)[0]))

    if args.limit:
        targets = targets[:args.limit]

    print(f"{len(rows)} published incidents; {len(targets)} carry the bug signature\n")

    changed = unchanged = unresolved = 0
    updates = []
    for i, (r, _) in enumerate(targets, 1):
        cur = r.get("incident_date")
        tl = {e.get("source_url"): e.get("date")
              for e in (r.get("source_timeline") or []) if isinstance(e, dict)}
        urls = r.get("source_urls") or []
        got = None
        used_pub = None
        for u in urls:
            pub = tl.get(u) or cur
            text = _fetch_text(u)
            if not text:
                continue
            got = _sanitise_event_date(_ask(ai, text, pub), pub)
            used_pub = pub
            if got:
                break

        if not got:
            unresolved += 1
            print(f"[{i}/{len(targets)}] ?  {cur}  -> unresolved   {r['slug'][:58]}")
            continue
        if got == cur:
            unchanged += 1
            print(f"[{i}/{len(targets)}] =  {cur}  confirmed      {r['slug'][:58]}")
            continue
        changed += 1
        print(f"[{i}/{len(targets)}] +  {cur} -> {got}  (pub {used_pub})  {r['slug'][:52]}")
        updates.append({"id": r["id"], "slug": r["slug"], "old": cur, "new": got})

    print(f"\n=== summary ===")
    print(f"  examined   : {len(targets)}")
    print(f"  would change: {changed}")
    print(f"  confirmed already correct: {unchanged}")
    print(f"  unresolved (left as-is)  : {unresolved}")

    if not args.apply:
        print("\n  DRY RUN - nothing written. Re-run with --apply to commit.")
        json.dump(updates, open("event_date_proposed.json", "w", encoding="utf-8"), indent=1)
        print("  proposal written to packages/agents/event_date_proposed.json")
        return 0

    json.dump([{k: u[k] for k in ("id", "slug", "old")} for u in updates],
              open("event_date_backup.json", "w", encoding="utf-8"), indent=1)
    for u in updates:
        db.table("incidents").update({"incident_date": u["new"]}).eq("id", u["id"]).execute()
    print(f"\n  APPLIED to {len(updates)} incident(s). Backup: event_date_backup.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
