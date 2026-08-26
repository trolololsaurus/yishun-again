"""
Canonicalise the dedup keys already in the database.

WHY
---
build_queue_row now stores canonical_url() forms as the dedup keys, but the
~160 incidents and the queue rows written before that still hold raw URLs with
tracking params (`?ref=home-editors-picks`). dedup.is_duplicate canonicalises
the incoming probe, so a raw stored key with a DIFFERENT tracking param is still
missed — which is how yishun-bicycle-basket-passenger-aug-2026 ended up citing
one Stomp article twice under two ?ref= values.

This rewrites incidents.source_urls and war_room_queue.source_url to their
canonical forms, de-duplicating the arrays (keeping the first spelling's
position) and correcting corroboration_count when the collapse drops a
duplicate. Idempotent: canonical_url is a fixed point, so a second run is a
no-op.

    ./.venv/Scripts/python.exe tools/canonicalize_source_urls.py            # dry run
    ./.venv/Scripts/python.exe tools/canonicalize_source_urls.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def _load_env():
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"):
        return
    p = os.path.join(_ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _canon_unique(urls):
    """Canonicalise, drop later duplicates, keep first-seen order."""
    from classifiers.source_allowlist import canonical_url
    out, seen = [], set()
    for u in urls or []:
        c = canonical_url(u) if u else u
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    _load_env()
    from classifiers.corroboration import get_supabase_client
    from classifiers.source_allowlist import canonical_url
    sb = get_supabase_client()

    print(f"{'APPLY' if args.apply else 'DRY RUN'}\n")

    # -- incidents.source_urls --
    inc = sb.table("incidents").select("id,slug,source_urls,corroboration_count").execute().data or []
    inc_changed = inc_collapsed = 0
    for r in inc:
        new = _canon_unique(r.get("source_urls"))
        if new == (r.get("source_urls") or []):
            continue
        inc_changed += 1
        patch = {"source_urls": new}
        # Only lower a count when the collapse actually removed a duplicate; the
        # public "Corroborated by N sources" line counts this array.
        if len(new) < len(r.get("source_urls") or []):
            inc_collapsed += 1
            patch["corroboration_count"] = max(1, len(new))
            print(f"  COLLAPSE {len(r['source_urls'])}->{len(new)}  cc->{patch['corroboration_count']}  {r['slug']}")
        else:
            print(f"  canon    {r['slug']}")
        if args.apply:
            sb.table("incidents").update(patch).eq("id", r["id"]).execute()

    # -- war_room_queue.source_url (the singular queue dedup key) --
    q = sb.table("war_room_queue").select("id,source_url").execute().data or []
    q_changed = 0
    for r in q:
        raw = r.get("source_url")
        c = canonical_url(raw) if raw else raw
        if c == raw:
            continue
        q_changed += 1
        if args.apply:
            sb.table("war_room_queue").update({"source_url": c}).eq("id", r["id"]).execute()

    print(f"\nincidents: {inc_changed} rewritten ({inc_collapsed} had a duplicate collapsed)")
    print(f"queue:     {q_changed} source_url rewritten")
    if not args.apply:
        print("\nDRY RUN — re-run with --apply.")


if __name__ == "__main__":
    main()
