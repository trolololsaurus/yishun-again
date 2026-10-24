"""
Autonomous publishing (req #3) + review-queue notification (req #4).

Runs immediately after the ingestion pass. Two jobs:

  1. Every `pending` queue row that clears the confidence threshold and the
     source, date, factual-integrity and duplicate checks can publish without a
     human — with the decision recorded as a training signal.
  2. Held rows and successful autonomous actions are reported from a refreshed
     War Room queue; one Telegram report is maintained per SGT pass window.

## Why this is a faithful port and not a second implementation

Publishing already exists, in TypeScript, at
`apps/war-room/app/api/queue/[id]/approve/route.ts`. This module deliberately
mirrors that route field-for-field — same incident payload, same slug source,
same geocode-is-non-fatal rule, same queue-update-governs-idempotency rule
(QA H2). If you change one, change the other. The alternative (calling the War
Room HTTP route from here) was rejected: it sits behind Cloudflare Access, so
the agent would need a service token and a network hop to do a database write
it can already do directly.

## The gates, and which are negotiable

The operator has chosen a LITERAL 0.95 threshold across all content classes —
crime and named individuals included. That is an editorial decision and it is
implemented as asked: there is no classification carve-out in this module.

What remains are the four HARDCODED LEGAL GUARDRAILS (CLAUDE.md, "Never
Remove") plus the data-integrity preconditions the human approve route ALREADY
enforces with a 422. These are not extra restrictions invented here — they are
the existing publish contract:

  * >= 1 source URL                    guardrail #1 (also a DB CHECK)
  * no `type='signal'` URL in sources  guardrail #2 (EDMW is never quoted)
  * political content                  guardrail #4 — forced to confidence 0 by
                                       Stage 2, so it can never reach 0.95;
                                       re-asserted here as defence in depth
  * a real incident_date               QA H3 — never stamp "today"
  * source domain is operator-approved a URL from an unknown domain is not a
                                       *verifiable* source, which is guardrail
                                       #1's actual point. Routed to review.

A row failing any of these is not rejected — it is left `pending` for the
operator, exactly as before. The failure mode of this agent is "a human looks
at it", never "it disappears".

`update` rows (merges into an existing story) take a different write path
(append to `source_urls`/`source_timeline`, recompute dates) whose failure mode
is corrupting an already-published incident rather than adding a new one. The
operator's policy is to auto-apply updates when both the draft confidence
(`agent_confidence`) and consolidation same-event confidence (`_match_confidence`)
are at least 0.90 and the appended source survives the allowlist. The pre-merge
state is snapshotted so the operator can undo it (revert-update). See
check_update_eligibility / _apply_merge and docs/AUTONOMY.md.
"""

import hashlib
import logging
import os
from datetime import date, datetime, timezone

from classifiers.source_allowlist import is_signal_source
from ops.activity import AgentRun, _client, agent_enabled
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "auto_publish"

# Operator-set. Env override so the bar can be raised the morning after a bad
# night without a redeploy.
def _threshold() -> float:
    try:
        return float(os.getenv("AUTO_PUBLISH_CONFIDENCE", "0.95"))
    except ValueError:
        return 0.95


# A safety valve, not a business rule: if a bug ever floods the queue with
# high-confidence rows, cap the blast radius at one pass's worth. The excess
# stays pending and a human sees it.
MAX_AUTO_PUBLISH_PER_RUN = int(os.getenv("AUTO_PUBLISH_MAX_PER_RUN", "25"))

# The same-pass duplicate guard is deliberately conservative and only holds a
# possible match for review. Name its policy values so they are visible during
# code review rather than buried as unexplained literals.
DUPLICATE_GUARD_ARCHIVE_LIMIT = 2000
DUPLICATE_GUARD_DATE_WINDOW_DAYS = 2
DUPLICATE_GUARD_MIN_KEYWORD_OVERLAP = 3
DUPLICATE_GUARD_MAX_LOCALITY_TOKENS = 2

# ── Autonomous merge (auto-apply an UPDATE row) ──────────────────────────────
# Applying a merge mutates an ALREADY-PUBLISHED incident, so the existing undo
# snapshot and source-integrity checks remain mandatory. Production explicitly
# enables this after migration 018; unconfigured environments default off.
# Both independent confidence signals must be >=0.90:
#   AUTO_MERGE_CONFIDENCE       — the Stage 2 draft confidence (write quality)
#   AUTO_MERGE_MATCH_CONFIDENCE — the consolidation same-event confidence (the
#                                 wrong-merge axis; the strict one). Held if the
#                                 row predates _match_confidence being persisted.
# The undo net (revert-update + _undo_snapshot) requires migration 018.
AUTO_MERGE_ENABLED = os.getenv("AUTO_MERGE_ENABLED", "false").strip().lower() in ("1", "true", "on", "yes")


def _merge_threshold() -> float:
    try:
        return float(os.getenv("AUTO_MERGE_CONFIDENCE", "0.90"))
    except ValueError:
        return 0.90


def _merge_match_threshold() -> float:
    try:
        return float(os.getenv("AUTO_MERGE_MATCH_CONFIDENCE", "0.90"))
    except ValueError:
        return 0.90


MAX_AUTO_MERGE_PER_RUN = int(os.getenv("AUTO_MERGE_MAX_PER_RUN", "25"))

# Autonomous SUMMARY enrichment: during an auto-merge, also refresh the incident's
# prose with the new development (the ingestion-time enriched summary from
# consolidation/enrich.py). SEPARATE from AUTO_MERGE_ENABLED and off by default,
# because it ships model-written prose to a live incident with no human glance —
# so it applies ONLY when the enrichment passed groundedness. The manual War Room
# path pre-fills the same enriched summary for operator review regardless of this
# flag; this only governs the unattended apply. revert-update restores the prior
# summary either way (the merge snapshot captures it). See docs/AUTONOMY.md §2b.
AUTO_ENRICH_SUMMARY = os.getenv("AUTO_ENRICH_SUMMARY", "false").strip().lower() in ("1", "true", "on", "yes")

# Art generation is opt-in per environment. It is the only step here that spends
# money per row, so it defaults OFF and an unconfigured deployment publishes
# exactly as it does today rather than logging a failure per incident.
#
# The original reason for the default — "CF_R2_* + GEMINI_API_KEY + IMAGE_MODEL
# are not set on Cloud Run yet" — is no longer true: verified 2026-08-04, all of
# them are set on the service. What remains true is the cost, so the flag stays
# a deliberate operator decision rather than something that flips itself once
# the config appears.
#
# Scope: this gate is read ONLY here, on the autonomous publish path. The HTTP
# endpoints (`/art/generate`, `/art/rectify`) call art.generate_image directly
# and are NOT gated by it, so the operator's approve and rectify clicks render
# regardless. With this false, an auto-published incident lands with
# image_status='pending' — which reads identically to "the backend was never
# reachable", the state the Cloud Run 403 produced. Check the flag before
# concluding the pipeline is broken.
ART_ENABLED = os.getenv("ART_GENERATION_ENABLED", "false").strip().lower() in ("1", "true", "on", "yes")

_POLITICAL_MARKER = "[POLITICAL CONTENT DETECTED"

_HOLD_REASON_LABELS = {
    "allowlist_check_failed": "source verification unavailable",
    "below_threshold": "below auto-publish confidence threshold",
    "casualty_mismatch": "casualty details need review",
    "date_fallback": "date was inferred and needs review",
    "duplicate_check_unavailable": "duplicate check unavailable",
    "match_below_threshold": "same-event confidence below threshold",
    "manual_review": "manual review required",
    "merge_failed": "automatic merge failed",
    "missing_summary": "summary is empty",
    "missing_title": "title is empty",
    "no_confidence": "confidence is missing",
    "no_match_confidence": "same-event confidence is missing",
    "no_real_date": "article date is missing or invalid",
    "no_source_url": "source URL is missing",
    "no_approved_source_after_filter": "no quotable source remains",
    "no_update_target": "update target is missing",
    "not_processed_this_pass": "not processed this pass",
    "not_update": "not an update row",
    "oversized_cluster_unproven": "large merge needs a human track record",
    "per_run_cap_hit": "per-pass auto-publish limit reached",
    "political_marker": "political-content safeguard",
    "possible_published_duplicate": "possible duplicate of a published incident",
    "publish_failed": "automatic publish failed",
    "source_not_verifiable": "source cannot be verified",
    "unapproved_source_domain": "source domain is not approved",
    "ungrounded_specifics": "specific details could not be verified",
}


def _human_hold_reason(reason: str) -> str:
    """Translate a stable internal gate code for Telegram and operators."""
    return _HOLD_REASON_LABELS.get(reason, str(reason).replace("_", " "))


def _possible_published_duplicate(item: dict, incidents: list[dict]) -> dict | None:
    """High-recall, independent hold check for same-date published duplicates.

    This deliberately only holds a row for a person to review. It never merges
    or rejects. The lexical check is separate from consolidation's model call,
    so an earlier missed match cannot be repeated as an unattended publish.
    """
    from consolidation.rules import (
        extract_keywords,
        extract_locality_tokens,
        keyword_overlap,
        locality_conflict,
    )

    rc = item.get("raw_content") or {}
    raw_date = rc.get("date") or rc.get("incident_date") or ""
    try:
        event_date = date.fromisoformat(str(raw_date)[:10])
    except (TypeError, ValueError):
        return None

    candidate_title = str(item.get("proposed_title") or "")
    candidate_summary = str(item.get("proposed_summary") or "")
    candidate_text = f"{candidate_title} {candidate_summary}"
    candidate_keywords = extract_keywords(candidate_text)
    candidate_locality = extract_locality_tokens(candidate_title)
    if not candidate_locality:
        candidate_locality = extract_locality_tokens(candidate_summary)
    if len(candidate_keywords) < 3:
        return None

    matches = []
    for incident in incidents:
        try:
            existing_date = date.fromisoformat(str(incident.get("incident_date") or "")[:10])
        except (TypeError, ValueError):
            continue
        if abs((event_date - existing_date).days) > DUPLICATE_GUARD_DATE_WINDOW_DAYS:
            continue
        existing_title = str(incident.get("title") or "")
        existing_summary = str(incident.get("summary") or "")
        existing_text = f"{existing_title} {existing_summary}"
        overlap = keyword_overlap(candidate_keywords, extract_keywords(existing_text))
        if overlap < DUPLICATE_GUARD_MIN_KEYWORD_OVERLAP:
            continue

        existing_locality = extract_locality_tokens(existing_title)
        if not existing_locality:
            existing_locality = extract_locality_tokens(existing_summary)
        if (len(candidate_locality) > DUPLICATE_GUARD_MAX_LOCALITY_TOKENS
                or len(existing_locality) > DUPLICATE_GUARD_MAX_LOCALITY_TOKENS):
            logger.info(
                "auto_publish: skipping locality veto for queue %s and incident %s "
                "(ambiguous locality: %d/%d tokens)",
                item.get("id"), incident.get("id"),
                len(candidate_locality), len(existing_locality),
            )
        elif locality_conflict(candidate_locality, existing_locality):
            logger.info(
                "auto_publish: lexical duplicate for queue %s and incident %s "
                "vetoed by conflicting locality",
                item.get("id"), incident.get("id"),
            )
            continue

        matches.append((overlap, incident))

    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return matches[0][1]


def _fetch_duplicate_guard_incidents(client) -> list[dict]:
    """Read the published archive once per pass; caller holds if unavailable."""
    result = (client.table("incidents")
              .select("id,title,summary,incident_date")
              .eq("is_published", True)
              .order("published_at", desc=True)
              .limit(DUPLICATE_GUARD_ARCHIVE_LIMIT).execute())
    return result.data or []


def can_record_decisions(client) -> tuple[bool, str]:
    """
    Pre-flight: can this database record an autonomous decision?

    NEVER TAKE AN ACTION YOU CANNOT LOG. Publishing is irreversible-ish (the
    incident is live on the public site); the training signal is what makes it
    auditable and what teaches the loop. If migration 011 has not been applied,
    `decided_by` does not exist and the `auto_approve` action violates the CHECK
    constraint — so every insert fails while every publish succeeds. The result
    is live incidents nobody approved and no record that an agent chose them.

    That is precisely the migration-009 failure mode (unpublish signals silently
    rejected for weeks), except here the silence hides autonomous publishing
    rather than a missing metric. So: probe first, and if the schema cannot hold
    the decision, publish nothing and shout.

    A SELECT is used rather than a trial insert — it needs no cleanup and cannot
    itself corrupt anything.
    """
    try:
        client.table("training_signals").select("decided_by").limit(1).execute()
        return True, ""
    except Exception as exc:                      # noqa: BLE001
        return False, (
            "training_signals.decided_by is missing — migration 011 has not been "
            f"applied ({exc})"
        )


# ── Oversized-merge trust (an exit condition, not a permanent gate) ─────────

# Same FORMULA as learning_monitor's source reputation, deliberately stricter
# settings. Laplace smoothing alone clears 0.70 after just TWO clean approvals
# ((2+1)/(2+0+2) = 0.75), which is nowhere near enough evidence to hand over
# merge autonomy — a wrong large merge conflates several real events into one
# public record. So: a minimum sample floor, and a threshold high enough that one
# rejection costs several more approvals to recover from.
#
#   samples   record   trust   trusted?
#         0      0-0    0.50   no (below the sample floor)
#         5      5-0    0.86   YES  — earned
#         6      5-1    0.75   no   — re-armed by one rejection
#        15     14-1    0.88   YES  — a long record outweighs one bad call
OVERSIZED_TRUST_THRESHOLD = float(os.getenv("OVERSIZED_MERGE_TRUST", "0.80"))
OVERSIZED_MIN_SAMPLES = int(os.getenv("OVERSIZED_MERGE_MIN_SAMPLES", "5"))


def oversized_merge_trust(client) -> tuple[float, int, int]:
    """
    How far has the batched grouper earned the right to auto-publish an unusually
    large merge? Returns (trust, approvals, rejections).

    Laplace-smoothed, the same shape as learning_monitor.rebuild_source_reputation:

        trust = (approvals + 1) / (approvals + rejections + 2)

    A gate with no exit is just permanent homework: the operator would approve
    oversized merges forever and the system would never bank the fact that it
    kept getting them right. So the hold is temporary and self-lifting — see
    `oversized_merges_trusted()` for the floor + threshold that decide it. A
    rejection re-arms the gate automatically. Nothing is flipped by hand in
    either direction.

    Never raises. On any failure it returns 0.0 — which HOLDS oversized merges
    for review. An unreadable history is not evidence of good judgement.
    """
    try:
        res = (client.table("war_room_queue")
               .select("status,raw_content")
               .in_("status", ["approved", "update_approved", "rejected"])
               .order("created_at", desc=True)
               .limit(500).execute())
        rows = res.data or []
    except Exception as exc:                          # noqa: BLE001
        logger.warning("auto_publish: oversized-merge history unavailable (%s) — holding for review", exc)
        return 0.0, 0, 0

    approvals = rejections = 0
    for r in rows:
        rc = r.get("raw_content")
        if not isinstance(rc, dict) or not rc.get("_oversized_cluster"):
            continue
        if r.get("status") in ("approved", "update_approved"):
            approvals += 1
        elif r.get("status") == "rejected":
            rejections += 1
    return (approvals + 1) / (approvals + rejections + 2), approvals, rejections


def oversized_merges_trusted(trust: float, approvals: int, rejections: int) -> bool:
    """
    May oversized merges auto-publish yet? Pure, so the graduation rule is
    testable without a database.

    BOTH conditions must hold: enough decisions on record to be evidence at all,
    and a high enough hit rate among them. The sample floor is the important
    half — Laplace smoothing on its own reads 0.75 after two approvals, and two
    data points is not a track record.
    """
    return (approvals + rejections) >= OVERSIZED_MIN_SAMPLES and trust >= OVERSIZED_TRUST_THRESHOLD


# ── Eligibility ─────────────────────────────────────────────────────────────

def check_eligibility(item: dict, threshold: float,
                      *, oversized_merges_trusted: bool = False,
                      duplicate_guard_incidents: list[dict] | None = None,
                      duplicate_guard_available: bool | None = None) -> tuple[bool, str]:
    """
    Pure function: may this queue row publish itself?

    Returns (ok, reason). Reason is a stable machine code on failure — it is
    logged per row and aggregated in the run stats, so "why did nothing publish
    last night" is answerable without re-running anything.

    `oversized_merges_trusted` is computed once per run by oversized_merge_trust()
    and passed in, so this stays pure and offline-testable. It defaults to False:
    a caller that has not consulted the history does not get to skip the hold.
    """
    rc = item.get("raw_content") or {}

    if item.get("status") != "pending":
        return False, "not_pending"

    # Sentinel rows (pattern alerts, lifecycle notices, backfill summaries) are
    # operator prompts, not incidents. Publishing one would put "Stomp has
    # returned 0 items for 5 days" on the public site.
    if rc.get("notification_type"):
        return False, "notification_row"

    confidence = item.get("agent_confidence")
    if confidence is None:
        return False, "no_confidence"
    if float(confidence) < threshold:
        return False, "below_threshold"

    if not (item.get("proposed_title") or "").strip():
        return False, "missing_title"
    summary = (item.get("proposed_summary") or "").strip()
    if not summary:
        return False, "missing_summary"

    # Guardrail #4, defence in depth. Stage 2 zeroes confidence on political
    # content, so this should be unreachable — which is exactly why it is
    # cheap to assert. If it ever fires, Stage 2 has regressed.
    if _POLITICAL_MARKER in summary.upper() or _POLITICAL_MARKER in (item.get("proposed_title") or "").upper():
        return False, "political_marker"

    # Guardrail #1
    source_urls = [u for u in (rc.get("source_urls") or [item.get("source_url")]) if u]
    if len(source_urls) < 1:
        return False, "no_source_url"

    # Guardrail #2 + verifiability. check_source_urls strips signal URLs and
    # flags domains the operator has not approved.
    try:
        from classifiers.source_allowlist import check_source_urls
        verdict = check_source_urls(source_urls)
        if len(verdict.get("kept") or []) < 1:
            return False, "no_approved_source_after_filter"
        if verdict.get("unapproved"):
            return False, "unapproved_source_domain"
    except Exception as exc:                      # noqa: BLE001
        # Cannot verify the allowlist -> cannot claim the source is verifiable.
        logger.warning("auto_publish: allowlist check failed, routing to review: %s", exc)
        return False, "allowlist_check_failed"

    # QA H3 — a real article date, never "today".
    raw_date = rc.get("date") or rc.get("incident_date") or ""
    if not (isinstance(raw_date, str) and len(raw_date) >= 10 and raw_date[4] == "-" and raw_date[7] == "-"):
        return False, "no_real_date"
    if rc.get("_date_fallback"):
        return False, "date_fallback"

    # Unlike the other optional arguments, the runtime passes both guard
    # parameters explicitly. None from legacy/offline callers preserves the
    # pure function's historical behavior; a runtime fetch failure is fail-safe.
    if duplicate_guard_available is False:
        return False, "duplicate_check_unavailable"
    if duplicate_guard_incidents is not None:
        match = _possible_published_duplicate(item, duplicate_guard_incidents)
        if match:
            logger.info("auto_publish: possible published duplicate for queue %s -> %s",
                        item.get("id"), match.get("id"))
            return False, "possible_published_duplicate"

    # Stage 2's deterministic groundedness check found a number or a proper noun
    # in the summary that appears in no source, and a regeneration did not clear
    # it. Publishing that is publishing an invented specific. There is no trust
    # curve here: unlike an oversized merge (a judgement call that can be earned),
    # an ungrounded specific is a factual defect in THIS row.
    grounding = rc.get("_groundedness")
    if isinstance(grounding, dict) and grounding.get("flagged"):
        # Re-check legacy flags with the current normalizer. In particular,
        # older rows can contain the same clock as `9.25am` in the source and
        # `9:25am` in the draft, which the original literal-number check held.
        # A failed/incomplete original check remains fail-closed.
        if grounding.get("checked") is False:
            return False, "ungrounded_specifics"
        try:
            from filters.stage2_writer import find_ungrounded, non_signal_source_text
            findings = find_ungrounded(
                item.get("proposed_summary") or "",
                non_signal_source_text(rc),
                rc.get("date") or rc.get("incident_date") or "",
            )
        except Exception as exc:                  # noqa: BLE001
            logger.warning("auto_publish: groundedness re-check failed: %s", exc)
            return False, "ungrounded_specifics"
        if findings["numbers"] or findings["proper_nouns"]:
            return False, "ungrounded_specifics"

    # Stage 2's deterministic casualty check found the source language and the
    # model's deaths/injuries disagreeing. A wrong death count is the most
    # damaging factual error this archive can publish, so it goes to a human.
    casualty = rc.get("_casualty_check")
    if isinstance(casualty, dict) and casualty.get("flagged"):
        # Re-evaluate stored flags with the current count parser. Older rows can
        # mistake an identifier such as bus service 852 for a casualty count.
        try:
            from filters.casualty_check import validate
            from filters.stage2_writer import non_signal_source_text
            source_text = non_signal_source_text(rc)
            if not source_text.strip():
                # A legacy flag cannot be cleared without evidence to re-check.
                return False, "casualty_mismatch"
            verdict = validate(
                source_text, rc.get("deaths"), rc.get("injuries")
            )
        except Exception as exc:                  # noqa: BLE001
            logger.warning("auto_publish: casualty re-check failed: %s", exc)
            return False, "casualty_mismatch"
        if not verdict.get("ok"):
            return False, "casualty_mismatch"

    # An unusually large merge: one grouping call decided that N articles are all
    # the same event. That is a bigger single bet than a normal row, so it waits
    # for a human — but only until the grouper has earned merges at this size.
    if rc.get("_oversized_cluster") and not oversized_merges_trusted:
        return False, "oversized_cluster_unproven"

    return True, "eligible"


def check_update_eligibility(item: dict, draft_threshold: float, match_threshold: float):
    """
    Gate for auto-applying an UPDATE row (merge a new source into an existing
    published incident). Returns (eligible, reason).

    Same fail-open direction as check_eligibility: an ineligible row is LEFT for
    the operator, never rejected. Two confidences must BOTH clear — the Stage 2
    draft confidence (write quality) and the consolidation same-event confidence
    (the wrong-merge axis, the strict one) — and the appended source must be a
    verifiable publisher URL. confirm-update trusts the operator for that last
    check; the autonomous path has no operator, so it re-runs the allowlist here.
    """
    rc = item.get("raw_content") or {}

    if item.get("status") != "update":
        return False, "not_update"
    if rc.get("notification_type"):
        return False, "notification_row"
    if not item.get("update_target_incident_id"):
        return False, "no_update_target"

    confidence = item.get("agent_confidence")
    if confidence is None:
        return False, "no_confidence"
    if float(confidence) < draft_threshold:
        return False, "below_threshold"

    # Same-event confidence. Missing -> hold: a row written before
    # _match_confidence was persisted (queue_row.py) cannot be auto-merged safely.
    match_conf = rc.get("_match_confidence")
    if match_conf is None:
        return False, "no_match_confidence"
    if float(match_conf) < match_threshold:
        return False, "match_below_threshold"

    new_url = item.get("source_url")
    if not new_url:
        return False, "no_source_url"
    try:
        from classifiers.source_allowlist import check_source_urls
        verdict = check_source_urls([new_url])
        # A signal or redirect URL is stripped entirely -> nothing verifiable to add.
        if len(verdict.get("kept") or []) < 1:
            return False, "source_not_verifiable"
        if verdict.get("unapproved"):
            return False, "unapproved_source_domain"
    except Exception as exc:                      # noqa: BLE001
        logger.warning("auto_merge: allowlist check failed, routing to review: %s", exc)
        return False, "allowlist_check_failed"

    return True, "eligible"


# ── Publish ─────────────────────────────────────────────────────────────────

def _generate_art(incident: dict, run: AgentRun, budget) -> dict:
    """
    Render the incident's image BEFORE the insert (ART_PIPELINE.md §6.1).

    Returns the fields to merge into the insert. Publication is never blocked on
    the outcome: a failure or a suppression publishes with `pixel_art_url` null,
    and the frontend already degrades to the placeholder and og-default.jpg.
    That is deliberate — the failure class disproportionately hits DARK EVENTS,
    which are the most newsworthy cards, so blocking would silently withhold
    exactly the stories that matter most (IMAGE_RETRY_AND_RECTIFY.md §4).

    Generating first rather than writing back after the insert avoids the ISR
    staleness trap: under Next.js caching an update-after-insert leaves the live
    page serving the placeholder until revalidation fires.
    """
    if not ART_ENABLED:
        return {"pixel_art_url": None, "image_status": "pending"}
    try:
        from art.generate_image import generate_image
        result = generate_image(incident, budget=budget)
    except Exception as exc:                      # noqa: BLE001 — never block a publish
        run.warn("image_failed", f"{incident.get('slug')}: {exc}")
        return {"pixel_art_url": None, "image_status": "transient"}

    if result.status not in ("ok", "suppressed"):
        run.warn("image_%s" % result.status,
                 f"{incident.get('slug')}: {result.attempts[-1].get('reason', '')[:200]}"
                 if result.attempts else incident.get("slug", ""))
    return {
        "pixel_art_url":  result.url,
        "image_status":   result.status,
        "image_prompt":   result.final_prompt or None,
        "image_attempts": result.attempts or None,
    }


def _publish(item: dict, client, run: AgentRun, budget=None) -> str | None:
    """
    Port of the War Room approve route. Returns the new incident id, or None.
    Raises nothing — a failed publish leaves the row pending for a human.
    """
    rc = item.get("raw_content") or {}
    title = (item.get("proposed_title") or "").strip()[:120]
    summary = (item.get("proposed_summary") or "").strip()
    classification = item.get("proposed_classification")
    if classification not in ("heart", "clown", "dagger", "custom"):
        classification = "dagger"
    try:
        severity = max(1, min(5, int(item.get("proposed_severity") or 3)))
    except (TypeError, ValueError):
        severity = 3

    from classifiers.source_allowlist import check_source_urls
    raw_urls = [u for u in (rc.get("source_urls") or [item.get("source_url")]) if u]
    source_urls = check_source_urls(raw_urls).get("kept") or []

    incident_date = str(rc.get("date") or rc.get("incident_date"))[:10]
    block_number = rc.get("block_number")
    area_name = rc.get("area_name")

    # Geocoding failure publishes without a pin — same as the human path.
    latitude = longitude = None
    try:
        from classifiers.geocoding import geocode_incident
        coords = geocode_incident(block_number, area_name, title, summary)
        if coords:
            latitude, longitude = coords
    except Exception as exc:                      # noqa: BLE001
        run.warn("geocode_failed", f"{title[:60]}: {exc}")

    incident = {
        "title": title,
        "summary": summary,
        "classification": classification,
        "severity": severity,
        "block_number": block_number,
        "area_name": area_name,
        "latitude": latitude,
        "longitude": longitude,
        "source_urls": source_urls,
        "corroboration_count": item.get("corroboration_count") or 1,
        "edmw_signal_count": item.get("edmw_signal_count") or 0,
        "hype_meter": rc.get("hype_meter") or 0,
        "slug": item.get("proposed_slug") or rc.get("slug"),
        "seo_title": rc.get("seo_title"),
        "seo_description": rc.get("seo_description"),
        "tags": rc.get("tags") or [],
        "agent_confidence": item.get("agent_confidence"),
        "chaos_contribution": rc.get("chaos_contribution"),
        "deaths": rc.get("deaths"),
        "injuries": rc.get("injuries"),
        "source_timeline": rc.get("source_timeline") or [],
        "update_count": rc.get("update_count") or 0,
        "latest_source_role": rc.get("latest_source_role"),
        "conclusion_type": rc.get("conclusion_type"),
        "is_milestone": bool(rc.get("is_milestone")),
        "milestone_type": rc.get("milestone_type"),
        "milestone_value": rc.get("milestone_value"),
        "incident_date": incident_date,
        "is_published": True,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    # Render before the insert so the URL is in the row from the start. Reads
    # the finished incident, never the sources (ART_PIPELINE.md §2), which is
    # why this sits below the dict rather than above it.
    incident.update(_generate_art(incident, run, budget))

    try:
        # No .single() here: postgrest's insert builder has .select() but NOT
        # .single(), so chaining it raises AttributeError — which this except
        # would have swallowed as "publish_failed", making auto-publish appear
        # to run while never publishing anything.
        res = client.table("incidents").insert(incident).select("id").execute()
        if not res.data:
            raise RuntimeError("insert returned no row")
        incident_id = res.data[0]["id"]
    except Exception as exc:                      # noqa: BLE001
        # 23505 == duplicate slug. A human can retitle; the agent should not
        # invent a new slug and publish a near-duplicate.
        run.error_("publish_failed", f"{title[:60]}: {exc}", queue_id=item.get("id"))
        return None

    # QA H2 — this update governs idempotency. If it fails the incident is live
    # but the row still looks pending, and the next pass would publish a second
    # copy. Unpublish immediately rather than leaving that landmine.
    try:
        client.table("war_room_queue").update({
            "status": "approved",
            "incident_id": incident_id,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", item["id"]).execute()
    except Exception as exc:                      # noqa: BLE001
        run.error_("queue_update_failed",
                   f"published {incident_id} but queue row {item['id']} not closed — rolling back publish: {exc}")
        try:
            client.table("incidents").update(
                {"is_published": False, "published_at": None}
            ).eq("id", incident_id).execute()
            run.warn("publish_rolled_back", f"incident {incident_id} unpublished to avoid a double-publish")
        except Exception as rollback_exc:         # noqa: BLE001
            run.anomaly("rollback_failed",
                        f"incident {incident_id} is LIVE with an open queue row — reconcile by hand: {rollback_exc}")
        return None

    _log_training_signal(item, incident_id, client, run)
    return incident_id


# ── Merge (auto-apply an update) ─────────────────────────────────────────────

def _compute_merge(existing: dict, new_url: str, source_name: str,
                   headline: str, new_date):
    """
    Pure mirror of applyUpdate() in apps/war-room/lib/utils.ts, with the summary
    edit omitted (auto-merge never rewrites the summary). Returns
    (updates, snapshot). If you change one, change the other — parity is asserted
    by test_auto_merge_eligibility.py against the same fixtures the TS test uses.
    """
    existing_urls = existing.get("source_urls") or []
    existing_timeline = existing.get("source_timeline")
    if not isinstance(existing_timeline, list):
        existing_timeline = []
    existing_date = existing.get("incident_date")
    existing_count = existing.get("update_count") or 0
    existing_developing = bool(existing.get("is_developing"))

    snapshot = {
        "source_urls":       existing_urls,
        "source_timeline":   existing_timeline,
        "update_count":      existing_count,
        "incident_date":     existing_date,
        "first_reported_at": existing.get("first_reported_at"),
        "is_developing":     existing_developing,
        "summary":           existing.get("summary"),
    }

    merged_urls = existing_urls if new_url in existing_urls else [*existing_urls, new_url]
    nd = new_date or existing_date or existing.get("first_reported_at") or None
    merged_timeline = [
        *existing_timeline,
        {"date": nd or existing_date, "source_url": new_url,
         "source_name": source_name, "headline": headline},
    ]

    updated_date = existing_date if (existing_date and nd and existing_date > nd) else (nd or existing_date)
    existing_first = existing.get("first_reported_at") or existing_date
    first_reported = existing_first if (existing_first and nd and existing_first < nd) else (existing_first or nd)

    updates = {
        "source_urls":       merged_urls,
        "source_timeline":   merged_timeline,
        "is_developing":     True,
        "update_count":      existing_count + 1,
        "incident_date":     updated_date,
        "first_reported_at": first_reported,
    }
    return updates, snapshot


def _apply_merge(item: dict, client, run: AgentRun) -> str | None:
    """
    Port of the War Room confirm-update route. Merges the row's source into
    `update_target_incident_id` and returns that id, or None on failure. Writes
    the pre-merge snapshot into raw_content._undo_snapshot so the operator can
    undo it (revert-update). Claims the queue row BEFORE mutating the incident,
    same ordering (and same reasoning) as _publish/confirm-update.
    """
    rc = item.get("raw_content") or {}
    target = item.get("update_target_incident_id")

    try:
        res = (client.table("incidents")
               .select("id,source_urls,source_timeline,update_count,"
                       "incident_date,first_reported_at,is_developing,summary")
               .eq("id", target).single().execute())
        existing = res.data
    except Exception as exc:                      # noqa: BLE001
        run.error_("merge_fetch_failed", f"target {target}: {exc}", queue_id=item.get("id"))
        return None
    if not existing:
        run.warn("merge_target_missing", f"incident {target} not found", queue_id=item.get("id"))
        return None

    new_url = item.get("source_url")
    source_name = rc.get("source_name") or item.get("source_type") or "unknown"
    headline = (item.get("proposed_title") or rc.get("title") or "")[:200]
    new_date = rc.get("date") or rc.get("published_at") or None

    # Guardrail #2: a signal source's URL must never land in source_urls.
    if is_signal_source(item.get("source_type"), new_url or ""):
        run.warn("merge_signal_source_blocked",
                  f"queue {item.get('id')}: source_type={item.get('source_type')!r} is a signal, refusing merge",
                  queue_id=item.get("id"))
        return None

    updates, snapshot = _compute_merge(existing, new_url, source_name, headline, new_date)

    # Autonomy path: refresh the summary too, but ONLY when explicitly enabled and
    # the ingestion-time enrichment passed groundedness. `snapshot` already holds
    # the pre-merge summary, so revert-update restores it if this was wrong.
    if AUTO_ENRICH_SUMMARY and rc.get("_enriched_summary") and rc.get("_enriched_grounded"):
        updates["summary"] = rc["_enriched_summary"]
        run.info("merge_summary_enriched",
                 f"applied grounded enriched summary to incident {target}",
                 queue_id=item.get("id"))

    # Claim BEFORE mutating (QA H2 reasoning): a failed status write must never
    # leave the incident merged but the row re-confirmable. CAS on status=update.
    try:
        claimed = (client.table("war_room_queue").update({
            "status":       "update_approved",
            "incident_id":  target,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "raw_content":  {**rc, "_undo_snapshot": snapshot},
        }).eq("id", item["id"]).eq("status", "update").select("id").execute())
    except Exception as exc:                      # noqa: BLE001
        run.error_("merge_claim_failed", f"queue {item['id']}: {exc}", queue_id=item.get("id"))
        return None
    if not claimed.data:
        # An operator confirmed/rejected it between the fetch and now — leave it.
        return None

    try:
        client.table("incidents").update(updates).eq("id", target).execute()
    except Exception as exc:                      # noqa: BLE001
        # Give the claim back so a human can retry once the cause is fixed.
        try:
            (client.table("war_room_queue")
             .update({"status": "update", "incident_id": None, "processed_at": None})
             .eq("id", item["id"]).eq("status", "update_approved").execute())
        except Exception as rel_exc:              # noqa: BLE001
            run.anomaly("merge_unclaim_failed",
                        f"queue {item['id']} stuck update_approved — reconcile by hand: {rel_exc}")
        run.error_("merge_update_failed", f"incident {target}: {exc}", queue_id=item.get("id"))
        return None

    _log_merge_signal(item, target, client, run)
    return target


def _log_merge_signal(item: dict, incident_id: str, client, run: AgentRun) -> None:
    """The autonomous merge IS training data — sibling of _log_training_signal.
    decided_by='agent' keeps it out of the operator agreement-rate maths."""
    rc = item.get("raw_content") or {}
    try:
        client.table("training_signals").insert({
            "incident_id": incident_id,
            "queue_id": item.get("id"),
            "action": "auto_update",
            "decision": "auto_approve",
            "decided_by": "agent",
            "source_url": item.get("source_url"),
            "source_type": item.get("source_type"),
            "proposed_classification": item.get("proposed_classification"),
            "proposed_severity": item.get("proposed_severity"),
            "original_draft": item.get("proposed_summary"),
            "agent_confidence": item.get("agent_confidence"),
            "agent_confidence_was": item.get("agent_confidence"),
            "agent_role_proposed": rc.get("agent_role_proposed") or "update",
            "operator_note": f"Auto-merged into {incident_id} (match {rc.get('_match_confidence')}).",
        }).execute()
    except Exception as exc:                      # noqa: BLE001
        run.error_("merge_signal_failed", f"incident {incident_id}: {exc}")


def _log_training_signal(item: dict, incident_id: str, client, run: AgentRun) -> None:
    """
    The autonomous decision IS training data (req #3). `decided_by='agent'`
    keeps it out of the operator agreement-rate maths in learning_snapshots —
    without that flag the agent would be grading its own homework and the
    agreement rate would read 100% forever.
    """
    try:
        client.table("training_signals").insert({
            "incident_id": incident_id,
            "queue_id": item.get("id"),
            "action": "auto_approve",
            "decision": "auto_approve",
            "decided_by": "agent",
            "source_url": item.get("source_url"),
            "source_type": item.get("source_type"),
            "proposed_classification": item.get("proposed_classification"),
            "proposed_severity": item.get("proposed_severity"),
            "original_draft": item.get("proposed_summary"),
            "original_classification": item.get("proposed_classification"),
            "original_severity": item.get("proposed_severity"),
            "agent_confidence": item.get("agent_confidence"),
            "agent_confidence_was": item.get("agent_confidence"),
            "operator_note": "Auto-published without human review (confidence gate).",
        }).execute()
    except Exception as exc:                      # noqa: BLE001
        # Telemetry, not the transaction — but loudly, because a silent failure
        # here is how the learning loop quietly stops learning (cf. migration 009).
        run.error_("training_signal_failed", f"incident {incident_id}: {exc}")


# ── Entry point ─────────────────────────────────────────────────────────────

def run(supabase_client=None, dry_run: bool = False, trigger: str = "scheduler") -> dict:
    """Auto-publish eligible rows, then notify about the rest. Never raises."""
    stats = {"considered": 0, "published": 0, "merged": 0, "skipped": 0, "failed": 0,
             "needs_review": None, "review_queue_verified": False,
             "reasons": {}, "dry_run": dry_run}

    if not agent_enabled(AGENT):
        logger.warning("auto_publish: disabled via AGENT_DISABLED")
        stats["disabled"] = True
        return stats

    threshold = _threshold()
    merge_threshold = _merge_threshold()
    merge_match_threshold = _merge_match_threshold()

    with AgentRun(AGENT, trigger=trigger) as arun:
        arun.stat("threshold", threshold)
        try:
            client = _client(supabase_client)
        except Exception as exc:                  # noqa: BLE001
            arun.fail(f"no Supabase client: {exc}")
            stats["failed"] += 1
            return stats

        # Refuse to publish into a schema that cannot record the decision.
        # Drafts still queue normally; the operator reviews them by hand until
        # migration 011 lands. Degrading to "a human decides" is always safe.
        recordable, why = can_record_decisions(client)
        if not recordable and not dry_run:
            arun.anomaly("cannot_record_decisions",
                         f"auto-publish SKIPPED for this pass: {why}")
            arun.set_summary("auto-publish skipped — schema cannot record autonomous decisions")
            stats["skipped_all"] = why
            _notify_schema_blocked(why, client)
            return stats

        try:
            res = (client.table("war_room_queue")
                   .select("*")
                   .in_("status", ["pending", "update"])
                   .is_("processed_at", "null")
                   .order("agent_confidence", desc=True)
                   .limit(500).execute())
            rows = res.data or []
        except Exception as exc:                  # noqa: BLE001
            arun.fail(f"queue fetch failed: {exc}")
            stats["failed"] += 1
            return stats

        stats["considered"] = len(rows)
        arun.stat("queue_size", len(rows))

        # Independent second net against published incidents. A read failure
        # holds otherwise eligible rows; the archive is not assumed unique.
        duplicate_guard_incidents = None
        duplicate_guard_available = True
        if any(r.get("status") == "pending" for r in rows):
            try:
                duplicate_guard_incidents = _fetch_duplicate_guard_incidents(client)
            except Exception as exc:                  # noqa: BLE001
                duplicate_guard_available = False
                logger.warning("auto_publish: duplicate guard unavailable; holding pending rows: %s", exc)
                arun.warn("duplicate_guard_unavailable",
                          "published incident list unavailable; eligible pending rows held for review")

        # Computed once per run, not per row: has the batched grouper earned the
        # right to auto-publish an unusually large merge yet?
        trust, ovr_ok, ovr_no = oversized_merge_trust(client)
        oversized_trusted = oversized_merges_trusted(trust, ovr_ok, ovr_no)
        stats["oversized_merge_trust"] = round(trust, 3)
        arun.stat("oversized_merge_trust", round(trust, 3))
        if ovr_ok or ovr_no:
            arun.info(
                "oversized_trust",
                f"oversized merges: {ovr_ok} approved / {ovr_no} rejected -> trust {trust:.2f} "
                f"({'AUTO-PUBLISHING' if oversized_trusted else 'still held for review'}; "
                f"needs >= {OVERSIZED_MIN_SAMPLES} decisions and trust >= {OVERSIZED_TRUST_THRESHOLD:.2f})",
            )

        # ONE attempt budget for the whole pass. The ceiling counts billed image
        # ATTEMPTS, not incidents: the refusal ladder can spend three per row, so
        # a per-incident cap of 25 would permit 75 calls (IMAGE_RETRY_AND_RECTIFY
        # §5). Exhausting it stops generation and never stops publication.
        art_budget = None
        if ART_ENABLED:
            from art.generate_image import AttemptBudget
            art_budget = AttemptBudget()

        merged_titles: list[str] = []
        published_queue_ids: list[str] = []
        merged_queue_ids: list[str] = []
        for row_index, item in enumerate(rows):
            # ── Auto-merge branch (update rows) ──────────────────────────────
            # Only when AUTO_MERGE_ENABLED; otherwise update rows fall through to
            # the publish path below and are skipped there as 'not_pending',
            # exactly as before this feature existed.
            if AUTO_MERGE_ENABLED and item.get("status") == "update":
                if stats["merged"] >= MAX_AUTO_MERGE_PER_RUN:
                    arun.anomaly("merge_per_run_cap_hit",
                                 f"stopped at {MAX_AUTO_MERGE_PER_RUN} auto-merges this pass")
                    continue
                mok, mreason = check_update_eligibility(item, merge_threshold, merge_match_threshold)
                stats["reasons"][mreason] = stats["reasons"].get(mreason, 0) + 1
                if not mok:
                    stats["skipped"] += 1
                    item["_hold_reason"] = mreason
                    # A high-confidence merge a guardrail stopped is worth a look;
                    # a routine below-threshold one is not.
                    if mreason not in ("below_threshold", "match_below_threshold",
                                       "no_match_confidence", "not_update", "notification_row"):
                        arun.warn("merge_gate_blocked",
                                  f"{(item.get('proposed_title') or '')[:70]}: {mreason}",
                                  queue_id=item.get("id"), confidence=item.get("agent_confidence"))
                    continue
                if dry_run:
                    stats["merged"] += 1
                    merged_titles.append((item.get("proposed_title") or "")[:80])
                    arun.info("would_merge", f"[dry-run] {(item.get('proposed_title') or '')[:70]}")
                    continue
                merged_id = _apply_merge(item, client, arun)
                if merged_id:
                    item["status"] = "update_approved"
                    stats["merged"] += 1
                    merged_titles.append((item.get("proposed_title") or "")[:80])
                    merged_queue_ids.append(str(item.get("id")))
                    arun.success("auto_merged",
                                 f"{(item.get('proposed_title') or '')[:70]} -> {merged_id} "
                                 f"(match={(item.get('raw_content') or {}).get('_match_confidence')})",
                                 incident_id=merged_id, queue_id=item.get("id"))
                else:
                    stats["failed"] += 1
                    item["_hold_reason"] = "merge_failed"
                continue

            if stats["published"] >= MAX_AUTO_PUBLISH_PER_RUN:
                remaining = [
                    row for row in rows[row_index:]
                    if row.get("status") == "pending"
                    and not (row.get("raw_content") or {}).get("notification_type")
                ]
                for row in remaining:
                    row["_hold_reason"] = "per_run_cap_hit"
                if remaining:
                    stats["reasons"]["per_run_cap_hit"] = (
                        stats["reasons"].get("per_run_cap_hit", 0) + len(remaining)
                    )
                arun.anomaly("per_run_cap_hit",
                             f"stopped at {MAX_AUTO_PUBLISH_PER_RUN} auto-publishes; "
                             f"{len(remaining)} pending card(s) left for review")
                break

            ok, reason = check_eligibility(item, threshold,
                                           oversized_merges_trusted=oversized_trusted,
                                           duplicate_guard_incidents=duplicate_guard_incidents,
                                           duplicate_guard_available=duplicate_guard_available)
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

            if not ok:
                stats["skipped"] += 1
                item["_hold_reason"] = (
                    "manual_review" if reason == "not_pending" and item.get("status") == "update"
                    else reason
                )
                if reason not in ("below_threshold", "not_pending", "notification_row"):
                    # These are the interesting skips: a high-confidence row that
                    # a guardrail stopped. Worth a human's attention.
                    arun.warn("gate_blocked", f"{(item.get('proposed_title') or '')[:70]}: {reason}",
                              queue_id=item.get("id"), confidence=item.get("agent_confidence"))
                continue

            if dry_run:
                stats["published"] += 1
                arun.info("would_publish", f"[dry-run] {(item.get('proposed_title') or '')[:70]}")
                continue

            incident_id = _publish(item, client, arun, budget=art_budget)
            if incident_id:
                item["status"] = "approved"
                stats["published"] += 1
                published_queue_ids.append(str(item.get("id")))
                # Include successes from this pass in the duplicate guard so
                # duplicate queue rows cannot all publish against the same
                # start-of-pass archive snapshot.
                if duplicate_guard_incidents is not None:
                    rc = item.get("raw_content") or {}
                    duplicate_guard_incidents.append({
                        "id": incident_id,
                        "title": item.get("proposed_title") or "",
                        "summary": item.get("proposed_summary") or "",
                        "incident_date": rc.get("date") or rc.get("incident_date"),
                    })
                arun.success("auto_published",
                             f"{(item.get('proposed_title') or '')[:70]} (conf={item.get('agent_confidence')})",
                             incident_id=incident_id, queue_id=item.get("id"))
            else:
                stats["failed"] += 1
                item["_hold_reason"] = "publish_failed"

        # The in-memory rows are only the pass-start snapshot. Refresh the same
        # pending/update view the War Room uses before reporting counts; a human
        # may have acted during this pass, and our writes may have changed it.
        review_rows = None
        try:
            review_rows = _fetch_current_review_queue(client)
            prior_by_id = {str(row["id"]): row for row in rows if row.get("id") is not None}
            for row in review_rows:
                prior = prior_by_id.get(str(row.get("id")))
                if prior and prior.get("_hold_reason"):
                    row["_hold_reason"] = prior["_hold_reason"]
            stats["needs_review"] = len(_reviewable_cards(review_rows))
            stats["review_queue_verified"] = True
        except Exception as exc:                  # noqa: BLE001
            arun.warn("review_queue_refresh_failed",
                      f"could not verify current War Room queue after auto-publish: {exc}")

        arun.stat("published", stats["published"])
        arun.stat("merged", stats["merged"])
        arun.stat("needs_review", stats["needs_review"])
        arun.stat("review_queue_verified", stats["review_queue_verified"])
        arun.stat("skip_reasons", stats["reasons"])
        merge_note = f", auto-merged {stats['merged']}" if AUTO_MERGE_ENABLED else ""
        review_note = (f"{stats['needs_review']} left for review"
                       if stats["review_queue_verified"] else "review queue unverified")
        arun.set_summary(
            f"auto-published {stats['published']}{merge_note}, {review_note} "
            f"(threshold {threshold})"
        )

        if not dry_run:
            if review_rows is None:
                _notify_review_queue_unverified(
                    client, published_queue_ids, merged_queue_ids,
                )
            else:
                _notify_review_queue(
                    review_rows, merged_titles, client, published_queue_ids, merged_queue_ids,
                )

    return stats


def _notify_schema_blocked(why: str, client) -> None:
    """Loud, because the symptom otherwise is 'autonomy silently did nothing'."""
    dedup_key, window = _review_report_window()
    notify(
        "maintenance",
        f"Yishun Again — auto-publish report ({window} SGT window)",
        ("Autonomous publishing did not run this pass.\n\n"
         f"Reason: {why}\n\n"
         "Nothing is lost: drafts are queueing normally and are waiting for you in\n"
         "the War Room. But nothing will auto-publish until migration 011 is applied.\n\n"
         "Apply packages/db/migrations/011_autonomy_ops_schema.sql by hand in the\n"
         "Supabase SQL Editor (there is no migration runner — QA M15), then the next\n"
         "scheduled pass (03:58 or 15:58 SGT) will pick it up with no redeploy needed."
         + footer()),
        dedup_key=dedup_key,
        throttle_minutes=720,
        coalesce_existing=True,
        coalesce_event_id="schema:" + hashlib.sha256(why.encode()).hexdigest()[:16],
        client=client,
    )


def _fetch_current_review_queue(client) -> list[dict]:
    """Read the current War Room queue after all auto-publish writes finish."""
    result = (client.table("war_room_queue")
              .select("*")
              .in_("status", ["pending", "update"])
              .order("created_at", desc=True)
              .execute())
    return result.data or []


def _reviewable_cards(rows) -> list[dict]:
    """Distinct incident cards awaiting review; ignore notification sentinels."""
    cards = {}
    for row in rows:
        if (row.get("status") not in ("pending", "update")
                or (row.get("raw_content") or {}).get("notification_type")):
            continue
        identity = row.get("id") or row.get("proposed_slug") or row.get("proposed_title")
        cards[str(identity)] = row
    return list(cards.values())


def _notify_review_queue_unverified(client, published_queue_ids, merged_queue_ids) -> None:
    """Report the refresh failure explicitly instead of sending stale counts."""
    dedup_key, window = _review_report_window()
    event_ids = sorted(
        [f"published:{queue_id}" for queue_id in published_queue_ids]
        + [f"merged:{queue_id}" for queue_id in merged_queue_ids]
        + ["queue_unverified"]
    )
    notify(
        "anomaly",
        f"Yishun Again — auto-publish report ({window} SGT window)",
        (f"Pass window: {window} SGT.\n"
         f"Auto-publish finished: {len(published_queue_ids)} published, "
         f"{len(merged_queue_ids)} updates merged. "
         "I could not refresh the current review queue. "
         "No queue count is reported; open the War Room to check the current state."
         + footer()),
        dedup_key=dedup_key,
        throttle_minutes=720,
        coalesce_existing=True,
        coalesce_event_id=hashlib.sha256("|".join(event_ids).encode()).hexdigest()[:20],
        client=client,
    )


def _review_report_window() -> tuple[str, str]:
    """Stable identity for the morning or afternoon SGT auto-publish report."""
    from ops.daily import SGT

    now = datetime.now(timezone.utc).astimezone(SGT)
    window = "morning" if now.hour < 12 else "afternoon"
    return f"review_queue:{now.date().isoformat()}:{window}", window


def _notify_review_queue(rows, merged_titles, client,
                         published_queue_ids=None, merged_queue_ids=None) -> None:
    """
    Send one rolling summary per SGT scheduler window. Later auto-publish runs
    in the same window edit this Telegram message and append their results.
    """
    pending_for_review = _reviewable_cards(rows)
    published_queue_ids = published_queue_ids or []
    merged_queue_ids = merged_queue_ids or []
    if (not pending_for_review and not merged_queue_ids
            and not merged_titles and not published_queue_ids):
        return

    lines = [
        f"Published this pass: {len(published_queue_ids)}.",
        f"Auto-merged updates: {len(merged_queue_ids)}.",
        f"Needs review now: {len(pending_for_review)} card(s) in the current War Room queue.",
    ]

    # Merges mutate a LIVE incident, so surface them for a look — each is
    # undoable from the "Recently merged updates" panel in the War Room queue.
    if merged_titles:
        merge_counts = {}
        for title in merged_titles:
            merge_counts[title] = merge_counts.get(title, 0) + 1
        lines += ["", "Merged updates:"]
        for title, count in list(merge_counts.items())[:5]:
            suffix = f" ({count} updates)" if count > 1 else ""
            lines.append(f"  • {title}{suffix}")
        if len(merge_counts) > 5:
            lines.append(f"  • and {len(merge_counts) - 5} more title(s)")
        lines.append("Undo these from the War Room queue's Recently-merged panel.")

    if pending_for_review:
        # Group repeated headlines so duplicate queue rows are visible as a
        # count, not repeated Telegram lines. Queue ids still drive the count;
        # grouping is only a display choice.
        groups = {}
        for row in sorted(pending_for_review,
                          key=lambda x: -(float(x.get("agent_confidence") or 0))):
            kind = "UPDATE" if row.get("status") == "update" else "NEW"
            title = (row.get("proposed_title") or "(untitled)")[:70]
            reason = row.get("_hold_reason") or "not_processed_this_pass"
            key = (kind, title, _human_hold_reason(reason))
            group = groups.setdefault(key, {
                "count": 0, "draft_confidences": [], "match_confidences": [],
                "match_confidence_missing": 0,
            })
            group["count"] += 1
            confidence = row.get("agent_confidence")
            if confidence is not None:
                group["draft_confidences"].append(float(confidence))
            if kind == "UPDATE":
                match_confidence = (row.get("raw_content") or {}).get("_match_confidence")
                if match_confidence is None:
                    group["match_confidence_missing"] += 1
                else:
                    group["match_confidences"].append(float(match_confidence))
        lines += ["", "Waiting for review:"]
        displayed = 0
        for (kind, title, reason), group in list(groups.items())[:5]:
            confidences = group["draft_confidences"]
            if confidences:
                low, high = min(confidences), max(confidences)
                draft_text = (f"{low:.0%}" if low == high
                              else f"{low * 100:.0f}–{high:.0%}")
            else:
                draft_text = "unknown"
            confidence_text = f"draft {draft_text}"
            if kind == "UPDATE":
                confidence_text += f" (needs ≥{_merge_threshold():.1%})"
                matches = group["match_confidences"]
                if matches:
                    low, high = min(matches), max(matches)
                    match_text = (f"{low:.1%}" if low == high
                                  else f"{low * 100:.1f}–{high * 100:.1f}%")
                else:
                    match_text = "missing"
                confidence_text += (
                    f"; same-event {match_text} (needs ≥{_merge_match_threshold():.1%})"
                )
                missing = group["match_confidence_missing"]
                if missing and matches:
                    confidence_text += f" ({missing} missing)"
            count = group["count"]
            suffix = f" ({count} cards)" if count > 1 else ""
            lines.append(f"  • [{confidence_text}] {kind}: {title}{suffix} — {reason}")
            displayed += count
        if len(pending_for_review) > displayed:
            lines.append(f"  • and {len(pending_for_review) - displayed} more card(s)")

    lines.append(f"\nReview: {war_room_url('/queue')}")

    dedup_key, window = _review_report_window()
    state = [
        f"review:{r.get('id')}:{r.get('status')}:{r.get('_hold_reason')}:{r.get('proposed_title')}:{r.get('agent_confidence')}:{(r.get('raw_content') or {}).get('_match_confidence')}"
        for r in pending_for_review
    ]
    state += [f"published:{queue_id}" for queue_id in published_queue_ids]
    state += [f"merged:{queue_id}" for queue_id in merged_queue_ids]
    event_id = hashlib.sha256("|".join(sorted(state)).encode()).hexdigest()[:20]

    notify(
        "review_queue",
        f"Yishun Again — auto-publish report ({window} SGT window)",
        f"Pass window: {window} SGT.\n" + "\n".join(lines) + footer(),
        dedup_key=dedup_key,
        throttle_minutes=720,
        coalesce_existing=True,
        coalesce_event_id=event_id,
        client=client,
    )
