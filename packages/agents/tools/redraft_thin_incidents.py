"""
Re-draft published incidents that were written from a feed standfirst.

WHY THIS EXISTS
---------------
Straits Times, CNA and Yahoo put only a STANDFIRST in their RSS <description>
(67-175 chars; Yahoo, none at all). Stage 2 writes the incident summary from
that text, so every incident sourced from those three was drafted from a
headline and one sentence. `scrapers.enrich_thin_content` fixes it going
forward; this repairs what already published.

Measured 2026-08-05 over 166 published incidents:
    39  sourced ONLY from ST/CNA/Yahoo   <- the targets
    23  mixed with a full-text source
     0  of the 39 were operator-edited

WHAT IT WILL NOT TOUCH
----------------------
- OPERATOR-EDITED ROWS. An `edit_approve` training signal means a human wrote
  or corrected that text, and no automated pass may overwrite it. Checked per
  row, not assumed from the count above.
- THE SLUG. A permanent public URL; re-stamping it 404s every existing link.
- classification, severity, tags. Re-classifying retroactively would move the
  Chaos Index and could flip guardrail #5 image suppression. The complaint is
  thin PROSE; that is all this changes.
- incident_date, source_urls, corroboration_count.
- Titles, unless --titles is passed. A published headline is what readers and
  search engines already have.

REFUSES TO MAKE THINGS WORSE
----------------------------
A redraft is written only when it is BOTH materially longer than what is
published AND passes Stage 2's groundedness check. A summary that invents
specifics is worse than a short one, so a flagged draft is reported and
discarded.

USAGE
-----
    ./.venv/Scripts/python.exe tools/redraft_thin_incidents.py            # DRY RUN
    ./.venv/Scripts/python.exe tools/redraft_thin_incidents.py --limit 3  # sample
    ./.venv/Scripts/python.exe tools/redraft_thin_incidents.py --apply
    ./.venv/Scripts/python.exe tools/redraft_thin_incidents.py --apply --titles
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifiers.corroboration import get_supabase_client        # noqa: E402
from classifiers.source_allowlist import domain_of               # noqa: E402
from scrapers import article_body                                # noqa: E402
from filters.stage2_writer import write_stage2                   # noqa: E402

# The three publishers whose RSS carries a standfirst rather than an article.
THIN_FEED_DOMAINS = {
    "straitstimes.com", "channelnewsasia.com",
    "yahoo.com", "news.yahoo.com", "sg.news.yahoo.com",
}

# A redraft must beat the published summary by this much to be worth changing a
# live page over. Small wobbles are churn, not improvement.
MIN_GAIN_CHARS = 200


def _thin_only(urls) -> bool:
    hosts = [domain_of(u) for u in (urls or [])]
    return bool(hosts) and all(
        any(h == d or h.endswith("." + d) for d in THIN_FEED_DOMAINS) for h in hosts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--titles", action="store_true",
                    help="also rewrite the headline (published pages already carry it)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), ".env"))
    db = get_supabase_client()

    signals = (db.table("training_signals").select("incident_id,action").execute()).data or []
    operator_edited = {s["incident_id"] for s in signals
                       if s.get("action") == "edit_approve" and s.get("incident_id")}

    rows = (db.table("incidents")
            .select("id,slug,title,summary,source_urls,incident_date,"
                    "classification,severity,edmw_signal_count")
            .eq("is_published", True).execute()).data or []

    targets = [r for r in rows
               if _thin_only(r.get("source_urls")) and r["id"] not in operator_edited]
    skipped_edited = [r for r in rows
                      if _thin_only(r.get("source_urls")) and r["id"] in operator_edited]

    if args.limit:
        targets = targets[:args.limit]

    print(f"{len(rows)} published incidents")
    print(f"  {len(targets)} sourced only from ST/CNA/Yahoo and not operator-edited")
    print(f"  {len(skipped_edited)} skipped — operator-edited, never overwritten\n")

    updates, ungrounded, no_gain, no_body = [], [], [], []

    for i, r in enumerate(targets, 1):
        urls = r.get("source_urls") or []
        body = ""
        for u in urls:
            body = article_body(u)
            if len(body) >= 600:
                break
        if len(body) < 600:
            no_body.append(r["slug"])
            print(f"[{i}/{len(targets)}] ?  no article body recovered   {r['slug'][:56]}")
            continue

        try:
            draft = write_stage2({
                "title": r["title"], "content": body, "url": urls[0],
                "source_name": domain_of(urls[0]), "source_urls": urls,
                "date": r.get("incident_date"),
                "edmw_signal_count": r.get("edmw_signal_count") or 0,
            })
        except Exception as exc:
            print(f"[{i}/{len(targets)}] !  stage2 failed: {str(exc)[:70]}  {r['slug'][:44]}")
            continue

        grounding = draft.get("_groundedness") or {}
        old, new = (r.get("summary") or ""), draft.get("summary", "")

        # A draft that invents specifics is worse than a short one.
        if grounding.get("flagged"):
            ungrounded.append(r["slug"])
            print(f"[{i}/{len(targets)}] x  UNGROUNDED, discarded          {r['slug'][:56]}")
            continue
        if len(new) - len(old) < MIN_GAIN_CHARS:
            no_gain.append(r["slug"])
            print(f"[{i}/{len(targets)}] =  no material gain ({len(old)}->{len(new)})  {r['slug'][:44]}")
            continue

        print(f"[{i}/{len(targets)}] +  {len(old)} -> {len(new)} chars   {r['slug'][:52]}")
        updates.append({"id": r["id"], "slug": r["slug"],
                        "old_summary": old, "new_summary": new,
                        "old_title": r["title"], "new_title": draft.get("title")})

    print("\n=== summary ===")
    print(f"  would rewrite            : {len(updates)}")
    print(f"  discarded as ungrounded  : {len(ungrounded)}")
    print(f"  no material gain         : {len(no_gain)}")
    print(f"  no article body          : {len(no_body)}")
    print(f"  operator-edited, skipped : {len(skipped_edited)}")
    print(f"  titles: {'REWRITTEN' if args.titles else 'left as published'}")

    if not args.apply:
        json.dump(updates, open("redraft_proposed.json", "w", encoding="utf-8"), indent=1)
        print("\n  DRY RUN — nothing written. Proposal: redraft_proposed.json")
        return 0

    json.dump(updates, open("redraft_backup.json", "w", encoding="utf-8"), indent=1)
    for u in updates:
        patch = {"summary": u["new_summary"]}
        if args.titles and u["new_title"]:
            patch["title"] = u["new_title"]
        db.table("incidents").update(patch).eq("id", u["id"]).execute()
    print(f"\n  APPLIED to {len(updates)} incident(s). Backup: redraft_backup.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
