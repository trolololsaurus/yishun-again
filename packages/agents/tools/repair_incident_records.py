"""
Repairs for four live-data defects found on 2026-08-05. DRY RUN BY DEFAULT —
pass --apply to write.

    cd packages/agents
    ./.venv/Scripts/python.exe tools/repair_incident_records.py                 # preview all
    ./.venv/Scripts/python.exe tools/repair_incident_records.py --only merge    # preview one
    ./.venv/Scripts/python.exe tools/repair_incident_records.py --apply         # commit

Loads SUPABASE_URL / SUPABASE_SECRET_KEY from the repo-root .env if they are not
already in the environment, so it runs with no setup.

WHAT IT REPAIRS
---------------
  1. published_at   MISSING PUBLICATION TIMESTAMPS
     23 incidents are is_published=true with published_at IS NULL — all seeded
     by the June-2026 backfill, before `backfill_agent._build_incident_row`
     started stamping the column. The War Room's Published column renders them
     as an em dash, and `ORDER BY published_at DESC` puts NULLs FIRST in
     Postgres, so they occupied the whole top of page 1.
     Sets published_at = created_at: on 134 of the 146 rows that DO carry the
     column the two agree to the day, because publishing IS what created the
     row. Drafts (is_published=false) are left NULL — that is correct for them.

  2. title           TRUNCATED McDONALD'S TITLE
     `yishun-mcdonalds-bomb-hoax-2023` published as
     "A false bomb report at a Yishun McDon's". The June-2026 backfill writer
     mangled "McDonald's"; the row's own source_timeline headline and the ST
     article both spell it in full.

  3. merge           TWO ROWS, ONE INCIDENT (x2 — see MERGE_PAIRS)
     Operator-identified duplicates. Each pair was ingested weeks apart, so the
     pipeline's own consolidation never compared them.

       a. 24 Jul 2026, Block 243 Yishun Ring Road, a 48-year-old beaten
          unconscious. `…group-knife-attack-block-243-carpark…` is the thin
          25 Jul report; `…ring-road-rioting-carpark-brawl…` SURVIVES as the
          fuller 28 Jul story carrying the rioting charges. The absorbed row's
          summary is no loss — it attributes the story to "a Reddit Singapore
          post" while citing Straits Times and Zaobao, and its headline claims
          a knife that no source reports.
       b. 19 Jan 2026, Yishun Integrated Transport Hub, a man assaults a Tower
          Transit ambassador then sprays a fire extinguisher through the
          interchange and Northpoint City. `…bus-staff-assault-fire-
          extinguisher-interchange` is the ST arrest report;
          `tower-transit-staff-restrain-…` SURVIVES as the Stomp follow-up on
          the two staff who stopped him — and is the one carrying map
          coordinates. It gains a first_reported_at it did not have.

     The absorbed row is UNPUBLISHED, never deleted: `training_signals`
     .incident_id is ON DELETE CASCADE, and deleting would destroy the operator
     decisions recorded against it. Its incident_links are dropped after the
     script has checked that every partner is one the survivor already holds —
     any that is not is printed as an orphan for the operator to re-point.
     The survivor's title, summary, classification and art are kept as-is;
     picking the survivor IS the editorial decision, and this script does not
     second-guess it.

     Each merge needs a matching redirect in `apps/web/next.config.js`, or the
     absorbed row's URL — live, shared and indexed — becomes a 404.

  4. url_date        A FABRICATED PUBLICATION DATE
     The survivor's timeline dated the Mothership article 2026-07-06, eighteen
     days BEFORE the incident. `scrapers._URL_DATE_RE` ended in `(?:/|\\b)`, and
     on `mothership.sg/2026/07/6-men-charged-yishun-rioting/` the `\\b` between
     "6" and "-" satisfied it, so "6-men-charged" was read as day 6. The article
     is dated 29 Jul 2026 (verified against its own byline).
     The regex is fixed in `scrapers/__init__.py` — this step repairs the row.
     Guard: `test_url_date_extraction.py`.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)

# ── Step 3/4 constants — the exact rows, named so a typo cannot hit another ──
#
# (survivor, absorbed, options). Operator-identified duplicates; the pipeline's
# own consolidation never saw them as one story because they were ingested weeks
# apart, past the recency window that would have compared them.
#
# Options, all optional:
#   absorbed_role  role stamped on absorbed timeline entries that carry none.
#                  An entry with no role renders as "REPORTED" — fine for a news
#                  report, wrong for a court ruling.
#   patch          extra survivor columns to set.
#   take_image     copy pixel_art_url + image_prompt from the absorbed row. For
#                  when the survivor's own render hallucinated and the absorbed
#                  row's did not.
MERGE_PAIRS = [
    # Same event: 24 Jul 2026, Block 243 Yishun Ring Road, 48-year-old beaten
    # unconscious. The absorbed row is the thin 25 Jul report; the survivor is
    # the 28 Jul story carrying the rioting charges.
    ("yishun-ring-road-rioting-carpark-brawl-jul-2026",
     "yishun-group-knife-attack-block-243-carpark-jul-2026",
     {}),
    # Same event: 19 Jan 2026, Yishun Integrated Transport Hub, man assaults a
    # Tower Transit ambassador then sprays a fire extinguisher. The absorbed row
    # is the ST arrest report; the survivor is the Stomp follow-up on the two
    # staff who restrained him — and it is the one with map coordinates.
    ("tower-transit-staff-restrain-fire-extinguisher-man-yishun-jan-2026",
     "yishun-bus-staff-assault-fire-extinguisher-interchange",
     {}),
    # Same case, 6 years apart: Wang Zhijian's 2008 triple murder at Block 349
    # Yishun Ave 11 and the 2014 Court of Appeal ruling that concluded it. The
    # absorbed row is a standalone card for the appeal; the survivor already
    # carries the whole story and already holds the eLitigation judgment for the
    # same 28 Nov 2014 ruling, so the absorbed row adds CNA's news report of it.
    #
    # latest_source_role was 'verdict'; the last court event is the appeal
    # dismissal, and migration 008 added that value for exactly this shape of
    # story. conclusion_type stays 'verdict' — 003 constrains it to
    # verdict/timeout/operator and 'verdict' is the right one of the three.
    #
    # take_image: the survivor's own render is a fantasy tavern, chalkboard
    # reading "STEW: 5 COPPER" — a total hallucination on a triple-murder page.
    # The absorbed row's is an HDB kitchen with blocks through the window, which
    # is where this happened.
    ("yishun-triple-murder-wang-zhijian-block-349-2008",
     "yishun-triple-murder-death-penalty-appeal-dismissed-nov-2014",
     {"absorbed_role": "appeal_dismissed",
      "patch": {"latest_source_role": "appeal_dismissed"},
      "take_image": True}),
]

# The row step 4 repairs, and the row step 3's first pair keeps.
MERGE_KEEP_SLUG = MERGE_PAIRS[0][0]

BAD_DATE_URL   = "https://mothership.sg/2026/07/6-men-charged-yishun-rioting/"
BAD_DATE_WAS   = "2026-07-06"
BAD_DATE_IS    = "2026-07-29"

TITLE_SLUG = "yishun-mcdonalds-bomb-hoax-2023"
TITLE_WAS  = "A false bomb report at a Yishun McDon's"
TITLE_IS   = "A false bomb report at a Yishun McDonald's"


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


def _client():
    from classifiers.corroboration import get_supabase_client
    return get_supabase_client()


# ── Step 1: published_at ─────────────────────────────────────────────────────

def step_published_at(apply: bool) -> dict:
    supabase = _client()
    rows = (supabase.table("incidents")
            .select("id,slug,created_at,published_at,is_published")
            .is_("published_at", "null")
            .eq("is_published", True)
            .execute().data or [])

    fixed = failed = 0
    for row in rows:
        created = row.get("created_at")
        if not created:
            # Nothing to copy from — never invent a date on a published page.
            failed += 1
            print(f"  SKIP  {row['slug']}  (no created_at)")
            continue
        print(f"  SET   {row['slug'][:58]:58} published_at <- {created[:10]}")
        if apply:
            res = (supabase.table("incidents")
                   .update({"published_at": created})
                   .eq("id", row["id"]).execute())
            if not res.data:
                failed += 1
                print("        !! update returned no rows")
                continue
        fixed += 1

    print(f"  {fixed} row(s) {'updated' if apply else 'would be updated'}")
    return {"candidates": len(rows), "fixed": fixed, "failed": failed}


# ── Step 2: title ────────────────────────────────────────────────────────────

def step_title(apply: bool) -> dict:
    supabase = _client()
    rows = (supabase.table("incidents").select("id,slug,title")
            .eq("slug", TITLE_SLUG).execute().data or [])
    if not rows:
        print(f"  SKIP  {TITLE_SLUG} not found")
        return {"fixed": 0, "failed": 0}

    row = rows[0]
    if row["title"] != TITLE_WAS:
        # Already corrected, or edited to something else — never overwrite
        # an operator's wording on a hunch.
        print(f"  SKIP  title is not the known-bad string: {row['title']!r}")
        return {"fixed": 0, "failed": 0}

    print(f"  WAS   {TITLE_WAS!r}")
    print(f"  IS    {TITLE_IS!r}")
    if apply:
        res = supabase.table("incidents").update({"title": TITLE_IS}).eq("id", row["id"]).execute()
        if not res.data:
            print("        !! update returned no rows")
            return {"fixed": 0, "failed": 1}
    return {"fixed": 1, "failed": 0}


# ── Step 4: the fabricated URL date (runs before the merge reads the row) ────

def step_url_date(apply: bool) -> dict:
    supabase = _client()
    rows = (supabase.table("incidents").select("id,slug,source_timeline")
            .eq("slug", MERGE_KEEP_SLUG).execute().data or [])
    if not rows:
        print(f"  SKIP  {MERGE_KEEP_SLUG} not found")
        return {"fixed": 0, "failed": 0}

    row = rows[0]
    timeline = list(row.get("source_timeline") or [])
    touched = 0
    for entry in timeline:
        if entry.get("source_url") == BAD_DATE_URL and entry.get("date") == BAD_DATE_WAS:
            entry["date"] = BAD_DATE_IS
            touched += 1

    if not touched:
        print("  SKIP  no timeline entry carries the known-bad date")
        return {"fixed": 0, "failed": 0}

    print(f"  {row['slug']}")
    print(f"        {BAD_DATE_URL}")
    print(f"        date {BAD_DATE_WAS} -> {BAD_DATE_IS}")
    if apply:
        res = (supabase.table("incidents").update({"source_timeline": timeline})
               .eq("id", row["id"]).execute())
        if not res.data:
            print("        !! update returned no rows")
            return {"fixed": 0, "failed": 1}
    return {"fixed": touched, "failed": 0}


# ── Step 3: the merge ────────────────────────────────────────────────────────

def _canon(url: str) -> str:
    """Cheap canonicalisation for dedup — scheme, www and trailing slash only.

    Deliberately NOT the full tracking-param strip that apps/web does: this
    only decides whether two spellings are the SAME article before appending,
    and merging two genuinely distinct URLs is far worse than keeping one
    duplicate that the render-time counter already collapses.
    """
    u = (url or "").strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def step_merge(apply: bool) -> dict:
    total = {"fixed": 0, "failed": 0, "added_sources": 0, "orphan_links": 0}
    for keep_slug, drop_slug, opts in MERGE_PAIRS:
        print(f"\n  >> {drop_slug}\n     -> {keep_slug}")
        try:
            res = _merge_one(keep_slug, drop_slug, apply, opts)
        except Exception as exc:                              # noqa: BLE001
            # One pair must not cost the others. supabase-py RAISES on a
            # constraint violation rather than returning empty data, so
            # without this a single bad row aborts the whole list — which is
            # what happened on the first --apply run (hype_meter's
            # BETWEEN 0 AND 5 check), and the second pair never ran.
            print(f"  !! merge failed, other pairs continue: {exc}")
            total["failed"] += 1
            continue
        for k, v in res.items():
            total[k] = total.get(k, 0) + v
    return total


def _merge_one(keep_slug: str, drop_slug: str, apply: bool, opts: dict) -> dict:
    supabase = _client()
    base_cols = ["id", "slug", "title", "source_urls", "source_timeline",
                 "corroboration_count", "hype_meter", "edmw_signal_count",
                 "update_count", "incident_date", "first_reported_at",
                 "is_published", "pixel_art_url", "image_prompt",
                 "image_status", "image_attempts"]
    # Whatever this pair patches has to be SELECTED too, or the dry run prints
    # "None -> x" for a column that already holds a value — and a preview that
    # misreports the current state is worse than no preview.
    cols = ",".join(dict.fromkeys(base_cols + list((opts.get("patch") or {}).keys())))

    rows = (supabase.table("incidents").select(cols)
            .in_("slug", [keep_slug, drop_slug]).execute().data or [])
    by_slug = {r["slug"]: r for r in rows}
    keep, drop = by_slug.get(keep_slug), by_slug.get(drop_slug)

    if not keep or not drop:
        print("  SKIP  one or both rows are missing — nothing merged")
        return {"fixed": 0, "failed": 1}
    if not drop["is_published"]:
        print(f"  SKIP  {drop_slug} is already unpublished — merge looks done")
        return {"fixed": 0, "failed": 0}

    # ── source_urls: union, survivor's order first ──
    seen = {_canon(u) for u in (keep["source_urls"] or [])}
    merged_urls = list(keep["source_urls"] or [])
    added_urls = []
    for u in (drop["source_urls"] or []):
        if _canon(u) in seen:
            continue
        seen.add(_canon(u))
        merged_urls.append(u)
        added_urls.append(u)

    # ── source_timeline: union keyed on the URL, then sorted by date ──
    keep_tl = list(keep["source_timeline"] or [])
    tl_seen = {_canon(e.get("source_url", "")) for e in keep_tl}
    added_tl = [dict(e) for e in (drop["source_timeline"] or [])
                if _canon(e.get("source_url", "")) not in tl_seen]
    # An entry with no role renders as "REPORTED". Correct for a news report,
    # wrong for a court ruling — and the role also decides which entry
    # represents a shared date once `collapseTimelineByDate` folds them.
    absorbed_role = opts.get("absorbed_role")
    if absorbed_role:
        for entry in added_tl:
            entry.setdefault("role", absorbed_role)
    merged_tl = sorted(keep_tl + added_tl, key=lambda e: e.get("date") or "")

    # ── first_reported_at: the EARLIEST real report across both rows ──
    # Taken from the two rows' own first_reported_at, never min(timeline): the
    # timeline is exactly where a fabricated date would hide (see step 4).
    firsts = [d for d in (keep.get("first_reported_at"), drop.get("first_reported_at")) if d]
    first_reported = min(firsts) if firsts else keep.get("first_reported_at")

    patch = {
        "source_urls":         merged_urls,
        "source_timeline":     merged_tl,
        "corroboration_count": len(merged_urls),
        # Legacy column — nothing renders it any more, but leaving it stale
        # would be a fresh disagreement for the next reader to trip over.
        #
        # CLAMPED TO 5 because migration 001 declares
        # `CHECK (hype_meter BETWEEN 0 AND 5)`, and 8 sources - 1 = 7 fails it.
        # This is a constraint, not a display choice: the public page derives
        # its bolts from `source_urls` with no ceiling (a 12-source incident
        # shows 11), so the column cannot represent what the site renders. One
        # more reason nothing reads it.
        "hype_meter":          min(5, max(0, len(merged_urls) - 1)),
        "edmw_signal_count":   (keep.get("edmw_signal_count") or 0)
                               + (drop.get("edmw_signal_count") or 0),
        "first_reported_at":   first_reported,
        "update_count":        (keep.get("update_count") or 0) + 1,
    }

    # ── the absorbed row's picture, when the survivor's own render is wrong ──
    #
    # Only the URL and the prompt move. The R2 object keeps the absorbed row's
    # slug in its filename, which is cosmetically odd and factually honest —
    # re-uploading it under the survivor's slug would need the art pipeline, and
    # the asset is not deleted by anything here.
    #
    # `image_attempts` is a LOG and is appended to, never replaced: attempt 1 on
    # the survivor really did produce what it produced, and erasing that would
    # leave a prompt that no recorded attempt explains. The appended entry is
    # what /rectify's "Retry as-is" will reach for.
    if opts.get("take_image"):
        donor_url    = drop.get("pixel_art_url")
        donor_prompt = drop.get("image_prompt")
        if not donor_url:
            print("  !! take_image requested but the absorbed row has no image — skipped")
        else:
            attempts = list(keep.get("image_attempts") or [])
            attempts.append({
                "n":       len(attempts) + 1,
                "prompt":  donor_prompt or "",
                "outcome": "ok",
                "reason":  f"image adopted from merged incident {drop['slug']}",
            })
            patch.update({
                "pixel_art_url":  donor_url,
                "image_prompt":   donor_prompt,
                "image_status":   drop.get("image_status") or "ok",
                "image_attempts": attempts,
            })
            print(f"  image          adopted from the absorbed row")
            print(f"          was  {keep.get('pixel_art_url')}")
            print(f"          now  {donor_url}")

    # Per-pair overrides last, so an explicit value always wins.
    patch.update(opts.get("patch") or {})

    print(f"  KEEP  {keep['slug']}")
    print(f"        {keep['title'][:80]}")
    print(f"  DROP  {drop['slug']}")
    print(f"        {drop['title'][:80]}")
    print(f"  sources        {len(keep['source_urls'] or [])} -> {len(merged_urls)}")
    for u in added_urls:
        print(f"          + {u}")
    print(f"  timeline       {len(keep_tl)} -> {len(merged_tl)} entries")
    print(f"  first_reported {keep.get('first_reported_at')} -> {first_reported}")
    print(f"  corroboration  {keep.get('corroboration_count')} -> {patch['corroboration_count']}")
    print(f"  edmw_signal    {keep.get('edmw_signal_count')} -> {patch['edmw_signal_count']}")
    print(f"  update_count   {keep.get('update_count')} -> {patch['update_count']}")
    for field, value in (opts.get("patch") or {}).items():
        print(f"  {field:14} {keep.get(field)} -> {value}")
    if absorbed_role and added_tl:
        print(f"  absorbed entries stamped role={absorbed_role}")

    # ── links: every one of the loser's is either a self-link to the survivor
    #    or a duplicate of one the survivor already holds. Report, then drop. ──
    links = (supabase.table("incident_links").select("id,incident_a,incident_b,link_type")
             .or_(f"incident_a.eq.{drop['id']},incident_b.eq.{drop['id']}")
             .execute().data or [])
    keep_partners = set()
    for l in (supabase.table("incident_links").select("incident_a,incident_b")
              .or_(f"incident_a.eq.{keep['id']},incident_b.eq.{keep['id']}")
              .execute().data or []):
        keep_partners.add(l["incident_b"] if l["incident_a"] == keep["id"] else l["incident_a"])

    orphans = []
    for l in links:
        other = l["incident_b"] if l["incident_a"] == drop["id"] else l["incident_a"]
        if other != keep["id"] and other not in keep_partners:
            orphans.append(other)
    print(f"  links          {len(links)} on the dropped row -> deleted "
          f"({len(orphans)} would be lost to the survivor)")
    for o in orphans:
        print(f"          !! {o} links to the dropped row only — re-point by hand")

    if not apply:
        return {"fixed": 0, "failed": 0, "added_sources": len(added_urls),
                "orphan_links": len(orphans)}

    # Survivor first: if this fails, nothing else has moved and the archive
    # still holds both rows rather than neither.
    res = supabase.table("incidents").update(patch).eq("id", keep["id"]).execute()
    if not res.data:
        print("        !! survivor update returned no rows — nothing else applied")
        return {"fixed": 0, "failed": 1}

    for l in links:
        supabase.table("incident_links").delete().eq("id", l["id"]).execute()

    res = (supabase.table("incidents")
           .update({"is_published": False, "published_at": None})
           .eq("id", drop["id"]).execute())
    if not res.data:
        print("        !! unpublish returned no rows — BOTH ROWS ARE STILL LIVE")
        return {"fixed": 0, "failed": 1}

    print("  merged.")
    return {"fixed": 1, "failed": 0, "added_sources": len(added_urls),
            "orphan_links": len(orphans)}


# Ordered: url_date must run before merge, which rewrites the same timeline.
STEPS = {
    "published_at": ("1. missing published_at on live rows", step_published_at),
    "title":        ("2. truncated McDonald's title",        step_title),
    "url_date":     ("3. fabricated Mothership URL date",    step_url_date),
    "merge":        ("4. merge duplicate incident rows",     step_merge),
}


def main() -> int:
    # This prints incident titles, and the Windows console is cp1252: one curly
    # apostrophe in a headline would otherwise kill a repair mid-run, after some
    # rows had already been written. Never let formatting decide that.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                     # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write to Supabase (default is a dry run)")
    ap.add_argument("--only", choices=list(STEPS), action="append",
                    help="run only this step (repeatable)")
    args = ap.parse_args()

    _load_env()
    if not os.getenv("SUPABASE_SECRET_KEY"):
        print("SUPABASE_SECRET_KEY is not set and no repo-root .env was found.")
        return 2

    chosen = args.only or list(STEPS)
    mode = "APPLY - WRITING TO THE LIVE DATABASE" if args.apply else "DRY RUN - no writes"
    print(f"\n{'=' * 68}\n  repair_incident_records - {mode}\n{'=' * 68}")

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
