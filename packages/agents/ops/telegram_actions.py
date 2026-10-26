"""
Telegram inline-button action handlers.

Called by POST /telegram/webhook in main.py when an operator taps an action
button on a per-card Telegram message. Dispatches on `callback_data`:

  approve:{queue_id}                 → publish the draft as-is
  reject:{queue_id}                  → show reject-reason sub-keyboard
  reject_reason:{queue_id}:{reason}  → reject with chosen reason
  reject_back:{queue_id}             → restore three-button keyboard
  keep:{queue_id}                    → dismiss buttons, leave card pending

Each handler answers the callback query first (dismisses Telegram's spinner),
then does its work. Never raises — exceptions edit the card to an error state.

Auth note: the War Room approve/reject routes are behind Cloudflare Access JWT
and cannot be called from here. Instead, these handlers write directly to
Supabase using the same secret key and the same logic as auto_publish._publish()
and the War Room routes (same CAS patterns, same training_signals schema).
"""

import logging
import re
from datetime import datetime, timezone

from ops.notify import (
    answer_callback, edit_card, reject_reason_menu, restore_card_buttons,
)

logger = logging.getLogger(__name__)

# Validate queue_id before any DB query. callback_data is attacker-controlled
# once the Telegram message is sent (a leaked bot token could forge it).
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)

# Valid reject reasons — must match migration 017's CHECK constraint on
# training_signals.reject_reason and war_room_queue's own allowlist.
_VALID_REJECT_REASONS = {
    "noise", "duplicate", "unverified", "too_thin", "legal_risk", "not_yishun",
}


def handle_callback(payload: dict, client) -> None:
    """
    Dispatch one Telegram callback_query. `payload` is the full update dict from
    the Telegram webhook. Never raises — caller returns 200 regardless.
    """
    cq = payload.get("callback_query") or {}
    cb_id = cq.get("id", "")
    data = (cq.get("data") or "").strip()
    message = cq.get("message") or {}
    message_id = str(message.get("message_id", ""))

    if not data:
        return

    parts = data.split(":", 2)
    action = parts[0]

    # Dismiss the Telegram spinner immediately — Telegram retries if it doesn't
    # get an answer within ~30s, which would re-invoke the webhook.
    answer_callback(cb_id)

    # Validate the queue_id before any action — callback_data is attacker-controlled.
    queue_id = parts[1] if len(parts) >= 2 else ""
    if action in ("approve", "reject", "reject_reason", "reject_back", "keep"):
        if not _UUID_RE.match(queue_id):
            logger.warning("telegram_actions: invalid queue_id %r in %r — ignoring", queue_id, data)
            return

    try:
        if action == "approve":
            _approve(queue_id, client)
        elif action == "reject":
            reject_reason_menu(queue_id, message_id, client)
        elif action == "reject_reason" and len(parts) == 3:
            _reject_reason(queue_id, parts[2], client)
        elif action == "reject_back":
            restore_card_buttons(queue_id, message_id, client)
        elif action == "keep":
            _keep(queue_id, client)
        else:
            logger.warning("telegram_actions: unrecognised callback_data %r", data)
    except Exception as exc:                        # noqa: BLE001
        # Log the full exception server-side; send a generic message to Telegram
        # so internal details (table names, constraint names) are not exposed.
        logger.error("telegram_actions: unhandled exception for %r: %s", data, exc)
        edit_card(queue_id, "⚠️ Error processing action — check Cloud Run logs.", client)


# ── Action handlers ──────────────────────────────────────────────────────────

def _approve(queue_id: str, client) -> None:
    """Publish the draft as-is via the same path as autonomous auto-publish."""
    from ops.activity import AgentRun
    from ops.auto_publish import _publish, check_eligibility

    # Fetch the queue row.
    try:
        res = (client.table("war_room_queue")
               .select("*")
               .eq("id", queue_id)
               .eq("status", "pending")
               .limit(1).execute())
        items = res.data or []
    except Exception as exc:                        # noqa: BLE001
        logger.error("_approve: fetch failed for %s: %s", queue_id, exc)
        edit_card(queue_id, "⚠️ Could not fetch queue item — check the War Room.", client)
        return

    if not items:
        # Race: operator acted in the War Room between tap and handler.
        edit_card(queue_id, "⚠️ Already actioned in the War Room.", client)
        return

    item = items[0]

    # Check the basic eligibility gates (guardrails, source validity, date).
    # threshold=0.0 lets the operator override the confidence gate.
    # duplicate_guard_incidents=[] runs the guard with an empty archive — the
    # operator's tap is the explicit override, but we still check legal guardrails.
    ok, reason = check_eligibility(
        item, threshold=0.0,
        oversized_merges_trusted=True,
        duplicate_guard_incidents=[],
        duplicate_guard_available=True,
    )
    # Legal guardrails (#1 source, #4 political, date) are hard blocks even for
    # an operator. Confidence, duplicate and oversized are operator overrides.
    # Any future new gate defaults to hard-blocked (safe) until explicitly added here.
    operator_overrides = {
        "eligible", "below_threshold", "possible_published_duplicate",
        "oversized_cluster_unproven", "not_processed_this_pass",
    }
    if reason not in operator_overrides:
        from ops.auto_publish import _human_hold_reason
        edit_card(queue_id,
                  f"⚠️ Cannot approve: {_human_hold_reason(reason)}\n"
                  "Go to the War Room to resolve this.", client)
        return

    # CAS-claim the row before calling _publish to prevent a double-approve race
    # (operator taps twice before the first handler edits the message).
    try:
        claimed = (client.table("war_room_queue")
                   .update({"status": "approved_pending"})
                   .eq("id", queue_id).eq("status", "pending")
                   .select("id").execute())
    except Exception as exc:                        # noqa: BLE001
        logger.error("_approve: claim failed for %s: %s", queue_id, exc)
        edit_card(queue_id, "⚠️ Could not claim queue item — check the War Room.", client)
        return

    if not claimed.data:
        # Another handler (or War Room) beat us to it.
        edit_card(queue_id, "⚠️ Already actioned in the War Room.", client)
        return

    with AgentRun("telegram_approve", trigger="telegram") as run:
        incident_id = _publish(item, client, run)

    if incident_id:
        edit_card(queue_id, "✅ Approved via Telegram", client)
        logger.info("telegram_actions: approved queue %s → incident %s", queue_id, incident_id)
    else:
        edit_card(queue_id, "⚠️ Publish failed — check the War Room logs.", client)


def _reject_reason(queue_id: str, reason: str, client) -> None:
    """Reject the queue item with the chosen reason."""
    if reason not in _VALID_REJECT_REASONS:
        logger.warning("telegram_actions: invalid reject reason %r for %s", reason, queue_id)
        edit_card(queue_id, f"⚠️ Unknown reject reason: {reason!r}", client)
        return

    # CAS on status='pending' so a concurrent War Room action wins cleanly.
    try:
        claimed = (client.table("war_room_queue")
                   .update({
                       "status": "rejected",
                       "processed_at": datetime.now(timezone.utc).isoformat(),
                   })
                   .eq("id", queue_id)
                   .eq("status", "pending")
                   .select("id,proposed_classification,proposed_severity,"
                           "agent_confidence,source_url,source_type,proposed_summary")
                   .execute())
        row = (claimed.data or [None])[0]
    except Exception as exc:                        # noqa: BLE001
        logger.error("_reject_reason: update failed for %s: %s", queue_id, exc)
        edit_card(queue_id, "⚠️ Reject failed — try from the War Room.", client)
        return

    if not row:
        edit_card(queue_id, "⚠️ Already actioned in the War Room.", client)
        return

    # Training signal — same schema as War Room reject route.
    try:
        client.table("training_signals").insert({
            "queue_id":               queue_id,
            "action":                 "reject",
            "decision":               "reject",
            "decided_by":             "operator",
            "source_url":             row.get("source_url"),
            "source_type":            row.get("source_type"),
            "proposed_classification": row.get("proposed_classification"),
            "proposed_severity":      row.get("proposed_severity"),
            "original_draft":         row.get("proposed_summary"),
            "agent_confidence":       row.get("agent_confidence"),
            "operator_note":          f"Rejected via Telegram: {reason}",
        }).execute()
    except Exception as exc:                        # noqa: BLE001
        logger.warning("_reject_reason: training signal failed for %s: %s", queue_id, exc)

    reason_label = reason.replace("_", " ")
    edit_card(queue_id, f"❌ Rejected via Telegram ({reason_label})", client)
    logger.info("telegram_actions: rejected queue %s (reason=%s)", queue_id, reason)


def _keep(queue_id: str, client) -> None:
    """Dismiss the buttons. No DB change — card stays pending in the queue."""
    edit_card(queue_id, "📋 Kept in War Room — will re-notify if the hold reason changes.", client)
    logger.info("telegram_actions: kept queue %s", queue_id)
