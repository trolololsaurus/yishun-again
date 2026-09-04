"""
Is the model learning, or stagnant? (req #5)

Two jobs, run daily after the pass:

  1. REBUILD `source_reputation` from operator decisions — the write side of the
     Learning Loop's accumulator (LEARNING_LOOP.md §2.2).
  2. SNAPSHOT confidence and agreement, and compare against the previous window
     to produce a signed DELTA and a verdict.

## Why job 1 lives here

`ingestion/learning.py` reads `source_reputation` every pass and nudges
candidate confidence by +/-0.10 based on `trust_score`. Nothing in the
repository has ever WRITTEN that table. Every domain therefore resolved to the
0.500 default, both thresholds (0.700 / 0.300) were unreachable, and the nudge
was a permanent no-op: the loop in LEARNING_LOOP.md §5 was drawn closed but was
open in code. Operator decisions accumulated in `training_signals` and were
read back only as prompt text, never as per-domain trust.

Without this rebuild the delta tracker below would be honest but useless — it
would correctly report "stagnant" forever, because nothing was feeding back.

Trust formula is the simple ratio recommended in LEARNING_LOOP.md Q-L2, with
Laplace smoothing so one rejection cannot send a brand-new domain to 0.000:

    trust = (approvals + 1) / (approvals + rejections + 2)

Smoothing also means a domain needs sustained agreement to clear 0.700 (about
5 clean approvals) and sustained failure to fall under 0.300 — the thresholds
become earned, not noise.

## Why the agreement rate is the real score, not confidence

Mean confidence is what the model CLAIMS. Agreement rate is what the operator
CONFIRMED. A model can drift to high confidence while getting worse; only the
delta between claim and confirmation catches that. So `verdict` keys off
agreement, and `mean_confidence` is reported alongside as the calibration
check: confidence rising while agreement falls is the over-confidence signature,
and is reported as `regressing` even if agreement is flat.

Auto-approvals (`decided_by='agent'`) are excluded from the agreement maths.
Counting the agent's own decisions as agreement would peg the rate at 100% the
day autonomy switched on and hide any real regression underneath.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ops.activity import AgentRun, _client, agent_enabled

logger = logging.getLogger(__name__)

AGENT = "learning_monitor"

WINDOW_DAYS = int(os.getenv("LEARNING_WINDOW_DAYS", "30"))

# Below this many operator decisions in a window, any rate is noise. 20 is not
# statistical rigour — it is the point below which a single decision moves the
# rate by >5%, which is the size of the "learning" signal itself.
MIN_SAMPLES = int(os.getenv("LEARNING_MIN_SAMPLES", "20"))

# Noise floor for the verdict. Agreement wobbles a couple of points between any
# two windows; only a move larger than this means anything.
LEARNING_DELTA = 0.02
REGRESSION_DELTA = -0.05

# training_signals.decision values that count as "the operator agreed with the
# agent, unchanged". approve_with_edits deliberately does NOT count: an edit is
# a correction, and treating corrections as agreement is how a metric flatters
# itself into uselessness.
AGREE_DECISIONS = {"approve"}
EDIT_DECISIONS = {"approve_with_edits"}
REJECT_DECISIONS = {"reject"}

# Minimum verdicts before a domain's trust is allowed to move off neutral.
# See the reasoning at the clamp in rebuild_source_reputation().
MIN_DOMAIN_OBSERVATIONS = int(os.getenv("REPUTATION_MIN_OBSERVATIONS", "10"))
DEFAULT_TRUST = 0.500

# Share of unchanged approvals above which an unmarked window is treated as
# unreadable rather than excellent (QA A11). 0.75 is deliberately high: a good
# model genuinely should produce mostly clean approvals, so this must only fire
# when the reading is extreme enough to be ambiguous.
UNMARKED_BULK_SUSPICION = float(os.getenv("UNMARKED_BULK_SUSPICION", "0.75"))


def _domain(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else (host or None)
    except Exception:                             # noqa: BLE001
        return None


# ── Job 1: rebuild source_reputation ────────────────────────────────────────

def rebuild_source_reputation(client, run: AgentRun, lookback_days: int = 365) -> dict:
    """
    Recompute per-domain trust from operator decisions and upsert.

    Full recompute rather than incremental counters: it is idempotent, it
    self-heals after a missed run, and at this data volume (thousands of rows,
    once a day) the cost is irrelevant next to the class of bug incremental
    counters invite — double-counting on a retry, drifting forever after.
    """
    stats = {"domains": 0, "updated": 0, "errors": 0}
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        res = (client.table("training_signals")
               .select("source_url,decision,decided_by,operator_added_source")
               .gte("created_at", since)
               .limit(10000).execute())
        rows = res.data or []
    except Exception as exc:                      # noqa: BLE001
        run.error_("reputation_fetch_failed", str(exc))
        stats["errors"] += 1
        return stats

    tally: dict[str, dict] = {}
    for row in rows:
        # Only human verdicts shape trust. An agent auto-approval reflects the
        # agent's own confidence, so counting it would let a domain bootstrap
        # its own reputation and then use that reputation to clear the bar.
        if row.get("decided_by") == "agent":
            continue
        dom = _domain(row.get("source_url") or "")
        if not dom:
            continue
        t = tally.setdefault(dom, {"approvals": 0, "rejections": 0, "re_source_wins": 0})
        decision = row.get("decision")
        if decision in AGREE_DECISIONS or decision in EDIT_DECISIONS:
            t["approvals"] += 1
        elif decision in REJECT_DECISIONS:
            t["rejections"] += 1

        # The operator finding a better source is that domain's strongest
        # endorsement — a human went looking and chose it (LEARNING_LOOP.md §2.1).
        win_dom = _domain(row.get("operator_added_source") or "")
        if win_dom:
            w = tally.setdefault(win_dom, {"approvals": 0, "rejections": 0, "re_source_wins": 0})
            w["re_source_wins"] += 1
            w["approvals"] += 1

    now_iso = datetime.now(timezone.utc).isoformat()
    for dom, t in tally.items():
        observations = t["approvals"] + t["rejections"]

        # Stay NEUTRAL until a domain has been seen enough times.
        #
        # This is a safety gate, not statistical fussiness. trust >= 0.700 buys a
        # +0.10 confidence nudge in ingestion/learning.py, and auto-publish fires
        # at 0.95 — so an inflated trust score can push a 0.86 draft over the
        # autonomy gate. Laplace smoothing alone does not protect against that:
        # a domain with 3 approvals and 0 rejections scores 0.800 and clears the
        # boost threshold on almost no evidence.
        #
        # Rejections are also structurally scarcer than approvals right now
        # (a rejected draft has no incident_id to trace a domain through), so
        # early data skews positive by construction. Neutral-until-proven is the
        # conservative direction: the worst case is the nudge does nothing, which
        # is exactly where the system has been all along.
        if observations < MIN_DOMAIN_OBSERVATIONS:
            trust = DEFAULT_TRUST
        else:
            trust = (t["approvals"] + 1) / (t["approvals"] + t["rejections"] + 2)
        try:
            client.table("source_reputation").upsert({
                "source_domain": dom,
                "approvals": t["approvals"],
                "rejections": t["rejections"],
                "re_source_wins": t["re_source_wins"],
                "trust_score": round(trust, 3),
                "last_updated": now_iso,
            }, on_conflict="source_domain").execute()
            stats["updated"] += 1
        except Exception as exc:                  # noqa: BLE001
            run.warn("reputation_upsert_failed", f"{dom}: {exc}")
            stats["errors"] += 1

    stats["domains"] = len(tally)
    if tally:
        best = max(tally.items(), key=lambda kv: kv[1]["approvals"])
        run.success("reputation_rebuilt",
                    f"{len(tally)} domain(s) scored from {len(rows)} signal(s); "
                    f"most-approved: {best[0]} ({best[1]['approvals']})")
    else:
        run.info("reputation_empty", "no operator decisions with a source URL yet — cold start")
    return stats


# ── Job 2: confidence + agreement delta ─────────────────────────────────────

def _window_metrics(client, start: datetime, end: datetime) -> dict:
    """Operator-decision metrics for one window. Returns zeros on failure."""
    m = {"samples": 0, "agree": 0, "edit": 0, "reject": 0, "unclassified": 0,
         "bulk_excluded": 0, "clean_approvals": 0,
         "mean_confidence": None, "per_category": {}}
    try:
        res = (client.table("training_signals")
               .select("decision,decided_by,agent_confidence,agent_confidence_was,"
                       "proposed_classification,original_classification,operator_changes")
               .gte("created_at", start.isoformat())
               .lt("created_at", end.isoformat())
               .limit(5000).execute())
        rows = res.data or []
    except Exception as exc:                      # noqa: BLE001
        logger.warning("learning_monitor: window fetch failed: %s", exc)
        return m

    confidences: list[float] = []
    for row in rows:
        if row.get("decided_by") == "agent":
            continue

        # QA A11 — a bulk approval is one click over many cards, not a verdict on
        # each. Counting it as "the operator agreed, unchanged" is how this metric
        # flatters itself: a window heavy in backfill always beats a window of real
        # review, and the delta measures workflow, not model quality.
        changes = row.get("operator_changes") or {}
        if isinstance(changes, dict) and changes.get("bulk"):
            m["bulk_excluded"] += 1
            continue

        decision = row.get("decision")
        if decision in AGREE_DECISIONS:
            bucket = "agree"
        elif decision in EDIT_DECISIONS:
            bucket = "edit"
        elif decision in REJECT_DECISIONS:
            bucket = "reject"
        else:
            continue                              # escalate / link_umbrella: not a verdict

        m["samples"] += 1
        m[bucket] += 1
        # An approval with no recorded changes is either a genuinely clean
        # proposal or an UNMARKED bulk click — the two are indistinguishable in
        # rows written before the marker existed. Tracked so _verdict can refuse
        # to draw a conclusion when the window is dominated by them.
        if bucket == "agree" and not changes:
            m["clean_approvals"] += 1

        conf = row.get("agent_confidence")
        if conf is None:
            conf = row.get("agent_confidence_was")
        if conf is not None:
            try:
                confidences.append(float(conf))
            except (TypeError, ValueError):
                pass

        # A row with no classification recorded is UNCLASSIFIABLE, not a
        # disagreement. Bucketing it as "unknown" and scoring it produced a
        # phantom 0%-agreement category that dragged the headline rate down and
        # invited exactly the wrong conclusion. Count it separately instead.
        cls = row.get("proposed_classification") or row.get("original_classification")
        if not cls:
            m["unclassified"] += 1
            continue
        c = m["per_category"].setdefault(cls, {"samples": 0, "agree": 0})
        c["samples"] += 1
        if bucket == "agree":
            c["agree"] += 1

    if confidences:
        m["mean_confidence"] = sum(confidences) / len(confidences)
    for c in m["per_category"].values():
        c["agreement_rate"] = round(c["agree"] / c["samples"], 3) if c["samples"] else None
    return m


def _auto_publish_calibration(client, start: datetime, end: datetime) -> tuple[int, int]:
    """
    (auto-published, later unpublished by a human).

    The reverted count is the sharpest calibration signal available: it is the
    operator saying "0.95 was wrong". Rising -> raise AUTO_PUBLISH_CONFIDENCE.
    """
    published = reverted = 0
    try:
        res = (client.table("training_signals")
               .select("incident_id")
               .eq("action", "auto_approve")
               .gte("created_at", start.isoformat())
               .lt("created_at", end.isoformat())
               .limit(5000).execute())
        auto_ids = [r["incident_id"] for r in (res.data or []) if r.get("incident_id")]
        published = len(auto_ids)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("learning_monitor: auto-publish fetch failed: %s", exc)
        return 0, 0

    if not auto_ids:
        return 0, 0
    try:
        # An unpublish can land in a later window than the publish, so this is
        # deliberately not window-bounded on the unpublish side.
        res = (client.table("training_signals")
               .select("incident_id")
               .eq("action", "unpublish")
               .in_("incident_id", auto_ids[:200])
               .limit(500).execute())
        reverted = len({r["incident_id"] for r in (res.data or [])})
    except Exception as exc:                      # noqa: BLE001
        logger.warning("learning_monitor: revert fetch failed: %s", exc)
    return published, reverted


def _verdict(cur: dict, prev: dict, agreement_delta, confidence_delta) -> tuple[str, str]:
    """(verdict, human-readable note)."""
    if cur["samples"] < MIN_SAMPLES:
        return ("insufficient_data",
                f"only {cur['samples']} operator decision(s) in the window "
                f"(need {MIN_SAMPLES}) — no honest trend yet.")
    if prev["samples"] < MIN_SAMPLES or agreement_delta is None:
        return ("insufficient_data",
                "no comparable previous window yet — this snapshot becomes the baseline.")

    # QA A11 bridge for pre-marker data. Rows written before backfill-bulk began
    # marking itself carry no `bulk` flag, so an unmarked bulk click is
    # indistinguishable from a genuinely clean approval. When almost every
    # approval is unchanged AND nothing in the window was identifiably bulk, the
    # window is equally consistent with "the model is excellent" and "someone
    # bulk-approved a backfill" — and reporting the flattering one would be a
    # guess dressed as a measurement.
    #
    # Self-clearing: once marked bulk rows appear (bulk_excluded > 0) or the
    # clean share drops back to a normal level, the verdict speaks again.
    clean_share = (cur["clean_approvals"] / cur["samples"]) if cur["samples"] else 0.0
    if cur["bulk_excluded"] == 0 and clean_share >= UNMARKED_BULK_SUSPICION:
        return ("insufficient_data",
                f"{clean_share:.0%} of decisions are approvals with no recorded changes and "
                f"none are marked as bulk. Cannot distinguish a well-performing model from "
                f"bulk backfill approvals (QA A11), so the {agreement_delta:+.1%} agreement "
                f"move is not reportable. Weight auto_publish_reverted instead.")

    # Over-confidence: claiming more while being confirmed less. Caught before
    # the flat-agreement check, because a flat rate hides this entirely.
    if confidence_delta is not None and confidence_delta > 0.03 and agreement_delta < 0:
        return ("regressing",
                f"confidence rose {confidence_delta:+.1%} while operator agreement fell "
                f"{agreement_delta:+.1%} — the model is getting more sure and less right. "
                f"Consider raising AUTO_PUBLISH_CONFIDENCE.")
    if agreement_delta <= REGRESSION_DELTA:
        return ("regressing",
                f"operator agreement fell {agreement_delta:+.1%} vs the previous window.")
    if agreement_delta >= LEARNING_DELTA:
        return ("learning",
                f"operator agreement rose {agreement_delta:+.1%} vs the previous window.")
    return ("stagnant",
            f"operator agreement moved {agreement_delta:+.1%} — inside the "
            f"{LEARNING_DELTA:.0%} noise floor. Not improving, not regressing.")


# ── Entry point ─────────────────────────────────────────────────────────────

def run(supabase_client=None, window_days: int = WINDOW_DAYS,
        trigger: str = "scheduler") -> dict:
    """Rebuild reputation, snapshot the deltas, persist. Never raises."""
    out = {"verdict": "insufficient_data", "agreement_delta": None,
           "confidence_delta": None, "samples": 0, "errors": 0}

    if not agent_enabled(AGENT):
        logger.warning("learning_monitor: disabled via AGENT_DISABLED")
        out["disabled"] = True
        return out

    with AgentRun(AGENT, trigger=trigger) as arun:
        try:
            client = _client(supabase_client)
        except Exception as exc:                  # noqa: BLE001
            arun.fail(f"no Supabase client: {exc}")
            out["errors"] += 1
            return out

        rep = rebuild_source_reputation(client, arun)
        arun.stat("reputation", rep)
        out["errors"] += rep["errors"]

        now = datetime.now(timezone.utc)
        cur_start = now - timedelta(days=window_days)
        prev_start = now - timedelta(days=window_days * 2)

        cur = _window_metrics(client, cur_start, now)
        prev = _window_metrics(client, prev_start, cur_start)

        cur_rate = cur["agree"] / cur["samples"] if cur["samples"] else None
        prev_rate = prev["agree"] / prev["samples"] if prev["samples"] else None
        agreement_delta = (cur_rate - prev_rate) if (cur_rate is not None and prev_rate is not None) else None
        confidence_delta = (
            cur["mean_confidence"] - prev["mean_confidence"]
            if (cur["mean_confidence"] is not None and prev["mean_confidence"] is not None) else None
        )

        auto_pub, auto_rev = _auto_publish_calibration(client, cur_start, now)
        verdict, note = _verdict(cur, prev, agreement_delta, confidence_delta)

        if auto_pub and auto_rev:
            note += (f" {auto_rev} of {auto_pub} auto-published incident(s) were later "
                     f"unpublished — the confidence gate is admitting bad cards.")

        snapshot = {
            "window_days": window_days,
            "sample_count": cur["samples"],
            "mean_confidence": _r3(cur["mean_confidence"]),
            "mean_confidence_prev": _r3(prev["mean_confidence"]),
            "confidence_delta": _r3(confidence_delta),
            "agreement_rate": _r3(cur_rate),
            "agreement_rate_prev": _r3(prev_rate),
            "agreement_delta": _r3(agreement_delta),
            "edit_rate": _r3(cur["edit"] / cur["samples"]) if cur["samples"] else None,
            "reject_rate": _r3(cur["reject"] / cur["samples"]) if cur["samples"] else None,
            "auto_publish_count": auto_pub,
            "auto_publish_reverted": auto_rev,
            "verdict": verdict,
            "per_category": {
                **cur["per_category"],
                "_meta": {
                    "bulk_excluded": cur["bulk_excluded"],
                    "clean_approvals": cur["clean_approvals"],
                    "unclassified": cur["unclassified"],
                },
            },
            "notes": note,
        }

        try:
            client.table("learning_snapshots").insert(snapshot).execute()
            arun.success("snapshot_written", f"verdict={verdict}: {note}")
        except Exception as exc:                  # noqa: BLE001
            arun.error_("snapshot_failed", str(exc))
            out["errors"] += 1

        if verdict == "regressing":
            arun.anomaly("model_regressing", note)
        elif verdict == "stagnant" and cur["samples"] >= MIN_SAMPLES:
            # Not an anomaly. Stagnant with autonomy running usually means the
            # agent is handling the easy cards itself and only hard ones reach
            # the operator — the loop working as designed, not a fault.
            arun.info("model_stagnant", note)

        arun.stat("verdict", verdict)
        arun.stat("agreement_rate", _r3(cur_rate))
        arun.stat("agreement_delta", _r3(agreement_delta))
        arun.set_summary(f"{verdict}: {note}")

        out.update({"verdict": verdict, "agreement_delta": _r3(agreement_delta),
                    "confidence_delta": _r3(confidence_delta),
                    "agreement_rate": _r3(cur_rate),
                    "samples": cur["samples"], "note": note,
                    "auto_publish_count": auto_pub, "auto_publish_reverted": auto_rev})
    return out


def _r3(v):
    return round(v, 3) if isinstance(v, (int, float)) else None
