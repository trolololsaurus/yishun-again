"""
Autonomous publishing (req #3) + review-queue notification (req #4).

Runs immediately after the ingestion pass. Two jobs:

  1. Every `pending` queue row at confidence >= AUTO_PUBLISH_CONFIDENCE (0.95)
     is approved and published without a human — and the decision is recorded as
     a training signal so the learning loop can grade it later.
  2. Everything left below the threshold triggers ONE email telling the operator
     how many cards are waiting in the War Room.

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
is corrupting an already-published incident rather than adding a new one — so
auto-merge is a SEPARATE decision, gated behind `AUTO_MERGE_ENABLED` (default
OFF). When off, every update row is left for review exactly as before. When on,
a merge auto-applies only when BOTH the draft confidence (`agent_confidence`) and
the consolidation same-event confidence (`_match_confidence`) clear their
thresholds AND the appended source survives the allowlist — and it snapshots the
pre-merge state so the operator can undo it (revert-update). See
check_update_eligibility / _apply_merge and docs/AUTONOMY.md.
"""

import logging
import os
from datetime import datetime, timezone

from ops.activity import AgentRun, agent_enabled
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

# ── Autonomous merge (auto-apply an UPDATE row) ──────────────────────────────
# Applying a merge mutates an ALREADY-PUBLISHED incident, and a wrong merge is
# near-invisible (one extra URL in a source list) and hits the source-integrity
# constraint, so this ships DARK: default off, flip AUTO_MERGE_ENABLED on when
# ready. Two thresholds because two different confidences must both be high:
#   AUTO_MERGE_CONFIDENCE       — the Stage 2 draft confidence (write quality)
#   AUTO_MERGE_MATCH_CONFIDENCE — the consolidation same-event confidence (the
#                                 wrong-merge axis; the strict one). Held if the
#                                 row predates _match_confidence being persisted.
# The undo net (PR #1: revert-update + _undo_snapshot) is the safety floor that
# makes this acceptable — do not enable this without migration 018 applied.
AUTO_MERGE_ENABLED = os.getenv("AUTO_MERGE_ENABLED", "false").strip().lower() in ("1", "true", "on", "yes")


def _merge_threshold() -> float:
    try:
        return float(os.getenv("AUTO_MERGE_CONFIDENCE", "0.95"))
    except ValueError:
        return 0.95


def _merge_match_threshold() -> float:
    try:
        return float(os.getenv("AUTO_MERGE_MATCH_CONFIDENCE", "0.95"))
    except ValueError:
        return 0.95


MAX_AUTO_MERGE_PER_RUN = int(os.getenv("AUTO_MERGE_MAX_PER_RUN", "25"))

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


def _client(explicit=None):
    if explicit is not None:
        return explicit
    from classifiers.corroboration import get_supabase_client
    return get_supabase_client()


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
                      *, oversized_merges_trusted: bool = False) -> tuple[bool, str]:
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

    # Stage 2's deterministic groundedness check found a number or a proper noun
    # in the summary that appears in no source, and a regeneration did not clear
    # it. Publishing that is publishing an invented specific. There is no trust
    # curve here: unlike an oversized merge (a judgement call that can be earned),
    # an ungrounded specific is a factual defect in THIS row.
    grounding = rc.get("_groundedness")
    if isinstance(grounding, dict) and grounding.get("flagged"):
        return False, "ungrounded_specifics"

    # Stage 2's deterministic casualty check found the source language and the
    # model's deaths/injuries disagreeing. A wrong death count is the most
    # damaging factual error this archive can publish, so it goes to a human.
    casualty = rc.get("_casualty_check")
    if isinstance(casualty, dict) and casualty.get("flagged"):
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

    _link_related(item, incident_id, client, run)
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

    updates, snapshot = _compute_merge(existing, new_url, source_name, headline, new_date)

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


def _link_related(item: dict, incident_id: str, client, run: AgentRun) -> None:
    rc = item.get("raw_content") or {}
    for link in (rc.get("agent_related_incidents") or []):
        if link.get("dismissed"):
            continue
        try:
            client.table("incident_links").insert({
                "incident_a": incident_id,
                "incident_b": link.get("incident_id"),
                "link_type": link.get("link_type") or "related",
                "confidence": link.get("confidence"),
                "agent_reason": link.get("reason") or "",
                # NOT confirmed_by_operator: no operator saw this. It stays an
                # agent proposal so the public page does not show it as verified.
            }).execute()
        except Exception as exc:                  # noqa: BLE001
            if "23505" not in str(exc):
                run.warn("link_insert_failed", f"{incident_id} -> {link.get('incident_id')}: {exc}")


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
             "needs_review": 0, "reasons": {}, "dry_run": dry_run}

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

        published_titles: list[str] = []
        merged_titles: list[str] = []
        for item in rows:
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
                    stats["merged"] += 1
                    merged_titles.append((item.get("proposed_title") or "")[:80])
                    arun.success("auto_merged",
                                 f"{(item.get('proposed_title') or '')[:70]} -> {merged_id} "
                                 f"(match={(item.get('raw_content') or {}).get('_match_confidence')})",
                                 incident_id=merged_id, queue_id=item.get("id"))
                else:
                    stats["failed"] += 1
                continue

            if stats["published"] >= MAX_AUTO_PUBLISH_PER_RUN:
                arun.anomaly("per_run_cap_hit",
                             f"stopped at {MAX_AUTO_PUBLISH_PER_RUN} auto-publishes; "
                             f"{len(rows) - stats['published']} row(s) left for review")
                break

            ok, reason = check_eligibility(item, threshold,
                                           oversized_merges_trusted=oversized_trusted)
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

            if not ok:
                stats["skipped"] += 1
                if reason not in ("below_threshold", "not_pending", "notification_row"):
                    # These are the interesting skips: a high-confidence row that
                    # a guardrail stopped. Worth a human's attention.
                    arun.warn("gate_blocked", f"{(item.get('proposed_title') or '')[:70]}: {reason}",
                              queue_id=item.get("id"), confidence=item.get("agent_confidence"))
                continue

            if dry_run:
                stats["published"] += 1
                published_titles.append((item.get("proposed_title") or "")[:80])
                arun.info("would_publish", f"[dry-run] {(item.get('proposed_title') or '')[:70]}")
                continue

            incident_id = _publish(item, client, arun, budget=art_budget)
            if incident_id:
                stats["published"] += 1
                published_titles.append((item.get("proposed_title") or "")[:80])
                arun.success("auto_published",
                             f"{(item.get('proposed_title') or '')[:70]} (conf={item.get('agent_confidence')})",
                             incident_id=incident_id, queue_id=item.get("id"))
            else:
                stats["failed"] += 1

        stats["needs_review"] = stats["skipped"] - stats["reasons"].get("notification_row", 0)
        arun.stat("published", stats["published"])
        arun.stat("merged", stats["merged"])
        arun.stat("needs_review", stats["needs_review"])
        arun.stat("skip_reasons", stats["reasons"])
        merge_note = f", auto-merged {stats['merged']}" if AUTO_MERGE_ENABLED else ""
        arun.set_summary(
            f"auto-published {stats['published']}{merge_note}, {stats['needs_review']} left for review "
            f"(threshold {threshold})"
        )

        if not dry_run:
            _notify_review_queue(rows, stats, threshold, published_titles, merged_titles, client)

    return stats


def _notify_schema_blocked(why: str, client) -> None:
    """Loud, because the symptom otherwise is 'autonomy silently did nothing'."""
    notify(
        "maintenance",
        "Yishun Again — auto-publish is OFF (migration 011 not applied)",
        ("Autonomous publishing did not run this pass.\n\n"
         f"Reason: {why}\n\n"
         "Nothing is lost: drafts are queueing normally and are waiting for you in\n"
         "the War Room. But nothing will auto-publish until migration 011 is applied.\n\n"
         "Apply packages/db/migrations/011_autonomy_ops_schema.sql by hand in the\n"
         "Supabase SQL Editor (there is no migration runner — QA M15), then the next\n"
         "14:58 SGT pass will pick it up with no redeploy needed."
         + footer()),
        dedup_key="schema_blocked:auto_publish",
        client=client,
    )


def _notify_review_queue(rows, stats, threshold, published_titles, merged_titles, client) -> None:
    """
    Req #4 — one email when cards below the threshold are waiting.

    Deduped per calendar day: the operator gets told once that there is work,
    not once per pass. Nothing waiting -> no email at all, so an empty inbox
    reliably means an empty queue.
    """
    pending_for_review = [
        r for r in rows
        if r.get("status") in ("pending", "update")
        and not (r.get("raw_content") or {}).get("notification_type")
        and (r.get("agent_confidence") is None or float(r.get("agent_confidence")) < threshold)
    ]
    if not pending_for_review:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"{len(pending_for_review)} card(s) are waiting for review in the War Room.",
        f"They scored below the {threshold:.0%} auto-publish threshold.",
        "",
    ]
    if published_titles:
        lines += [f"Auto-published without review this pass ({len(published_titles)}):"]
        lines += [f"  + {t}" for t in published_titles[:10]]
        lines += [""]

    # Merges mutate a LIVE incident, so surface them for a look — each is
    # undoable from the "Recently merged updates" panel in the War Room queue.
    if merged_titles:
        lines += [f"Auto-merged into existing incidents this pass ({len(merged_titles)}):"]
        lines += [f"  ~ {t}" for t in merged_titles[:10]]
        lines += ["  (undo any of these from the War Room queue's Recently-merged panel)", ""]

    lines.append("Waiting for you:")
    for r in sorted(pending_for_review,
                    key=lambda x: -(float(x.get("agent_confidence") or 0)))[:20]:
        conf = r.get("agent_confidence")
        conf_s = f"{float(conf):.0%}" if conf is not None else " ?  "
        kind = "UPDATE" if r.get("status") == "update" else "NEW   "
        lines.append(f"  [{conf_s:>4}] {kind} {(r.get('proposed_title') or '(untitled)')[:70]}")
    if len(pending_for_review) > 20:
        lines.append(f"  ... and {len(pending_for_review) - 20} more")

    blocked = {k: v for k, v in stats["reasons"].items()
               if k not in ("below_threshold", "not_pending", "notification_row")}
    if blocked:
        lines += ["", "High-confidence cards a guardrail held back:"]
        lines += [f"  {k}: {v}" for k, v in sorted(blocked.items())]

    lines.append(f"\nReview: {war_room_url('/queue')}")

    notify(
        "review_queue",
        f"Yishun Again — {len(pending_for_review)} card(s) need review",
        "\n".join(lines) + footer(),
        dedup_key=f"review_queue:{today}",
        client=client,
    )
