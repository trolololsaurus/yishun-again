"""
Outbound operator alerting, via Telegram (reqs #4, #9, #11, #12).

    from ops.notify import notify
    notify("review_queue", "3 cards need review", body, dedup_key="review:2026-07-21")

Transport is the Telegram Bot API (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) — swapped
in 2026-08-27 for the original Resend/email transport, which is why this module's
shape (ledger-first, dedup, throttle, degrade-don't-block) still reads like an email
sender: those properties are transport-agnostic and were kept as-is. Only
`_sending_enabled()` and the actual send call changed. Everything else about this
module exists to make unattended alerting survivable:

  * LEDGER FIRST. The notifications row is written before the send is attempted,
    so a provider outage loses the alert content, not the alert.
  * DEDUP + THROTTLE. Same dedup_key inside its window is recorded 'suppressed'
    rather than re-sent. An agent that discovers the same broken scraper on
    every pass must not message 48 times a day — the operator would mute the
    whole chat, and then the ONE alert that mattered is invisible too.
  * DEGRADES, NEVER BLOCKS. No bot token configured -> status 'disabled', still
    recorded, still visible in War Room. notify() never raises.

Sent with parse_mode=HTML: the subject leads as a BOLD headline so an alert is
scannable rather than a wall of monospace, and the body is HTML-escaped (a scraped
title with `<`/`&` can't break the markup). If the HTML send is ever rejected it
falls back to plain text — the alerting path must not go silent over formatting.
Telegram caps a message at 4096 UTF-8 characters; a longer body is truncated with a
marker rather than failing the send (same "degrade, don't block" rule).

Env:
    TELEGRAM_BOT_TOKEN  transport credential; absent => disabled (recorded only)
    TELEGRAM_CHAT_ID    recipient chat/user id (required for a real send)
    NOTIFY_ENABLED      set to "false" to mute sending without removing the token
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from ops.activity import _client

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Telegram's own hard cap on message length (UTF-8 code units). A body over this
# is truncated with a marker rather than dropped — the ledger already has the
# full text for anyone reading War Room / `notifications` directly.
TELEGRAM_MAX_CHARS = 4096
_TRUNCATION_MARKER = "\n\n… [truncated — full text in the notifications table]"

# Per-kind throttle. Deliberately asymmetric: a maintenance digest once a day is
# plenty, but a health alarm suppressed for 24h could hide a real outage.
DEFAULT_THROTTLE_MINUTES = {
    "review_queue":   180,      # at most every 3h — new cards are not urgent
    "anomaly":         60,
    "maintenance":   1440,      # daily digest
    "health":          60,
    "monthly_report":   -1,     # never throttled; one a month by construction
    "test":             -1,
}

# Muted alert classes — RECORDED to the notifications ledger but never pushed.
# Matched against the alert's dedup_key by PREFIX, not by `kind`, because the
# noisy classes share kind='anomaly'/'maintenance' with alerts that must still
# push: integrity (`integrity:`) and the maintenance digest (`maintenance:`) are
# standing-state re-announcements the operator reads on their own schedule, and a
# political flag (`political:`) is already handled by guardrail #4 before it fires
# — while `supervisor:` (fleet outage), `schema_blocked:` (migration missing) and
# `review_queue:`/`health:` stay push-worthy. Prefix-matching keeps the digest
# muted while `schema_blocked:auto_publish` (also kind='maintenance') still pushes.
# Env-overridable so the push/pull line moves without a redeploy; empty = mute
# nothing. Nothing is lost — the ledger row and the underlying agent_events /
# War Room health views remain, only the phone buzz is suppressed.
MUTED_PREFIXES = tuple(
    p.strip() for p in os.getenv(
        "NOTIFY_MUTED_PREFIXES", "integrity:,maintenance:,political:"
    ).split(",") if p.strip()
)


def _is_muted(dedup_key: str) -> bool:
    return bool(MUTED_PREFIXES) and dedup_key.startswith(MUTED_PREFIXES)


def operator_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def _sending_enabled() -> tuple[bool, str]:
    """(enabled, reason_if_not)."""
    if os.getenv("NOTIFY_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        return False, "NOTIFY_ENABLED=false"
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        return False, "TELEGRAM_BOT_TOKEN not configured"
    if not operator_chat_id():
        return False, "TELEGRAM_CHAT_ID not configured"
    return True, ""


def _recently_sent(dedup_key: str, minutes: int, client) -> bool:
    """True if this key was actually SENT inside the window. Fails open."""
    if not client or minutes <= 0:
        return False
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    try:
        res = (client.table("notifications")
               .select("id")
               .eq("dedup_key", dedup_key)
               .eq("status", "sent")
               .gte("created_at", since)
               .limit(1).execute())
        return bool(res.data)
    except Exception as exc:                      # noqa: BLE001
        # Fail OPEN: if we cannot tell, send. A duplicate alert is a nuisance;
        # a swallowed alert is an outage nobody hears about.
        logger.warning("notify: dedup check failed (%s) — sending anyway", exc)
        return False


def notify(kind: str, subject: str, body: str, *,
           dedup_key: str | None = None,
           throttle_minutes: int | None = None,
           recipient: str | None = None,
           client=None) -> dict:
    """
    Record and (if configured) send one operator Telegram message.

    Returns {"status": sent|suppressed|disabled|failed|pending, "id": ..., "error": ...}.
    Never raises — callers are agents that must finish their pass regardless.
    """
    if kind not in DEFAULT_THROTTLE_MINUTES:
        kind = "anomaly"
    to = (recipient or operator_chat_id()).strip()
    key = dedup_key or f"{kind}:{subject}"
    window = DEFAULT_THROTTLE_MINUTES[kind] if throttle_minutes is None else throttle_minutes

    c = _client(client)
    subject = subject[:200]

    # 0. Muted class: record to the ledger but do not push. Checked BEFORE
    # throttle/transport — the outcome (recorded, not sent) is the same whatever
    # they'd say. Stored as 'disabled' (the notifications status CHECK has no
    # 'muted' value and this needs no migration); the `error` names it as a mute
    # so it is distinguishable from a transport-off 'disabled'. The returned
    # status is 'muted' so callers' logs read true.
    if _is_muted(key):
        logger.info("notify: muted (%s) — recorded only, not pushed", key)
        row_id = _record(c, kind, key, to or "(unset)", subject, body, "disabled",
                         error="muted: routed to War Room only (NOTIFY_MUTED_PREFIXES)")
        return {"status": "muted", "id": row_id, "error": None}

    # 1. Throttle check
    if _recently_sent(key, window, c):
        logger.info("notify: suppressed (dedup=%s, window=%dm)", key, window)
        _record(c, kind, key, to, subject, body, "suppressed")
        return {"status": "suppressed", "id": None, "error": None}

    # 2. Transport availability
    enabled, why = _sending_enabled()
    if not enabled:
        logger.warning("notify: sending disabled (%s) — recorded only: %s", why, subject)
        row_id = _record(c, kind, key, to or "(unset)", subject, body, "disabled", error=why)
        return {"status": "disabled", "id": row_id, "error": why}

    # 3. Ledger first, then send
    row_id = _record(c, kind, key, to, subject, body, "pending")

    try:
        provider_id = _send_telegram(to, subject, body)
    except Exception as exc:                      # noqa: BLE001
        logger.error("notify: send failed for %r: %s", subject, exc)
        _update(c, row_id, {"status": "failed", "error": str(exc)[:1000]})
        return {"status": "failed", "id": row_id, "error": str(exc)}

    logger.info("notify: sent %r to %s (provider_id=%s)", subject, to, provider_id)
    _update(c, row_id, {
        "status": "sent",
        "provider_id": provider_id,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "sent", "id": row_id, "error": None}


def _send_telegram(to: str, subject: str, body: str) -> str | None:
    """
    POST to the Telegram Bot API's sendMessage. Raises on failure so notify()
    can record it.

    Telegram has no subject field, so the subject leads the message as a BOLD
    first line (parse_mode=HTML) — the single biggest readability win, because it
    gives every alert a scannable headline instead of an undifferentiated wall.
    The body is escaped so a scraped headline containing `<`/`&` can't break the
    markup, and the raw body is truncated to Telegram's 4096-char cap BEFORE
    escaping so a cut never splits an HTML entity. If the HTML send is rejected
    for any reason, it falls back to a plain-text send — the alerting path must
    never be the thing that goes silent.
    """
    import httpx
    from html import escape

    # .strip(): a secret provisioned by piping a string through a shell to
    # `gcloud secrets create` can pick up a trailing CRLF the shell appends on
    # the way to the native process. Telegram's endpoint takes the token in the
    # URL PATH, so an unstripped trailing \r surfaces as "Invalid non-printable
    # ASCII character in URL" rather than an auth error — worth guarding here
    # rather than trusting every future secret-provisioning path to be clean.
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    # Truncate the raw body first (headroom for the bold tags, the "subject\n\n"
    # prefix and the marker), so escaping afterwards can't produce a half-entity.
    max_body = TELEGRAM_MAX_CHARS - len(subject) - 60
    if len(body) > max_body:
        body = body[:max_body - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER

    def _post(payload: dict):
        return httpx.post(url, json={"chat_id": to, **payload}, timeout=20.0)

    resp = _post({"text": f"<b>{escape(subject)}</b>\n\n{escape(body)}",
                  "parse_mode": "HTML"})
    if resp.status_code >= 300:
        logger.warning("notify: Telegram HTML send failed (%s) — retrying as plain text: %s",
                       resp.status_code, resp.text[:200])
        resp = _post({"text": f"{subject}\n\n{body}"})

    if resp.status_code >= 300:
        raise RuntimeError(f"Telegram HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        return str(resp.json()["result"]["message_id"])
    except Exception:                             # noqa: BLE001
        return None


def _record(client, kind, dedup_key, recipient, subject, body, status, error=None) -> str | None:
    if not client:
        return None
    try:
        res = client.table("notifications").insert({
            "kind": kind, "dedup_key": dedup_key[:500], "recipient": recipient,
            "subject": subject, "body": body[:50000], "status": status, "error": error,
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as exc:                      # noqa: BLE001
        logger.warning("notify: could not record notification: %s", exc)
        return None


def _update(client, row_id, patch: dict) -> None:
    if not client or not row_id:
        return
    try:
        client.table("notifications").update(patch).eq("id", row_id).execute()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("notify: could not update notification %s: %s", row_id, exc)


# ── shared formatting ──────────────────────────────────────────────────────

def war_room_url(path: str = "/queue") -> str:
    base = os.getenv("WAR_ROOM_URL", "https://warroom.yishunagain.com").rstrip("/")
    return f"{base}{path}"


def footer() -> str:
    # Telegram, not email — one actionable link, no signature block. The old
    # footer carried three lines of email boilerplate ending in a mute tip that
    # is now wrong (NOTIFY_ENABLED silences EVERYTHING; per-class muting is
    # NOTIFY_MUTED_PREFIXES). A link is the only part the operator acts on.
    return f"\n\n🔗 War Room: {war_room_url()}"
