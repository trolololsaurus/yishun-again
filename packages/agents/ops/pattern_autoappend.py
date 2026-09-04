"""
Confidence-gated auto-append of new incidents to curated patterns.

A curated `patterns` row (see migration 022) links a hand-picked set of
incidents behind an authored thesis. New incidents that clearly belong to an
existing pattern should join it without the operator hand-editing an array every
time — so this agent, once per daily pass, scores recently-published incidents
against each published pattern with one Haiku call and appends any that clear the
confidence gate (default 0.85).

Every append is REVERSIBLE and AUDITED:
  - the id is added to both `incident_ids` (what the page renders) and
    `auto_added_incident_ids` (so the War Room can flag it for review);
  - a `training_signals` row (action='pattern_auto_append', decided_by='agent')
    records the decision;
  - the operator's undo (War Room) removes the id and lists it in
    `excluded_incident_ids`, which this agent treats as a permanent "never
    re-add" — so a reversed decision does not come back the next pass.

Bounds on cost: only incidents published within PATTERN_AUTO_APPEND_LOOKBACK_DAYS
(default 3 — the twice-daily cadence means a new incident is still seen a couple
of times) are considered, and PATTERN_AUTO_APPEND_MAX_CHECKS (default 60) caps
the Haiku calls per pass. An already-appended or already-excluded incident is
never re-scored.

ops/ contract: this module never raises. Any failure degrades to logging and the
pass continues.

Public API
----------
run(supabase_client=None, *, dry_run=False, trigger="manual") -> dict
    {enabled, patterns_checked, candidates_scored, appended, skipped, errors}
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

AGENT = "pattern_autoappend"
MATCH_MODEL = "claude-haiku-4-5-20251001"


def _env(name: str, default, cast=str):
    """One env-var reader for the flag/float/int settings below.

    cast=bool reads a truthy word-set (unset -> default); any other cast
    (float, int) parses the raw string and falls back to default on error.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    if cast is bool:
        return raw.strip().lower() in ("true", "1", "yes", "on")
    try:
        return cast(raw)
    except ValueError:
        return default


def _client(explicit=None):
    if explicit is not None:
        return explicit
    from classifiers.corroboration import get_supabase_client
    return get_supabase_client()


_MATCH_SYSTEM = """\
You judge whether a NEW Yishun incident belongs to a CURATED editorial pattern.

You are given the pattern's title, its thesis (what unifies its incidents), a few
example incident titles already in it, and one candidate incident. Decide how
strongly the candidate fits the SAME specific theme — not merely "also happened
in Yishun".

Be strict: the whole archive is Yishun incidents, so shared location alone is not
a match. A match means the candidate is the same KIND of story the thesis is
about (e.g. a cat killing for a cat-killings pattern; a death on that specific
road for a road pattern; the same named person for a person pattern).

Return JSON only: {"confidence": 0.0-1.0, "reason": "one short sentence"}.
"""


def _match_confidence(client, pattern: dict, examples: list[str],
                      incident: dict) -> tuple[float, str]:
    """One Haiku call → (confidence 0..1, reason). Never raises; returns 0 on error."""
    ex = "\n".join(f"- {t}" for t in examples[:6]) or "(none)"
    user = (
        f"PATTERN TITLE: {pattern.get('title', '')}\n"
        f"PATTERN THESIS: {pattern.get('thesis', '')}\n\n"
        f"EXAMPLE INCIDENTS ALREADY IN THIS PATTERN:\n{ex}\n\n"
        f"CANDIDATE INCIDENT\n"
        f"Title: {incident.get('title', '')}\n"
        f"Summary: {(incident.get('summary') or '')[:600]}"
    )
    try:
        resp = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=200,
            temperature=0.0,
            system=_MATCH_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
        data = json.loads(raw)
        conf = float(data.get("confidence", 0.0))
        conf = max(0.0, min(1.0, conf))
        return conf, str(data.get("reason", ""))[:200]
    except Exception as exc:                          # noqa: BLE001
        logger.debug("autoappend match failed (%s): %s", incident.get("id"), exc)
        return 0.0, ""


def run(supabase_client=None, *, dry_run: bool = False, trigger: str = "manual") -> dict:
    """Score recent incidents against published patterns; append clear matches."""
    stats = {"enabled": True, "patterns_checked": 0, "candidates_scored": 0,
             "appended": 0, "skipped": 0, "errors": 0}

    if not _env("PATTERN_AUTO_APPEND_ENABLED", True, bool):
        return {**stats, "enabled": False, "skipped": "PATTERN_AUTO_APPEND_ENABLED is off"}

    min_conf   = _env("PATTERN_AUTO_APPEND_CONFIDENCE", 0.85, float)
    lookback   = _env("PATTERN_AUTO_APPEND_LOOKBACK_DAYS", 3, int)
    max_checks = _env("PATTERN_AUTO_APPEND_MAX_CHECKS", 60, int)

    try:
        sb = _client(supabase_client)
    except Exception as exc:                          # noqa: BLE001
        logger.error("autoappend: Supabase unavailable: %s", exc)
        return {**stats, "errors": 1}

    from ops.activity import AgentRun

    try:
        anthropic_client = _anthropic()
    except Exception as exc:                          # noqa: BLE001
        logger.warning("autoappend: Anthropic unavailable, nothing scored: %s", exc)
        return {**stats, "skipped": "no ANTHROPIC_API_KEY"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()
    appended_ids: list[str] = []

    with AgentRun(AGENT, trigger=trigger, client=sb) as run_:
        run_.stat("dry_run", dry_run)
        run_.stat("min_confidence", min_conf)

        try:
            patterns = (sb.table("patterns")
                        .select("id,slug,title,thesis,incident_ids,auto_added_incident_ids,excluded_incident_ids")
                        .eq("published", True).execute().data or [])
        except Exception as exc:                      # noqa: BLE001
            run_.error_("patterns_fetch_failed", str(exc))
            return {**stats, "errors": 1}

        try:
            candidates = (sb.table("incidents")
                          .select("id,title,summary")
                          .eq("is_published", True)
                          .gte("published_at", cutoff)
                          .order("published_at", desc=True)
                          .execute().data or [])
        except Exception as exc:                      # noqa: BLE001
            run_.error_("candidates_fetch_failed", str(exc))
            return {**stats, "errors": 1}

        checks = 0
        for pat in patterns:
            stats["patterns_checked"] += 1
            existing = set(pat.get("incident_ids") or [])
            excluded = set(pat.get("excluded_incident_ids") or [])
            examples = [c["title"] for c in candidates if c["id"] in existing][:6]
            # Example titles from the archive, not only the recent window.
            if len(examples) < 3 and existing:
                try:
                    ex_rows = (sb.table("incidents").select("title")
                               .in_("id", list(existing)[:6]).execute().data or [])
                    examples = [r["title"] for r in ex_rows]
                except Exception:                     # noqa: BLE001
                    pass

            for cand in candidates:
                cid = cand["id"]
                if cid in existing or cid in excluded:
                    continue
                if checks >= max_checks:
                    run_.warn("check_cap_reached",
                              f"stopped at {max_checks} match calls (raise PATTERN_AUTO_APPEND_MAX_CHECKS)")
                    break
                checks += 1
                stats["candidates_scored"] += 1
                conf, reason = _match_confidence(anthropic_client, pat, examples, cand)
                if conf < min_conf:
                    continue

                if dry_run:
                    run_.info("would_append",
                              f"{cand['title'][:60]} -> {pat['slug']} ({conf:.2f})")
                    stats["appended"] += 1
                    appended_ids.append(cid)
                    continue

                if _append(sb, pat, cid, conf, reason, run_):
                    stats["appended"] += 1
                    appended_ids.append(cid)
                    existing.add(cid)   # don't re-score this cid again for this pattern
                else:
                    stats["errors"] += 1
            if checks >= max_checks:
                break

        run_.set_summary(
            f"{stats['appended']} appended across {stats['patterns_checked']} pattern(s), "
            f"{stats['candidates_scored']} scored"
        )
        for k, v in stats.items():
            run_.stat(k, v)

    _notify_if_appended(sb, appended_ids, dry_run)
    return stats


def _append(sb, pattern: dict, incident_id: str, conf: float, reason: str, run_) -> bool:
    """Add incident to a pattern's incident_ids + auto_added_incident_ids; log the signal.

    ponytail: read-modify-write on the arrays, no row lock. Safe because this is
    the only autonomous writer and it runs single-threaded once per pass; the
    only competitor is a rare concurrent War Room edit. Move to a DB-side
    array_append RPC if that ever becomes contended.
    """
    pid = pattern["id"]
    new_ids   = list(dict.fromkeys([*(pattern.get("incident_ids") or []), incident_id]))
    new_auto  = list(dict.fromkeys([*(pattern.get("auto_added_incident_ids") or []), incident_id]))
    try:
        sb.table("patterns").update({
            "incident_ids": new_ids,
            "auto_added_incident_ids": new_auto,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", pid).execute()
    except Exception as exc:                          # noqa: BLE001
        run_.error_("append_failed", f"{pattern['slug']} <- {incident_id}: {exc}")
        return False

    # keep the in-memory pattern consistent for any later append in this pass
    pattern["incident_ids"] = new_ids
    pattern["auto_added_incident_ids"] = new_auto

    try:
        sb.table("training_signals").insert({
            "incident_id": incident_id,
            "action": "pattern_auto_append",
            "decision": "auto_approve",
            "decided_by": "agent",
            "agent_confidence": conf,
            "agent_confidence_was": conf,
            "operator_note": f"Auto-appended to pattern '{pattern['slug']}' (match {conf:.2f}). {reason}",
        }).execute()
    except Exception as exc:                          # noqa: BLE001
        # The append already happened and is reversible in the War Room; losing
        # the signal is a metric gap, not a data loss. Log, don't fail.
        run_.warn("append_signal_failed", f"{pattern['slug']} <- {incident_id}: {exc}")

    run_.success("appended", f"{pattern['slug']} <- {incident_id} ({conf:.2f})")
    return True


def _notify_if_appended(sb, appended_ids: list[str], dry_run: bool) -> None:
    if not appended_ids or dry_run:
        return
    try:
        from ops.notify import notify
        sig = ",".join(sorted(appended_ids))[:400]
        notify(
            "anomaly",
            f"{len(appended_ids)} incident(s) auto-appended to patterns",
            "The pattern auto-append agent added incidents to curated patterns. "
            "Review under 'Recently auto-added' in the War Room and undo any that "
            "do not belong.",
            dedup_key=f"pattern_autoappend:{sig}",
            throttle_minutes=1440,
            client=sb,
        )
    except Exception as exc:                          # noqa: BLE001
        logger.debug("autoappend notify failed: %s", exc)


def _anthropic():
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set")
    return anthropic.Anthropic(api_key=api_key)
