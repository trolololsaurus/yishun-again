"""
Seed-driven historical backfill — ingest a curated list of MANUAL source URLs
through the standard Stage 1 -> Stage 2 -> intra-batch dedup -> consolidation ->
tier pipeline (scrapers.backfill_agent.process_candidates).

Unlike run_backfill() (which DISCOVERS candidates via Google News RSS / Wikipedia
and under-recalls historical years), this seeds the pipeline with operator-curated
URLs, so recall is guaranteed for the backlog. Built for the June-2026 gap audit
re-backfill (docs/QA_BACKLOG.md companion).

Per-URL fetch chain:  direct GET  ->  Wayback snapshot  ->  flag (skipped).
  (Mothership + TheOnlineCitizen are Cloudflare-blocked; Wayback recovers older ones.)

Hybrid routing (operator choice, gap-audit):
  route="queue"  -> ALWAYS War Room queue  (named crime / death / sexual / drugs / etc.)
                    achieved by raising TIER_AUTO_PUBLISH so nothing auto-publishes.
  route="auto"   -> standard tiers (auto-publish >= 0.70, else queue) for colour /
                    accidents / fire / nuisance / non-named.
  route="consolidate" -> treated as "auto"; consolidation detects the UPDATE and
                    routes it to the queue automatically.

Manifest JSON: list of {story, urls:[...], category, value, route, source_type?}.

Usage (run from packages/agents/):
  .venv/Scripts/python.exe seed_backfill.py --manifest seeds.json --dry-run
  .venv/Scripts/python.exe seed_backfill.py --manifest seeds.json          # live
  .venv/Scripts/python.exe seed_backfill.py --manifest seeds.json --route queue --dry-run
"""
import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

import scrapers.backfill_agent as bf
from scrapers.groq_budget import GroqBudget

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_PUB_PATTERNS = [
    r'property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)',
    r'<time[^>]+datetime=["\']([^"\']+)',
]
_REFERENCE_HOSTS = ("wikipedia.org", "grokipedia.com", "wiki.sg", "fandom.com")
_SIGNAL_HOSTS    = ("reddit.com", "hardwarezone.com.sg")


def _host(u: str) -> str:
    try:
        return urlparse(u).netloc.replace("www.", "")
    except Exception:
        return u


def _source_type(u: str) -> str:
    h = _host(u)
    if any(r in h for r in _REFERENCE_HOSTS):
        return "reference"
    if any(s in h for s in _SIGNAL_HOSTS):
        return "signal"
    return "msm"


def _extract(html: str, fallback_url: str) -> tuple[str, str, str | None]:
    """Return (title, body_text, iso_date|None) from article HTML."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    title = re.sub(r"\s*[\|\-–]\s*(The Straits Times|CNA|Mothership|AsiaOne|"
                   r"The Online Citizen|MustShareNews|TODAY).*$", "", title).strip()
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    body = " ".join(p for p in paras if len(p) > 30)[:3000]

    date = None
    for pat in _PUB_PATTERNS:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            dm = re.match(r"(\d{4}-\d{2}-\d{2})", m.group(1).strip())
            if dm:
                date = dm.group(1)
                break
    if not date:
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", fallback_url)
        if m:
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return title, body, date


def fetch_article(url: str) -> dict | None:
    """
    Fetch one article -> {title, content, url, source_name, source_type, date}.
    Chain: direct GET (200) -> Wayback snapshot -> None (flagged).
    On Wayback, `url` stays the canonical publisher URL (not the archive URL),
    so source_urls remain clean; date comes from the snapshot's article meta.
    """
    title = body = date = None
    used = "direct"
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15.0,
                      headers={"User-Agent": _UA})
        if r.status_code == 200 and len(r.text) > 2000:
            title, body, date = _extract(r.text, url)
    except Exception as exc:
        logger.debug("Direct fetch error %s: %s", url[:70], exc)

    if not body:
        snap = bf.get_wayback_url(url)
        if snap:
            try:
                r = httpx.get(snap, follow_redirects=True, timeout=25.0,
                              headers={"User-Agent": _UA})
                if r.status_code == 200:
                    title, body, date = _extract(r.text, url)
                    used = "wayback"
            except Exception as exc:
                logger.debug("Wayback fetch error %s: %s", snap[:70], exc)

    if not body or len(body) < 120:
        logger.warning("FETCH FAILED (no content): %s", url)
        return None

    logger.info("fetched [%s] %s | %s | %s", used, _host(url), date, (title or "")[:70])
    return {
        "title":       title or url.rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
        "content":     body,
        "url":         url,
        "source_name": _host(url),
        "source_type": _source_type(url),
        "date":        date or "",
        "_fetch_via":  used,
    }


_ROLE_BY_KEYWORD = [
    ("verdict",   ("jailed", "sentenced", "sentencing", "convicted", "pleads guilty",
                   "found guilty", "gets jail", "coroner", "verdict", "appeal")),
    ("update",    ("charged", "charge", "arrest", "remanded", "trial", "court")),
]


def _role_for(title: str) -> str:
    t = (title or "").lower()
    for role, kws in _ROLE_BY_KEYWORD:
        if any(k in t for k in kws):
            return role
    return "initial"


def build_candidates(manifest: list[dict], route_filter: str | None) -> tuple[list, list]:
    """
    Fetch each STORY's sources and emit exactly ONE candidate per story
    (content from the first fetchable source; all msm URLs aggregated into
    source_urls; a source_timeline built across sources). One candidate per
    story => one incident per story, so multi-source stories can never split
    into duplicate incidents the way intra-batch dedup's 7-day window allows.
    Returns (candidates, flagged).
    """
    candidates: list[dict] = []
    flagged: list[dict] = []
    for entry in manifest:
        if route_filter and entry.get("route") != route_filter:
            continue
        story_urls = entry.get("urls", [])
        fetched = []
        for u in story_urls:
            got = fetch_article(u)
            time.sleep(0.5)
            if got:
                fetched.append(got)
        if not fetched:
            flagged.append({"story": entry.get("story", ""), "urls": story_urls,
                            "reason": "no fetchable source (direct + wayback both failed)"})
            continue

        # Primary = earliest-dated fetched source (the initial report); fall back to first.
        fetched.sort(key=lambda f: f.get("date") or "9999-99-99")
        primary = fetched[0]
        # source_urls: all non-signal sources (reference allowed; signal excluded per guardrail #2)
        src_urls = [f["url"] for f in fetched if f["source_type"] != "signal"]
        timeline = [{
            "date": f.get("date") or "", "source_url": f["url"],
            "source_name": f["source_name"], "headline": f.get("title", ""),
            "source_role": _role_for(f.get("title", "")),
        } for f in fetched if f["source_type"] != "signal"]

        cand = dict(primary)
        cand["source_urls"]     = src_urls or [primary["url"]]
        cand["source_timeline"] = timeline
        cand["_story"]    = entry.get("story", "")
        cand["_route"]    = entry.get("route", "auto")
        cand["_category"] = entry.get("category", "")
        cand["_n_sources"] = len(src_urls)
        if entry.get("source_type"):
            cand["source_type"] = entry["source_type"]
        candidates.append(cand)
    return candidates, flagged


def run(manifest_path: str, dry_run: bool, route_filter: str | None) -> dict:
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    logger.info("Seed backfill: %d manifest entries | route_filter=%s | dry_run=%s",
                len(manifest), route_filter, dry_run)

    candidates, flagged = build_candidates(manifest, route_filter)
    logger.info("Built %d candidates from manifest (%d stories flagged unfetchable)",
                len(candidates), len(flagged))

    # Hybrid routing: if EVERY candidate this run is route="queue", raise the
    # auto-publish threshold so nothing auto-publishes (all go to War Room).
    routes = {c.get("_route") for c in candidates}
    # queue + consolidate both force-queue: nothing auto-publishes. (Consolidation
    # still runs and attaches genuine matches as UPDATEs; an unmatched "partial"
    # then lands in the queue for review instead of publishing a near-duplicate.)
    force_queue = routes and routes <= {"queue", "consolidate"}
    saved_threshold = bf.TIER_AUTO_PUBLISH
    if force_queue:
        bf.TIER_AUTO_PUBLISH = 1.01
        logger.info("Force-queue run: TIER_AUTO_PUBLISH raised to 1.01 (nothing auto-publishes)")

    supabase = None
    if not dry_run:
        from classifiers.corroboration import get_supabase_client
        supabase = get_supabase_client()

    stats = {
        "run_at": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run,
        "scraped": len(candidates), "stage1_passed": 0, "stage1_rejected": 0,
        "stage2_processed": 0, "stage2_errors": 0, "duplicates_skipped": 0,
        "prefiltered": 0, "batch_merges": 0, "auto_published": 0,
        "queued_for_review": 0, "updates_found": 0, "rejected": 0, "errors": 0,
        "capped": False, "items": [], "error_details": [],
    }
    budget = GroqBudget()

    try:
        bf.process_candidates(candidates, cap=len(candidates) or 1,
                              dry_run=dry_run, supabase=supabase,
                              stats=stats, budget=budget)
    finally:
        bf.TIER_AUTO_PUBLISH = saved_threshold

    stats["_flagged_unfetchable"] = flagged
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--route", choices=["queue", "auto", "consolidate"], default=None,
                    help="Only process manifest entries with this route.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    stats = run(args.manifest, dry_run=args.dry_run, route_filter=args.route)

    print("\n===== SEED BACKFILL SUMMARY =====")
    for k in ("dry_run", "scraped", "stage1_passed", "stage1_rejected",
              "stage2_processed", "stage2_errors", "batch_merges",
              "auto_published", "queued_for_review", "updates_found",
              "rejected", "errors"):
        print(f"  {k:20} {stats.get(k)}")
    if stats.get("items"):
        print("\n  --- per-item (dry-run) ---")
        for it in stats["items"]:
            print(f"   [{it['tier']:22}] {it['classification']:8} sev={it['severity']} "
                  f"conf={it['confidence']:.2f} | {it['title'][:66]}")
    if stats.get("_flagged_unfetchable"):
        print(f"\n  --- FLAGGED unfetchable ({len(stats['_flagged_unfetchable'])}) ---")
        for f in stats["_flagged_unfetchable"]:
            print(f"   {f['story'][:70]}  {f['urls']}")


if __name__ == "__main__":
    main()
