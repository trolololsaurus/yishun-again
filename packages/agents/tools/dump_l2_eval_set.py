"""
Build the reproducible Stage-2 eval fixture (programme step 3.1).

Writes packages/agents/fixtures/l2_eval_set.json — a frozen set of real
write_stage2 inputs so eval runs are comparable across days and models. Run once;
tools/eval_l2_write.py reads the file, never the live queue.

Two halves, labelled:

  single  ~15 real single-source inputs, lifted straight out of
          war_room_queue.raw_content.

  multi   ~15 inputs with 3+ non-signal sources, assembled with
          clustering.build_cluster_stage2_input so they carry the real clustered
          shape (source_articles + source_timeline), not a hand-rolled imitation.

Why the multi half has to be FETCHED rather than read:
    No war_room_queue row anywhere in the live DB carries source_articles —
    clustering has only ever written 2 rows, so per-source article text simply
    does not exist in storage. The only way to build a realistic clustered input
    is to take a published incident that already has 3+ source_urls and re-fetch
    each source. seed_backfill.fetch_article does that, with the Wayback fallback
    Cloudflare-blocked outlets need.

Run:
    ./.venv/Scripts/python.exe tools/dump_l2_eval_set.py
"""

import json
import logging
import pathlib
import sys

_AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENTS_ROOT.parents[1]
sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("dump_l2_eval_set")

from classifiers.corroboration import get_supabase_client  # noqa: E402
from ingestion import clustering  # noqa: E402
from seed_backfill import fetch_article  # noqa: E402

OUT = _AGENTS_ROOT / "fixtures" / "l2_eval_set.json"
WANT_SINGLE = 15
WANT_MULTI = 15
MIN_SOURCES = 3          # "3+ non-signal sources" per the brief
MIN_CONTENT_CHARS = 400  # a stub body makes a meaningless eval input


class _Cand:
    """Candidate stand-in with the attributes clustering reads."""
    def __init__(self, item):
        self.title = item.get("title", "")
        self.content = item.get("content", "")
        self.url = item.get("url", "")
        self.source_name = item.get("source_name", "")
        self.source_type = item.get("source_type", "msm")
        self.published_at = None


def collect_single(client) -> list[dict]:
    """Real single-source stage2 inputs, straight from the queue."""
    rows = (client.table("war_room_queue")
            .select("id,proposed_title,raw_content,created_at")
            .order("created_at", desc=True)
            .limit(400).execute()).data or []
    out = []
    for r in rows:
        rc = r.get("raw_content")
        if not isinstance(rc, dict):
            continue
        urls = rc.get("source_urls") or []
        content = (rc.get("content") or "").strip()
        if len(urls) != 1 or len(content) < MIN_CONTENT_CHARS:
            continue
        if not (rc.get("url") and rc.get("title")):
            continue
        out.append({
            "_label": "single",
            "_queue_id": r.get("id"),
            "title":             rc["title"],
            "content":           content,
            "url":               rc["url"],
            "source_name":       rc.get("source_name", "") or "unknown",
            "source_type":       rc.get("source_type", "msm"),
            "date":              rc.get("date") or "",
            "source_urls":       urls,
            "edmw_signal_count": rc.get("edmw_signal_count", 0) or 0,
        })
        if len(out) >= WANT_SINGLE:
            break
    return out


def collect_multi(client) -> list[dict]:
    """
    Published incidents with 3+ sources, re-fetched per source and assembled
    through the REAL clustered-input builder.
    """
    rows = (client.table("incidents")
            .select("id,slug,title,source_urls,incident_date,published_at")
            .eq("is_published", True)
            .order("published_at", desc=True)
            .limit(400).execute()).data or []
    pool = [r for r in rows if len(r.get("source_urls") or []) >= MIN_SOURCES]
    print(f"  {len(pool)} published incident(s) carry >= {MIN_SOURCES} source_urls")

    out = []
    for inc in pool:
        if len(out) >= WANT_MULTI:
            break
        urls = inc["source_urls"]
        print(f"  fetching {len(urls)} source(s) for {inc['slug'][:60]} ...", flush=True)
        items = []
        for u in urls:
            try:
                art = fetch_article(u)
            except Exception as exc:  # noqa: BLE001
                logger.debug("fetch error %s: %s", u[:70], exc)
                art = None
            if not art or len((art.get("content") or "")) < MIN_CONTENT_CHARS:
                continue
            items.append({
                "title":       art.get("title") or "",
                "content":     art.get("content") or "",
                "url":         u,
                "source_name": art.get("source_name") or "unknown",
                "source_type": art.get("source_type") or "msm",
                "date":        art.get("date") or inc.get("incident_date") or "",
            })
        if len(items) < MIN_SOURCES:
            print(f"    -> only {len(items)} fetched, skipping")
            continue

        cands = [_Cand(it) for it in items]
        by_id = {id(c): it for c, it in zip(cands, items)}
        stage2 = clustering.build_cluster_stage2_input(cands, lambda c: by_id[id(c)])
        stage2["_label"] = "multi"
        stage2["_incident_slug"] = inc["slug"]
        stage2["_source_count"] = len(items)
        out.append(stage2)
        print(f"    -> OK, {len(items)} sources, "
              f"{len(stage2.get('source_articles') or [])} source_articles attached")
    return out


def main() -> int:
    client = get_supabase_client()

    print("Collecting SINGLE-source inputs from war_room_queue ...")
    single = collect_single(client)
    print(f"  got {len(single)}")

    print("\nCollecting MULTI-source inputs (re-fetching each source) ...")
    multi = collect_multi(client)
    print(f"  got {len(multi)}")

    if not single and not multi:
        print("\nFATAL: no usable inputs found.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(single + multi, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nWrote {len(single) + len(multi)} input(s) -> {OUT}")
    print(f"  single-source: {len(single)}")
    print(f"  multi-source : {len(multi)}"
          f"  (sources per input: {sorted((m.get('_source_count', 0) for m in multi), reverse=True)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
