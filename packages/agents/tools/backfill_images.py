"""
Generate art for every published incident stuck at image_status='pending'.

WHY THIS EXISTS
---------------
162 published incidents have never had image generation attempted. `pending`
is the DISABLED return from both writers (War Room approve, auto_publish) —
"the backend was never reachable and nothing was attempted" — not "queued".
That was caused by two compounding infra bugs, both fixed 2026-08-04:

  1. Cloud Run was deployed --no-allow-unauthenticated, so the War Room's
     approve-time call to /art/generate 403'd at the edge and never reached
     FastAPI (CLAUDE.md § Deployment).
  2. ART_GENERATION_ENABLED was false, so ops/auto_publish.py's own writer
     never called generate_image at all.

Both are fixed now — verified by hand-generating 4 images through the War
Room's /rectify UI, all `ok` on the first attempt. This script is the backfill
for the other 162, which is a scale problem the operator UI is the wrong tool
for (one row, one click, one operator watching).

This calls art.generate_image DIRECTLY — the same function
ops/auto_publish.py imports — rather than going over HTTP through the Cloud
Run service. No OPS_TOKEN, no Vercel hop: just the Gemini/R2/Supabase
credentials already in this environment's .env. Guardrail #5 (suicide/
self-harm suppression) still runs first, inside generate_image, exactly as it
does for every other caller — this backfill cannot bypass it, and should not
try to.

COST
----
gemini-3.1-flash-lite-image is $0.0336/image at the standard (non-batch) rate
(docs/ART_PIPELINE.md; IMAGE_USE_BATCH=false in this deployment). Each
incident may cost more than one image if the softening ladder retries — every
attempt calls Gemini. 162 incidents:

    best case  (1 attempt each):  162 x $0.0336  ~= $5.44
    worst case (up to 3 rungs):   162 x $0.1008  ~= $16.33

Either figure exceeds COST_ALERT_USD_PER_DAY's $2.00 default for a single
day — this IS the "large deliberate spend" that guard exists to flag, not
something to route around. `--limit` runs a bounded slice so you can see real
attempt counts before committing to the rest.

WHAT IT WRITES
--------------
Only the three image columns, and only on rows still at image_status='pending'
at write time (`.eq('image_status', 'pending')` alongside the id match — an
optimistic check, not a lock, so a row an operator started rectifying by hand
mid-run is not clobbered). `suppressed` outcomes are written too: this is the
first time guardrail #5 has had a chance to evaluate these 162 rows, so some
suicide/self-harm incidents are expected to land there, terminally, correctly.

Does NOT call the web revalidate endpoint. That endpoint rate-limits to 10
req/min per IP (apps/web/app/api/revalidate/route.ts), which 162 sequential
calls would blow through immediately, and unlike a single operator rectify this
is not urgent — every incident page already has `revalidate = 3600`, so each
one self-heals within an hour of being written regardless.

USAGE
-----
    ./.venv/Scripts/python.exe tools/backfill_images.py                # dry run
    ./.venv/Scripts/python.exe tools/backfill_images.py --limit 10 --apply
    ./.venv/Scripts/python.exe tools/backfill_images.py --apply         # the rest

Naturally resumable: it re-queries `image_status='pending'` on every
invocation, and a processed row leaves that status, so a second run only ever
sees what the first run did not finish or did not attempt.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)

COST_PER_IMAGE_USD = 0.0336  # gemini-3.1-flash-lite-image, standard rate (docs/ART_PIPELINE.md)

INCIDENT_COLUMNS = "id,slug,title,summary,classification,severity,area_name,tags,image_status"


def _load_env() -> None:
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually call Gemini and write to Supabase (default is a dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N incidents this run (0 = all pending)")
    ap.add_argument("--sleep", type=float, default=2.0,
                    help="seconds to wait between incidents (default 2.0) — a courtesy "
                         "to the Gemini rate limit, not something this codebase enforces "
                         "elsewhere for image generation")
    args = ap.parse_args()

    _load_env()
    if not os.getenv("SUPABASE_SECRET_KEY"):
        print("SUPABASE_SECRET_KEY is not set and no repo-root .env was found.")
        return 2
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set — every call will fail 'transient'.")
        return 2

    from classifiers.corroboration import get_supabase_client
    supabase = get_supabase_client()

    q = (supabase.table("incidents")
         .select(INCIDENT_COLUMNS)
         .eq("is_published", True)
         .eq("image_status", "pending")
         .order("published_at", desc=False))
    if args.limit:
        q = q.limit(args.limit)
    rows = q.execute().data or []

    mode = "APPLY — calling Gemini and writing to Supabase" if args.apply else "DRY RUN — no calls, no writes"
    print(f"\n{'=' * 68}\n  backfill_images — {mode}\n{'=' * 68}")
    print(f"  pending incidents in scope: {len(rows)}")
    if not args.apply:
        lo = len(rows) * COST_PER_IMAGE_USD
        hi = len(rows) * COST_PER_IMAGE_USD * 3
        print(f"  estimated cost: ${lo:.2f} best case (1 attempt each) "
              f"to ${hi:.2f} worst case (3 rungs each)")
        print("\n  Re-run with --apply to generate. --limit N to bound the first run.")
        return 0

    from art.generate_image import generate_image

    ok = suppressed = failed = 0
    attempts_total = 0

    for i, row in enumerate(rows, 1):
        slug = row["slug"]
        result = generate_image(row)  # fresh AttemptBudget per incident — no cross-row cap
        n_attempts = len(result.attempts) or 1
        attempts_total += n_attempts
        running_cost = attempts_total * COST_PER_IMAGE_USD

        patch = {
            "image_status":   result.status,
            "image_prompt":   result.final_prompt or None,
            "image_attempts": [a for a in result.attempts] or None,
        }
        if result.status == "ok":
            patch["pixel_art_url"] = result.url
            ok += 1
        elif result.status == "suppressed":
            suppressed += 1
        else:
            failed += 1

        (supabase.table("incidents")
         .update(patch)
         .eq("id", row["id"])
         .eq("image_status", "pending")   # don't clobber a row an operator already touched
         .execute())

        print(f"  [{i}/{len(rows)}] {result.status:<10} "
              f"({n_attempts} attempt{'s' if n_attempts != 1 else ''}, "
              f"~${running_cost:.2f} running total)  {slug}")

        if i < len(rows) and args.sleep > 0:
            time.sleep(args.sleep)

    print(f"\n{'=' * 68}\n  SUMMARY\n{'=' * 68}")
    print(f"  ok:         {ok}")
    print(f"  suppressed: {suppressed}  (guardrail #5 — terminal, correct, not a failure)")
    print(f"  failed:     {failed}  (refused/transient/invalid/skipped — visible at /rectify)")
    print(f"  attempts:   {attempts_total}  (~${attempts_total * COST_PER_IMAGE_USD:.2f} estimated)")
    print("\n  Pages self-heal within the 3600s ISR window — no revalidation was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
