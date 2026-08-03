"""
Give every source link on every published incident its article's publication date.

WHY THIS EXISTS
---------------
The incident page prints the publication date beside each source link, reading
it from `incidents.source_timeline`. But a timeline entry is only ever written
for a story that was CONSOLIDATED from several reports — a single-source
incident gets `source_timeline: []`, and a multi-source one only gets entries
for the reports the consolidation step actually merged. Audited 2026-08-03:
163 source links across the 163 published incidents had no date at all, and
the page rendered those links bare.

The date cannot be inferred from the row. `incident_date` is when the EVENT
happened and `published_at` is when Yishun Again published — a follow-up filed
two years later shares neither. So this tool goes and reads the date off the
article, using the same `scrapers.resolve_published_at()` the ingestion
pipeline uses (URL path → article meta tags → newest Wayback snapshot).

WHAT IT WRITES
--------------
Only `incidents.source_timeline`, and only by APPENDING. Existing entries are
copied through byte-for-byte — their `role`, `headline` and `source_name` are
operator- and pipeline-authored and this tool has no business rewriting them.

Appended entries deliberately carry NO `role`. `collapseTimelineByDate()` in
the frontend ranks a missing role lowest, so a backfilled entry can never
outrank a real `verdict`/`initial` label on the same date, and
`lastVerdictEntry()` (which drives "time to verdict") only scans for verdict
roles and so cannot see them either. A backfilled entry on a genuinely new
date does add a node to the story timeline — that is correct: it is a real
report filed on a real date that the timeline was previously silent about.

A URL whose date cannot be resolved is left alone. Nothing is guessed: the page
renders such a link as "Undated", which is honest, where a fabricated date
would not be.

USAGE
-----
    ./.venv/Scripts/python.exe tools/backfill_source_dates.py            # DRY RUN
    ./.venv/Scripts/python.exe tools/backfill_source_dates.py --limit 5  # sample
    ./.venv/Scripts/python.exe tools/backfill_source_dates.py --apply    # writes

Dry run is the default and performs the network reads but no writes, so the
report below is exactly what --apply would do.
"""

import argparse
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifiers.corroboration import get_supabase_client   # noqa: E402
from scrapers import resolve_published_at                   # noqa: E402


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").replace("www.", "").lower()
    except Exception:                                       # noqa: BLE001
        return ""


def _source_names(supabase) -> dict[str, str]:
    """domain → display name, from the operator-curated `sources` table."""
    names: dict[str, str] = {}
    try:
        # `sources` has no domain column — the domain is derived from `url`.
        rows = supabase.table("sources").select("name,url").execute().data or []
    except Exception as exc:                                # noqa: BLE001
        print(f"  (sources table unreadable, falling back to hostnames: {exc})")
        return names
    for r in rows:
        dom = _domain(r.get("url") or "")
        if dom and r.get("name"):
            names.setdefault(dom, r["name"])
    return names


def _display_name(url: str, names: dict[str, str]) -> str:
    dom = _domain(url)
    if dom in names:
        return names[dom]
    # Suffix match, so cnalifestyle.channelnewsasia.com inherits CNA's name.
    for known, name in names.items():
        if dom.endswith("." + known):
            return name
    return dom


def run(apply: bool = False, limit: int = 0, slug: str = "") -> dict:
    """Resolve and append missing source dates. Returns a stats dict.

    Exposed as a function so tools/repair_display_data.py can run this as one
    step of the combined repair without shelling out.
    """
    supabase = get_supabase_client()
    names = _source_names(supabase)

    q = (supabase.table("incidents")
         .select("id,slug,source_urls,source_timeline")
         .eq("is_published", True)
         .order("incident_date", desc=True))
    if slug:
        q = q.eq("slug", slug)
    rows = q.execute().data or []

    mode = "APPLY (writing)" if apply else "DRY RUN (no writes)"
    print(f"=== backfill_source_dates - {mode} ===")
    print(f"published incidents: {len(rows)}\n")

    touched = resolved = unresolved = failed = 0
    considered = 0

    for row in rows:
        urls = [u for u in dict.fromkeys(row.get("source_urls") or []) if u]
        timeline = list(row.get("source_timeline") or [])
        dated = {
            e.get("source_url") for e in timeline
            if isinstance(e, dict) and e.get("source_url") and e.get("date")
        }
        missing = [u for u in urls if u not in dated]
        if not missing:
            continue

        considered += 1
        if limit and considered > limit:
            considered -= 1
            break

        print(f"{row['slug']}  ({len(missing)}/{len(urls)} undated)")
        additions = []
        for url in missing:
            try:
                found = resolve_published_at(url)
            except Exception as exc:                        # noqa: BLE001
                # resolve_published_at documents that it never raises; belt and
                # braces so one bad URL cannot end the whole backfill.
                print(f"    !! {_domain(url)}: {exc}")
                found = None
            if found:
                iso = found.isoformat()
                additions.append({
                    "date":        iso,
                    "source_url":  url,
                    "source_name": _display_name(url, names),
                    "headline":    "",
                })
                resolved += 1
                print(f"    +  {iso}  {_domain(url)}")
            else:
                unresolved += 1
                print(f"    ?  unresolved  {_domain(url)}")

        if not additions:
            continue

        merged = timeline + additions
        merged.sort(key=lambda e: str(e.get("date") or "9999-99-99"))

        if apply:
            try:
                (supabase.table("incidents")
                 .update({"source_timeline": merged})
                 .eq("id", row["id"]).execute())
                touched += 1
            except Exception as exc:                        # noqa: BLE001
                failed += 1
                print(f"    !! write failed: {exc}")
        else:
            touched += 1

    print("\n=== summary ===")
    print(f"  incidents {'updated' if apply else 'that WOULD be updated'}: {touched}")
    print(f"  dates resolved:   {resolved}")
    print(f"  dates unresolved: {unresolved}  (left undated - never guessed)")
    if apply:
        print(f"  write failures:   {failed}")
    else:
        print("\n  DRY RUN - nothing was written. Re-run with --apply to commit.")

    return {"touched": touched, "resolved": resolved,
            "unresolved": unresolved, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write to Supabase (default is a dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N incidents needing work")
    ap.add_argument("--slug", type=str, default="",
                    help="only process this one incident")
    args = ap.parse_args()
    stats = run(apply=args.apply, limit=args.limit, slug=args.slug)
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
