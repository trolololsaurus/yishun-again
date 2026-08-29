"""
Update-summary enrichment (2026-08-29).

When a new MSM source is matched as an UPDATE to an existing published incident,
the incident's summary should be REFRESHED with the new development — not left
frozen (stale), and not wholesale-replaced by the new source's own short
standalone draft (which is what corrupted yishun-silent-calls-… when a Reddit
draft overwrote the full summary).

`enrich_summary()` is the missing middle: one Haiku call merges the existing
summary + the new development into a unified, refreshed summary that PRESERVES
existing factual detail and WEAVES IN the new development. The result is screened
by the same deterministic groundedness check Stage 2 uses (`find_ungrounded`), so
it can only carry specifics that actually appear in the two source texts.

Two consumers:
  - the War Room UpdateCard pre-fills its box with the enriched summary for the
    operator to review / edit / confirm — the default, always-on path;
  - the autonomous auto-merge applies it unattended ONLY when AUTO_ENRICH_SUMMARY
    is on AND the enrichment passed groundedness (docs/AUTONOMY.md §2b).

Fails SAFE: any error (no client, model failure, empty or ungrounded output)
returns ok=False with summary="" — the caller keeps the existing summary and the
incident is never corrupted. Signal (EDMW/Reddit) content is never enrichment
input: the caller passes only non-signal source text (guardrail #2).
"""
import logging
import os

from filters.model_call import create_with_headroom
from filters.stage2_writer import _get_client, _parse_json, find_ungrounded

logger = logging.getLogger(__name__)

MODEL = os.getenv("STAGE2_WRITE_MODEL", "claude-haiku-4-5-20251001")
ENRICH_MAX_TOKENS = int(os.getenv("ENRICH_MAX_TOKENS", "1200"))

_SYSTEM = """\
You refresh an existing incident summary with a newly-reported development.

You are given the EXISTING summary of a published incident and a NEW development
(a later report about the SAME event). Produce ONE refreshed summary that:
  - preserves every factual detail already in the existing summary,
  - weaves in the new development where it belongs (usually the outcome: a charge,
    trial, sentencing, a revised casualty figure, a resolution),
  - invents NOTHING — every name, number and place must appear in one of the two
    texts you were given,
  - keeps the paragraph style (2-4 sentences per paragraph, blank line between),
  - does not exceed {budget} characters.

Return JSON only, no markdown fences: {{"summary": "<refreshed summary>"}}
"""


def enrich_summary(existing_summary: str, new_source_text: str,
                   new_headline: str = "", char_budget: int = 1600,
                   client=None) -> dict:
    """
    Merge `existing_summary` + the new development into a refreshed summary.

    Returns {"summary", "grounded": bool, "ungrounded": dict, "ok": bool}.
    `ok` is True only when a non-empty, grounded summary was produced; on any
    failure it is False with summary="" so the caller falls back to keeping the
    existing summary. Never raises.
    """
    empty = {"summary": "", "grounded": False, "ungrounded": {}, "ok": False}
    existing_summary = (existing_summary or "").strip()
    new_source_text = (new_source_text or "").strip()
    if not existing_summary or not new_source_text:
        return empty

    try:
        c = client or _get_client()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("enrich: no Anthropic client — keeping existing (%s)", exc)
        return empty

    user = (
        f"EXISTING SUMMARY:\n{existing_summary}\n\n"
        f"NEW DEVELOPMENT (headline: {new_headline or 'n/a'}):\n{new_source_text[:6000]}"
    )
    try:
        response, _ = create_with_headroom(
            c, call="consolidation.enrich_summary", env_var="ENRICH_MAX_TOKENS",
            model=MODEL, max_tokens=ENRICH_MAX_TOKENS, temperature=0.2,
            system=_SYSTEM.format(budget=char_budget),
            messages=[{"role": "user", "content": user}],
        )
        enriched = (_parse_json(response.content[0].text).get("summary") or "").strip()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("enrich: model call failed — keeping existing (%s)", exc)
        return empty

    if not enriched:
        return empty

    # Ground against BOTH texts: the refresh may carry any fact from the existing
    # summary OR the new development, and nothing else.
    ungrounded = find_ungrounded(enriched, existing_summary + "\n" + new_source_text)
    grounded = not (ungrounded["numbers"] or ungrounded["proper_nouns"])
    if not grounded:
        logger.warning("enrich: ungrounded specifics %s — offered for review, not auto-applied",
                       ungrounded)
    return {"summary": enriched, "grounded": grounded, "ungrounded": ungrounded,
            "ok": bool(enriched and grounded)}
