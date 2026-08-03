"""
One-shot repair of the three live-data defects behind the August-2026 incident
display fixes. DRY RUN BY DEFAULT — pass --apply to write.

    cd packages/agents
    ./.venv/Scripts/python.exe tools/repair_display_data.py            # preview
    ./.venv/Scripts/python.exe tools/repair_display_data.py --apply    # commit

Loads SUPABASE_URL / SUPABASE_SECRET_KEY from the repo-root .env if they are not
already in the environment, so it runs with no setup.

WHAT IT REPAIRS
---------------
The code changes shipped alongside this script fix the pipeline going forward.
They cannot fix rows that are already published, which is what this does.
Each step is independent; a failure in one does not stop the others.

  1. SIGNAL URLS IN source_urls  (migration 016 §1)
     Reddit threads quoted as citations on published incidents — guardrail #2 —
     which also inflated the "Corroborated by N sources" line by one. Strips the
     URL, corrects corroboration_count, credits edmw_signal_count.
     Measured 2026-08-03: 3 rows.
     A row whose ONLY citation is a signal URL is SKIPPED, not emptied: that
     would breach guardrail #1's CHECK (cardinality(source_urls) >= 1) and is an
     editorial decision. See migration 016 §2 — 1 row is in this state.

  2. UNDATED SOURCE LINKS
     The incident page prints each citation's publication date, but a timeline
     entry was only ever written for a consolidated multi-source story, so
     single-source incidents rendered undated. Resolves the real date from each
     article (URL path -> meta tags -> Wayback) and appends it.
     Measured 2026-08-03: 163 undated links; 109 resolvable across 84 incidents.
     The other 54 are left undated — never guessed.

  3. MISSING MAP PINS
     The geocoder only read a block/street from the block_number / area_name
     COLUMNS, so incidents naming their address only in the headline got no pin
     at all ("NSF dies after being pinned down at Block 279 Yishun Street 22",
     block_number=NULL). The code fix mines the title and summary; this re-runs
     the geocoder over every pin-less published incident.
     Measured 2026-08-03: 71 pin-less, 4 now resolve. The remaining 67 name no
     location anywhere and correctly stay unpinned rather than stacking on the
     Yishun centroid.

Step 2 makes ~160 outbound HTTP requests and takes several minutes.
Step 3 is rate-limited to one OneMap call per 0.5 s.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)

# Host-anchored so a path or query mentioning "reddit.com" cannot match.
SIGNAL_HOST = re.compile(
    r"^https?://([a-z0-9-]+\.)*(reddit\.com|hardwarezone\.com\.sg)(/|$)", re.I
)


def _load_env() -> None:
    """Populate Supabase credentials from the repo-root .env if unset."""
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"):
        return
    path = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# ── Step 1 ───────────────────────────────────────────────────────────────────

def step_signal_urls(apply: bool) -> dict:
    from classifiers.corroboration import get_supabase_client

    supabase = get_supabase_client()
    rows = (supabase.table("incidents")
            .select("id,slug,source_urls,corroboration_count,edmw_signal_count")
            .eq("is_published", True).execute().data or [])

    cleaned = skipped = failed = 0
    for row in rows:
        urls = row.get("source_urls") or []
        kept = [u for u in urls if not SIGNAL_HOST.match(u)]
        removed = len(urls) - len(kept)
        if removed == 0:
            continue
        if len(kept) < 1:
            # Guardrail #1: the CHECK would reject this, and choosing between
            # "attach a real source" and "unpublish" is the operator's call.
            skipped += 1
            print(f"  SKIP  {row['slug']}")
            print("        only citation is a signal URL - see migration 016 sec 2")
            continue

        patch = {
            "source_urls":         kept,
            "corroboration_count": max(1, len(kept)),
            "edmw_signal_count":   (row.get("edmw_signal_count") or 0) + removed,
        }
        print(f"  {row['slug']}")
        print(f"        sources {len(urls)} -> {len(kept)} | "
              f"corroboration {row.get('corroboration_count')} -> {patch['corroboration_count']}")
        if apply:
            try:
                supabase.table("incidents").update(patch).eq("id", row["id"]).execute()
            except Exception as exc:                          # noqa: BLE001
                failed += 1
                print(f"        !! write failed: {exc}")
                continue
        cleaned += 1

    return {"cleaned": cleaned, "skipped": skipped, "failed": failed}


# ── Step 2 ───────────────────────────────────────────────────────────────────

def step_source_dates(apply: bool) -> dict:
    from tools.backfill_source_dates import run
    return run(apply=apply)


# ── Step 3 ───────────────────────────────────────────────────────────────────

def step_map_pins(apply: bool) -> dict:
    from classifiers.corroboration import get_supabase_client
    from classifiers.geocoding import geocode_incident_with_method

    supabase = get_supabase_client()
    rows = (supabase.table("incidents")
            .select("id,slug,title,summary,block_number,area_name")
            .eq("is_published", True)
            .is_("latitude", "null").execute().data or [])
    print(f"  pin-less published incidents: {len(rows)}")

    pinned = nothing = failed = 0
    for row in rows:
        coords, method = geocode_incident_with_method(
            row.get("block_number"), row.get("area_name"),
            extra_text=row.get("title"), location_text=row.get("summary"),
        )
        if not coords:
            nothing += 1
            continue
        lat, lon = coords
        print(f"  + {lat:.5f},{lon:.5f}  via {method:<8} {row['slug'][:55]}")
        if apply:
            try:
                (supabase.table("incidents")
                 .update({"latitude": lat, "longitude": lon})
                 .eq("id", row["id"]).execute())
            except Exception as exc:                          # noqa: BLE001
                failed += 1
                print(f"      !! write failed: {exc}")
                continue
        pinned += 1

    print(f"  no usable location (correctly left unpinned): {nothing}")
    return {"pinned": pinned, "unpinned": nothing, "failed": failed}


STEPS = {
    "signal-urls":  ("Signal URLs in source_urls (migration 016 sec 1)", step_signal_urls),
    "source-dates": ("Undated source links",                          step_source_dates),
    "map-pins":     ("Missing map pins",                              step_map_pins),
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write to Supabase (default is a dry run)")
    ap.add_argument("--only", choices=sorted(STEPS), action="append",
                    help="run only this step (repeatable)")
    args = ap.parse_args()

    _load_env()
    if not os.getenv("SUPABASE_SECRET_KEY"):
        print("SUPABASE_SECRET_KEY is not set and no repo-root .env was found.")
        return 2

    chosen = args.only or list(STEPS)
    mode = "APPLY - WRITING TO THE LIVE DATABASE" if args.apply else "DRY RUN - no writes"
    print(f"\n{'=' * 68}\n  repair_display_data - {mode}\n{'=' * 68}")

    results, failures = {}, 0
    for key in chosen:
        label, fn = STEPS[key]
        print(f"\n--- {label} ---")
        try:
            results[key] = fn(args.apply)
            failures += results[key].get("failed", 0)
        except Exception as exc:                              # noqa: BLE001
            # One broken step must not cost the others.
            failures += 1
            results[key] = {"error": str(exc)}
            print(f"  !! step failed: {exc}")

    print(f"\n{'=' * 68}\n  SUMMARY\n{'=' * 68}")
    for key, res in results.items():
        print(f"  {STEPS[key][0]}")
        print(f"      {res}")
    if not args.apply:
        print("\n  DRY RUN - nothing was written. Re-run with --apply to commit.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
