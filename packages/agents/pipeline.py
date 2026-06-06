"""
Agent pipeline orchestrator (spec §4 / v1.5).

Flow:
  scrape_all()
    → Stage 1 filter (Groq)       — rejects noise, confidence < 0.4
    → duplicate check             — skips URLs already in queue or incidents
    → Stage 2 writer (Claude)     — classifies, drafts, scores
    → Consolidation check         — detects updates to existing incidents
                                    and related-incident links
    → war_room_queue insert       — status='pending' (new) or 'update'
    → incident_links write        — for UPDATE items only; NEW items defer
                                    to the approve route after publish

Run a dry-run (no DB writes) from the command line:
    python pipeline.py --dry-run

Run a live cycle:
    python pipeline.py
"""

import logging
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

# Pause between Stage 2 calls to stay within Anthropic rate limits.
_INTER_ITEM_DELAY = 1.5   # seconds


# ── Helpers ─────────────────────────────────────────────────────────────────

def _build_queue_row(item: dict, draft: dict, edmw_signal_count: int, consolidation=None) -> dict:
    """Merge scrape item + Stage 2 draft into a war_room_queue insert dict."""
    from classifiers.consolidation import ConsolidationResult
    status                    = "pending"
    update_target_incident_id = None

    if consolidation is not None and isinstance(consolidation, ConsolidationResult):
        status                    = consolidation.queue_status
        update_target_incident_id = consolidation.matched_incident_id

    row = {
        # raw_content stores both the original scrape and Stage 2 output for
        # War Room's "View Source" pane and future training signal analysis.
        "raw_content":             {**item, **draft},
        "source_url":              item["url"],
        "source_type":             item.get("source_type", "msm"),
        "proposed_title":          draft["title"],
        "proposed_summary":        draft["summary"],
        "proposed_classification": draft["classification"],
        "proposed_severity":       draft["severity"],
        "proposed_pixel_prompt":   draft.get("pixel_art_prompt", ""),
        "proposed_slug":           draft.get("slug", ""),
        "agent_confidence":        draft["confidence"],
        "corroboration_count":     1,       # enriched by corroboration agent later
        "edmw_signal_count":       edmw_signal_count,
        "status":                  status,
    }
    if update_target_incident_id:
        row["update_target_incident_id"] = update_target_incident_id

    # Store consolidation metadata so the War Room can render banners
    # and log training signals with the agent's proposed role.
    if consolidation is not None:
        row["raw_content"]["agent_role_proposed"] = consolidation.agent_role_proposed

    if consolidation is not None and consolidation.related_incidents:
        row["raw_content"]["agent_related_incidents"] = [
            {
                "incident_id": lnk.incident_id,
                "confidence":  lnk.confidence,
                "reason":      lnk.reason,
                "link_type":   lnk.link_type,
            }
            for lnk in consolidation.related_incidents
        ]

    return row


# ── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False) -> dict:
    """
    Run one full scrape → filter → draft → queue cycle.

    Args:
        dry_run: If True, all AI calls run normally but nothing is written to
                 Supabase. Returns the full list of items that would be queued.

    Returns:
        Stats dict. In dry_run mode, stats["items"] contains per-item detail.
    """
    from scrapers import scrape_all
    from filters.stage1_filter import filter_content
    from filters.stage2_writer import write_stage2
    from classifiers.corroboration import check_duplicate, get_supabase_client
    from classifiers.consolidation import check as consolidation_check, write_incident_links

    start = time.monotonic()
    stats: dict = {
        "run_at":             datetime.now(timezone.utc).isoformat(),
        "dry_run":            dry_run,
        "scraped":            0,
        "stage1_passed":      0,
        "stage1_rejected":    0,
        "stage2_processed":   0,
        "stage2_errors":      0,
        "duplicates_skipped": 0,
        "consolidation_updates": 0,
        "pattern_alerts": 0,
        "queued":             0,
        "errors":             0,
        "duration_s":         0.0,
        "items":              [],   # populated in dry_run mode
    }

    # ── Supabase client (skip in dry_run if unconfigured) ───────────────────
    supabase = None
    if not dry_run:
        try:
            supabase = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Supabase not configured — cannot run live pipeline: %s", exc)
            stats["errors"] += 1
            return stats

    # ── Step 1: Scrape ───────────────────────────────────────────────────────
    logger.info("Pipeline — scraping all sources")
    try:
        raw_items = scrape_all()
    except Exception as exc:
        logger.error("scrape_all() failed: %s", exc)
        stats["errors"] += 1
        stats["duration_s"] = round(time.monotonic() - start, 1)
        return stats

    stats["scraped"] = len(raw_items)
    logger.info("Pipeline — scraped %d raw item(s)", stats["scraped"])

    # ── Step 2: Stage 1 filter ───────────────────────────────────────────────
    approved: list[dict] = []
    for item in raw_items:
        try:
            s1 = filter_content(item)
            if s1["passes"]:
                approved.append(item)
                stats["stage1_passed"] += 1
            else:
                stats["stage1_rejected"] += 1
                logger.debug(
                    "Stage 1 REJECT [%s] conf=%.2f — %s",
                    item.get("source_name", "?"), s1["confidence"], s1["reason"],
                )
        except Exception as exc:
            stats["errors"] += 1
            logger.error("Stage 1 error [%s]: %s", item.get("url", "?"), exc)

    logger.info(
        "Pipeline — Stage 1: %d passed / %d rejected",
        stats["stage1_passed"], stats["stage1_rejected"],
    )

    # ── Steps 3–6: Dedup → Stage 2 → insert ─────────────────────────────────
    for item in approved:
        url         = item.get("url", "")
        source_type = item.get("source_type", "msm")

        # ── Step 5: Duplicate check ──────────────────────────────────────────
        # Run even in dry_run mode so the report reflects what's actually new.
        if check_duplicate(url, client=supabase):
            stats["duplicates_skipped"] += 1
            logger.debug("Duplicate — skipping: %s", url)
            continue

        # ── Step 4: Corroboration prep ───────────────────────────────────────
        # EDMW items are signals — never added to source_urls (spec §13).
        # Their count is surfaced as "Forum buzz" in War Room only.
        if source_type == "signal":
            source_urls       = []
            edmw_signal_count = 1
        else:
            source_urls       = [url]
            edmw_signal_count = item.get("edmw_signal_count", 0)

        stage2_input = {
            **item,
            "source_urls":       source_urls,
            "edmw_signal_count": edmw_signal_count,
        }

        # ── Step 3: Stage 2 writer ───────────────────────────────────────────
        try:
            draft = write_stage2(stage2_input)
            stats["stage2_processed"] += 1
        except Exception as exc:
            stats["stage2_errors"] += 1
            logger.error("Stage 2 error [%s]: %s", url, exc)
            continue

        # ── Step 3b: Consolidation check ─────────────────────────────────────
        consolidation = None
        if not dry_run:
            try:
                consolidation = consolidation_check(draft, supabase_client=supabase)
                if consolidation.action == "update":
                    stats["consolidation_updates"] += 1
                    logger.info(
                        "Consolidation UPDATE — target incident %s (conf=%.2f)",
                        consolidation.matched_incident_id, consolidation.match_confidence,
                    )
            except Exception as exc:
                logger.warning("Consolidation check failed (non-fatal) [%s]: %s", url, exc)

        # Consolidation may flag an item as skip (near-certain duplicate that
        # cleared the URL dedup but was caught by semantic matching).
        if consolidation is not None and consolidation.action == "skip":
            logger.info(
                "Consolidation SKIP — not queuing: %s", draft.get("title", "")[:60]
            )
            stats["duplicates_skipped"] += 1
            continue

        # ── Step 6: Insert or dry-run report ────────────────────────────────
        queue_row = _build_queue_row(item, draft, edmw_signal_count, consolidation)

        if dry_run:
            stats["items"].append({
                "url":            url,
                "source_name":    item.get("source_name", "?"),
                "source_type":    source_type,
                "title":          draft["title"],
                "classification": draft["classification"],
                "severity":       draft["severity"],
                "confidence":     draft["confidence"],
                "hype_meter":     draft.get("hype_meter", 0),
                "chaos":          draft.get("chaos_contribution", 0.0),
                "slug":           draft.get("slug", ""),
                "summary_len":    len(draft.get("summary", "")),
                "tags":           draft.get("tags", []),
            })
            logger.info(
                "DRY RUN [%s] %s sev=%d conf=%.2f hype=%d | %s",
                source_type, draft["classification"].upper(),
                draft["severity"], draft["confidence"],
                draft.get("hype_meter", 0), draft["title"][:70],
            )
        else:
            try:
                insert_res = (
                    supabase.table("war_room_queue")
                    .insert(queue_row)
                    .select("id")
                    .execute()
                )
                stats["queued"] += 1
                logger.info(
                    "Queued [%s] %s sev=%d conf=%.2f hype=%d | %s",
                    source_type, draft["classification"].upper(),
                    draft["severity"], draft["confidence"],
                    draft.get("hype_meter", 0), draft["title"][:70],
                )

                new_id = (insert_res.data or [{}])[0].get("id")

                # For UPDATE items both sides of the link are already published
                # incidents → write incident_links immediately.
                # For NEW items the candidate is not yet published, so links are
                # created by the approve route; IDs are in raw_content.
                if (
                    consolidation is not None
                    and consolidation.action == "update"
                    and consolidation.matched_incident_id
                    and consolidation.related_incidents
                ):
                    try:
                        write_incident_links(
                            queue_id              = new_id or "",
                            published_incident_id = consolidation.matched_incident_id,
                            links                 = consolidation.related_incidents,
                            supabase_client       = supabase,
                        )
                        logger.debug(
                            "incident_links: %d link(s) written from %s",
                            len(consolidation.related_incidents),
                            consolidation.matched_incident_id,
                        )
                    except Exception as exc:
                        logger.warning("incident_links write failed (non-fatal): %s", exc)

                # ── Herald agent: milestone detection ────────────────────────
                if new_id:
                    try:
                        from orchestrator.herald_agent import check_milestones
                        herald = check_milestones(
                            draft           = draft,
                            queue_id        = new_id,
                            source_url      = url,
                            incident_title  = draft.get("title", ""),
                            supabase_client = supabase,
                        )
                        if herald.get("triggered"):
                            logger.info(
                                "Herald triggered: %s",
                                ", ".join(herald["triggered"]),
                            )
                    except Exception as exc:
                        logger.warning("Herald agent error (non-fatal): %s", exc)

            except Exception as exc:
                stats["errors"] += 1
                logger.error("Queue insert failed [%s]: %s", url, exc)

        time.sleep(_INTER_ITEM_DELAY)

    if dry_run:
        stats["queued"] = len(stats["items"])

    # ── Post-pipeline: pattern detection ─────────────────────────────────────
    # Runs after every live cycle so newly published incidents (approved between
    # pipeline runs) are checked for patterns promptly.
    # Non-fatal — errors here never fail the pipeline stats.
    if not dry_run and supabase is not None:
        try:
            from classifiers.pattern_detection import run as pattern_run
            pattern_stats = pattern_run(supabase_client=supabase)
            stats["pattern_alerts"] = pattern_stats.get("alerts_created", 0)
            if pattern_stats.get("alerts_created", 0) > 0:
                logger.info(
                    "Pattern detection: %d new alert(s) created",
                    pattern_stats["alerts_created"],
                )
        except Exception as exc:
            logger.warning("Pattern detection (post-pipeline) failed (non-fatal): %s", exc)

    stats["duration_s"] = round(time.monotonic() - start, 1)

    logger.info(
        "Pipeline done — scraped=%d s1_pass=%d s2_ok=%d dupes=%d "
        "updates=%d queued=%d s2_err=%d err=%d (%.1fs)",
        stats["scraped"], stats["stage1_passed"], stats["stage2_processed"],
        stats["duplicates_skipped"], stats["consolidation_updates"],
        stats["queued"], stats["stage2_errors"], stats["errors"], stats["duration_s"],
    )
    return stats


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dry_run = "--dry-run" in sys.argv

    print(f"\n{'=' * 64}")
    print(f"Yishun Again — Agent Pipeline {'DRY RUN' if dry_run else 'LIVE RUN'}")
    print(f"{'=' * 64}\n")

    result = run_pipeline(dry_run=dry_run)

    print(f"\n{'=' * 64}")
    print("PIPELINE SUMMARY")
    print(f"{'=' * 64}")
    print(f"  Run at:            {result['run_at']}")
    print(f"  Scraped:           {result['scraped']}")
    print(f"  Stage 1 passed:    {result['stage1_passed']}")
    print(f"  Stage 1 rejected:  {result['stage1_rejected']}")
    print(f"  Stage 2 processed: {result['stage2_processed']}")
    print(f"  Stage 2 errors:    {result['stage2_errors']}")
    print(f"  Duplicates:        {result['duplicates_skipped']}")
    print(f"  Updates found:     {result['consolidation_updates']}")
    print(f"  {'Would queue' if dry_run else 'Queued'}:        {result['queued']}")
    print(f"  Errors:            {result['errors']}")
    print(f"  Duration:          {result['duration_s']}s")

    if dry_run and result["items"]:
        print(f"\n{'─' * 64}")
        print(f"ITEMS THAT WOULD BE QUEUED ({len(result['items'])})")
        print(f"{'─' * 64}")
        for i, item in enumerate(result["items"], 1):
            print(f"\n  [{i}] {item['classification'].upper()}"
                  f"  sev={item['severity']}"
                  f"  conf={item['confidence']:.2f}"
                  f"  hype={item['hype_meter']}"
                  f"  chaos={item['chaos']}")
            print(f"       Source: {item['source_name']} ({item['source_type']})")
            print(f"       Title:  {item['title']}")
            print(f"       Slug:   {item['slug']}")
            print(f"       Tags:   {', '.join(item['tags'])}")
            print(f"       URL:    {item['url']}")
            print(f"       Summary: {item['summary_len']} chars")
    elif dry_run:
        print("\n  No new Yishun items found in this run.")

    print(f"\n{'=' * 64}\n")
    sys.exit(0 if result["errors"] == 0 else 1)
