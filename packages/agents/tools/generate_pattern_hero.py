"""
Generate a hero image for one curated pattern (packages/db/migrations/022).

Reuses the incident art pipeline UNCHANGED (art.generate_image.generate_image) by
treating the pattern's title + thesis as a synthetic "incident": same Haiku scene
writer, same deterministic style/palette/exclusions template, same Gemini call,
same guardrail #5 suicide/self-harm check, same crop-to-1200x630 and R2 upload.
No new prompt logic — the whole point is one image pipeline, not two.

The synthetic incident's slug is `pattern-<pattern-slug>`, which can never
collide with a real incident slug (none start with "pattern-"), so the R2 object
lands at pixel-art/pattern-<slug>.png alongside incident art with no risk of
overwriting one.

USAGE
-----
    cd packages/agents
    ./.venv/Scripts/python.exe tools/generate_pattern_hero.py --slug kurt-tay-superstar
    ./.venv/Scripts/python.exe tools/generate_pattern_hero.py --slug kurt-tay-superstar --apply
    ./.venv/Scripts/python.exe tools/generate_pattern_hero.py --slug kurt-tay-superstar --apply --classification dagger
    ./.venv/Scripts/python.exe tools/generate_pattern_hero.py --slug kurt-tay-superstar --apply \
        --note "Kurt Tay has a skinny-fat build, not muscular. He wears a wrestling championship belt and has set up a selfie stick / phone tripod, livestreaming the fight himself."

Dry run by default (writes nothing, calls nothing) — pass --apply to actually
call Gemini and write patterns.hero_image_url.

--classification picks the template's colour accent (heart=teal, clown=yellow,
dagger=coral) and defaults to 'clown', since these are character/theme pieces
rather than a single dark event; override per pattern with judgement.

--note appends extra scene guidance (character build, props, framing) that the
Haiku scene writer reads alongside the thesis. It is NOT saved to patterns.thesis
— the published prose is untouched; the note exists only for this render. Re-run
with a refined --note to iterate on one pattern's look; the R2 key is stable
(pixel-art/pattern-<slug>.png) so a re-run overwrites the same object under a new
content hash rather than orphaning the old one.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._env import load_env as _load_env  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True, help="patterns.slug to generate a hero image for")
    ap.add_argument("--classification", default="clown", choices=["heart", "clown", "dagger"],
                    help="palette/tone for the template (default: clown)")
    ap.add_argument("--apply", action="store_true",
                    help="actually call Gemini/R2 and write hero_image_url (default is a dry run)")
    ap.add_argument("--note", default="",
                    help="extra scene guidance (character look, props) appended for this render only "
                         "— never written to patterns.thesis")
    args = ap.parse_args()

    _load_env()
    if not os.getenv("SUPABASE_SECRET_KEY"):
        print("SUPABASE_SECRET_KEY is not set and no repo-root .env was found.")
        return 2
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set — the call would fail 'transient'.")
        return 2

    from classifiers.corroboration import get_supabase_client
    supabase = get_supabase_client()

    row = (supabase.table("patterns")
           .select("id,slug,title,thesis,hero_image_url")
           .eq("slug", args.slug).single().execute().data)
    if not row:
        print(f"No pattern found with slug={args.slug!r}")
        return 1

    summary = row["thesis"]
    if args.note.strip():
        summary = f"{summary}\n\nART DIRECTION FOR THIS SCENE: {args.note.strip()}"

    incident = {
        "slug":           f"pattern-{row['slug']}",
        "title":          row["title"],
        "summary":        summary,
        "classification": args.classification,
        "severity":       3,
        "area_name":      "Yishun",
    }

    print(f"\n{'=' * 68}\n  generate_pattern_hero — {row['slug']}\n{'=' * 68}")
    print(f"  title:          {row['title']}")
    print(f"  classification: {args.classification}")
    print(f"  existing hero:  {row.get('hero_image_url') or '(none)'}")
    if args.note.strip():
        print(f"  art note:       {args.note.strip()}")

    if not args.apply:
        print("\n  DRY RUN — no calls, no writes. Re-run with --apply to generate.")
        return 0

    from art.generate_image import generate_image
    result = generate_image(incident)

    print(f"\n  status:   {result.status}")
    print(f"  attempts: {len(result.attempts)}")
    for a in result.attempts:
        print(f"    [{a['n']}] {a['outcome']}" + (f" — {a['reason']}" if a.get("reason") else ""))

    if result.status != "ok":
        print(f"\n  No image written. Prompt used:\n{result.final_prompt[:500]}")
        return 1

    (supabase.table("patterns")
     .update({"hero_image_url": result.url, "updated_at": datetime.now(timezone.utc).isoformat()})
     .eq("id", row["id"]).execute())

    print(f"\n  hero_image_url set: {result.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
