"""
Delete R2 objects under pixel-art/ that no incident references.

Dry-run by default. Pass --apply to delete.

Safety model, in order of importance:
  1. The reference set is EVERY incident's pixel_art_url read with the SECRET
     key, so unpublished drafts count. Deleting an image a draft points at would
     break that draft the moment it publishes. Anon-key reads would miss drafts
     and are never used here.
  2. Only the `pixel-art/` prefix is a deletion candidate. og-default.jpg,
     placeholders and anything else are reported and LEFT ALONE -- the frontend
     fallback lives outside pixel-art/ and must survive.
  3. Objects modified within RECENT_HOURS are skipped even if unreferenced: an
     autonomous pass renders the image slightly before it writes the incident
     row, so a very fresh orphan may be a row that is about to exist.
  4. If the reference set comes back empty (a fetch bug), the script refuses to
     treat the whole bucket as orphaned and exits.
"""
import os
import sys
import json
import urllib.request
from urllib.parse import urlparse

APPLY = "--apply" in sys.argv
RECENT_HOURS = 24
PREFIX = "pixel-art/"


def _r2_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CF_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _referenced_keys() -> set[str]:
    """Every pixel_art_url across ALL incidents -> the R2 key it points at."""
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SECRET_KEY"]
    url = f"{base}/rest/v1/incidents?select=slug,pixel_art_url,is_published"
    req = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json",
    })
    rows = json.load(urllib.request.urlopen(req, timeout=90))
    refs: set[str] = set()
    for r in rows:
        u = r.get("pixel_art_url")
        if not isinstance(u, str) or not u.strip():
            continue
        path = urlparse(u).path.lstrip("/")   # drops scheme, host AND ?v= query
        if path:
            refs.add(path)
    print(f"  incidents fetched : {len(rows)}")
    print(f"  with an image     : {len(refs)}")
    return refs


def main():
    client = _r2_client()
    bucket = os.environ["CF_R2_BUCKET_NAME"]

    refs = _referenced_keys()
    if not refs:
        print("REFUSING: reference set is empty — treating nothing as orphaned.")
        raise SystemExit(2)

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RECENT_HOURS)

    paginator = client.get_paginator("list_objects_v2")
    all_objs, art_objs = 0, 0
    orphans, too_fresh, other_prefix = [], [], []
    for page in paginator.paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            all_objs += 1
            k, size, lm = o["Key"], o["Size"], o["LastModified"]
            if not k.startswith(PREFIX):
                other_prefix.append((k, size))
                continue
            art_objs += 1
            if k in refs:
                continue
            if lm > cutoff:
                too_fresh.append((k, size, lm))
            else:
                orphans.append((k, size, lm))

    def mb(b): return b / 1_048_576

    print(f"\n  bucket objects total : {all_objs}")
    print(f"  under {PREFIX}         : {art_objs}")
    print(f"  referenced (keep)    : {art_objs - len(orphans) - len(too_fresh)}")
    print(f"  orphaned (delete)    : {len(orphans)}  ({mb(sum(s for _, s, _ in orphans)):.2f} MB)")
    print(f"  too fresh (skip <{RECENT_HOURS}h): {len(too_fresh)}")
    print(f"  other prefixes (leave): {len(other_prefix)}  ({mb(sum(s for _, s in other_prefix)):.2f} MB)")

    if other_prefix:
        print("\n  -- objects OUTSIDE pixel-art/ (never touched here) --")
        for k, s in sorted(other_prefix)[:40]:
            print(f"     {k}  ({mb(s):.2f} MB)")

    if too_fresh:
        print(f"\n  -- skipped, modified within {RECENT_HOURS}h --")
        for k, s, lm in sorted(too_fresh):
            print(f"     {k}  ({mb(s):.2f} MB)  {lm:%Y-%m-%d %H:%M}Z")

    print(f"\n  -- ORPHANS ({'DELETING' if APPLY else 'dry-run, NOT deleting'}) --")
    for k, s, lm in sorted(orphans):
        print(f"     {k}  ({mb(s):.2f} MB)  {lm:%Y-%m-%d %H:%M}Z")

    if not orphans:
        print("\n  nothing to delete.")
        return
    if not APPLY:
        print(f"\n  DRY RUN. Re-run with --apply to delete {len(orphans)} object(s).")
        return

    deleted = 0
    for i in range(0, len(orphans), 900):
        batch = [{"Key": k} for k, _, _ in orphans[i:i + 900]]
        resp = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        errs = resp.get("Errors") or []
        deleted += len(batch) - len(errs)
        for e in errs:
            print(f"     ERROR {e.get('Key')}: {e.get('Message')}")
    print(f"\n  DELETED {deleted}/{len(orphans)} object(s), "
          f"reclaimed {mb(sum(s for _, s, _ in orphans)):.2f} MB.")


if __name__ == "__main__":
    main()
