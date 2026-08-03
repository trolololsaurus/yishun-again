"""
Shared war_room_queue row builder for the Historical agent (backfill_agent.py)
and the forward/ingestion pipeline (mined from pipeline.py, INGESTION_DESIGN.md
§10b step 4).

Moved here unchanged from scrapers/backfill_agent.py's private _build_queue_row
(INGESTION_DESIGN.md §10b step 2, §5.4). backfill_agent.py now calls
build_queue_row() instead of defining its own copy.
"""

import logging

from classifiers.source_allowlist import (
    check_source_urls,
    domain_of,
    is_redirect_domain,
)
from consolidation.check import ConsolidationResult

logger = logging.getLogger(__name__)


def build_queue_row(
    item: dict,
    draft: dict,
    consolidation: ConsolidationResult | None = None,
    is_update: bool = False,
    date_missing: bool = False,
    edmw_signal_count: int = 0,
    include_related_incidents: bool = False,
    is_backfill: bool = True,
) -> dict:
    """
    Build a war_room_queue row for items going to operator review.

    date_missing: caller's _parse_source_date_to_iso(item["date"]) result —
    True if the source date is missing/unparseable, in which case
    raw_content._date_fallback is set so the operator knows to set it manually.

    edmw_signal_count: forum-signal corroboration count (spec §13 "Forum buzz").
    Defaults to 0 — backfill items never carry EDMW signal counts; the forward
    ingestion pipeline passes the actual count for source_type='signal' items.

    include_related_incidents: if True and consolidation.related_incidents is
    non-empty, write raw_content.agent_related_incidents for War Room rendering.
    Defaults to False so backfill output is unchanged (consolidation_check()
    can return related_incidents for backfill candidates too, but backfill
    currently has no UI to surface them).

    is_backfill: QA H4 — whether this row came from the historical backfill agent
    (True) or the live forward-ingestion pipeline (False). The War Room buckets
    bulk-backfill actions on raw_content._backfill, so the orchestrator MUST pass
    False to avoid live drafts being mass-approved as "historical cleanup".
    """
    status = "update" if is_update else "pending"
    update_target_id = None
    agent_role = "initial"

    # Corroboration = distinct non-signal source URLs backing this draft. This was
    # hardcoded to 1, so multi-source stories reached the operator (and publish)
    # claiming a single source — which also zeroed the lightning meter downstream
    # (bolts = corroboration_count - 1). draft wins over item because Stage 2
    # returns the list it actually wrote from.
    _srcs = [u for u in (draft.get("source_urls") or item.get("source_urls") or []) if u]
    corroboration_count = max(1, len(dict.fromkeys(_srcs)))

    if consolidation is not None:
        update_target_id = consolidation.matched_incident_id
        agent_role = consolidation.agent_role_proposed

    raw_content = {
        **item,
        **draft,
        "_backfill":        is_backfill,
        "_backfill_source": item.get("source_type", "msm"),
        **({"_date_fallback": True} if date_missing else {}),
    }
    # The full text of every corroborating report is Stage 2 *input*, not queue
    # state — persisting it would add ~12KB of duplicated article text to every
    # row. The sources themselves are still recorded in source_urls and
    # source_timeline.
    raw_content.pop("source_articles", None)

    # Guardrail #2 + allowlist. A signal URL (EDMW/HWZ) can never be a quoted
    # source, so it is removed outright. A URL from a domain outside the
    # operator-approved `sources` table is KEPT — stripping it could take an
    # incident's last source and break guardrail #1 — but recorded so the War
    # Room can surface it and the operator can approve the domain or re-source.
    allow = check_source_urls(raw_content.get("source_urls") or [])
    if raw_content.get("source_urls") is not None:
        raw_content["source_urls"] = allow["kept"]
    if allow["dropped_signal"] or allow["dropped_redirect"] or allow["unapproved"]:
        raw_content["_source_allowlist"] = {
            "dropped_signal":   allow["dropped_signal"],
            "dropped_redirect": allow["dropped_redirect"],
            "unapproved":       allow["unapproved"],
        }

    # Every kept source URL should carry the date its article was published —
    # that is the date the public incident page prints beside each citation.
    #
    # clustering.build_cluster_stage2_input only emits a source_timeline when a
    # cluster holds MORE THAN ONE article, so a single-source story reached
    # publication with an empty timeline and rendered its one citation undated.
    # Audited 2026-08-03: 163 undated source links across 163 published
    # incidents, most of them "1/1". Backfilling history does not fix this —
    # without the block below, the next single-source incident is undated again.
    #
    # The dates are the candidates' OWN published_at values (Candidate's
    # contract: "parsed from the source's own date field, never inferred from
    # now"), so this records what the pipeline already knew instead of deriving
    # anything. Three properties worth keeping:
    #   - it only ever ADDS. An existing entry — with its operator/pipeline
    #     `role` and headline — is never rewritten.
    #   - it iterates `allow["kept"]`, which check_source_urls has already
    #     stripped, so a signal or redirect URL can never enter the timeline
    #     (guardrail #2).
    #   - a dateless candidate contributes NO entry. An undated link is honest;
    #     a fabricated date beside a citation is not.
    # Added entries carry no `role`: collapseTimelineByDate() ranks a missing
    # role lowest, so a synthesised entry can never outrank a real verdict or
    # initial label sharing its date.
    _existing = raw_content.get("source_timeline") or []
    _already = {
        e.get("source_url") for e in _existing
        if isinstance(e, dict) and e.get("date")
    }
    _known: dict[str, dict] = {}
    for _art in (draft.get("source_articles") or []):
        if _art.get("url") and _art.get("date"):
            _known[_art["url"]] = _art
    if item.get("url") and item.get("date"):
        _known.setdefault(item["url"], item)

    _additions = [
        {
            "date":        str(_known[u].get("date") or ""),
            "source_url":  u,
            "source_name": _known[u].get("source_name", ""),
            "headline":    _known[u].get("title", ""),
        }
        for u in allow["kept"]
        if u in _known and u not in _already
    ]
    if _additions:
        raw_content["source_timeline"] = sorted(
            list(_existing) + _additions,
            key=lambda e: str(e.get("date") or "9999-99-99"),
        )

    # `source_url` is the row's headline link — what the War Room renders and
    # what dedup.is_duplicate matches on — and it used to be copied from the
    # candidate with no check at all. That is how two news.google.com wrappers
    # became the visible source on 2026-08-01 rows: unmatched by dedupe (so
    # they proposed updates to a story we already held) and unusable as a
    # citation. Prefer the first surviving real source; fall back to the raw
    # value only so the row is never malformed, and flag it either way.
    source_url = item["url"]
    if is_redirect_domain(source_url):
        replacement = next((u for u in allow["kept"] if not is_redirect_domain(u)), None)
        logger.warning(
            "queue_row: candidate source_url is a redirector (%s) — %s",
            domain_of(source_url),
            f"substituting {domain_of(replacement)}" if replacement
            else "no publisher URL available, flagging for the operator",
        )
        raw_content.setdefault("_source_allowlist", {})["redirect_source_url"] = source_url
        if replacement:
            source_url = replacement

    row = {
        "raw_content": raw_content,
        "source_url":              source_url,
        "source_type":             item.get("source_type", "msm"),
        "proposed_title":          draft["title"],
        "proposed_summary":        draft["summary"],
        "proposed_classification": draft["classification"],
        "proposed_severity":       draft["severity"],
        "proposed_pixel_prompt":   draft.get("pixel_art_prompt", ""),
        "proposed_slug":           draft.get("slug", ""),
        "agent_confidence":        draft["confidence"],
        "corroboration_count":     corroboration_count,
        "edmw_signal_count":       edmw_signal_count,
        "status":                  status,
    }
    row["raw_content"]["agent_role_proposed"] = agent_role

    if include_related_incidents and consolidation is not None and consolidation.related_incidents:
        row["raw_content"]["agent_related_incidents"] = [
            {
                "incident_id": lnk.incident_id,
                "confidence":  lnk.confidence,
                "reason":      lnk.reason,
                "link_type":   lnk.link_type,
            }
            for lnk in consolidation.related_incidents
        ]

    if update_target_id:
        row["update_target_incident_id"] = update_target_id

    return row
