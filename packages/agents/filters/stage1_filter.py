"""
Stage 1 noise filter — Groq (openai/gpt-oss-20b)

Fast, cheap noise rejection before content reaches the more expensive
Stage 2 Claude writer. Targets 60-70% rejection of raw scrape volume.

Pass threshold: confidence >= 0.4
"""

import json
import logging
import os
import re
import sys

from dotenv import load_dotenv
from groq import Groq
from scrapers.groq_budget import GroqMinuteRateLimiter

load_dotenv(override=False)

# Module-level singleton: shared across all filter_content() calls in this
# process. Enforces the 6k TPM free-tier ceiling without any changes to callers.
_rate_limiter = GroqMinuteRateLimiter()

logger = logging.getLogger(__name__)

# Model history (Groq deprecations):
#   llama3-8b-8192      — decommissioned by Groq.
#   llama-3.1-8b-instant — deprecated 2026-06-27, decommissioned 2026-08-16.
# Current: GPT OSS 20B, Groq's recommended replacement for the 8B-instant tier.
# (Lighter alt if cost/latency matters: "gemma2-9b-it".)
MODEL = "openai/gpt-oss-20b"
PASS_THRESHOLD = 0.4

# System prompt — extended with backfill-hardened reject criteria.
# Override rule: even a normally-rejected topic PASSES if incident-signal
# keywords (death, stab, arrest, …) appear in the content.
STAGE1_SYSTEM_PROMPT = """
You are a content filter for a Yishun, Singapore incident archive.

Your job: determine if a piece of content is worth logging as a Yishun incident.

Return JSON only:
{
  "is_relevant": boolean,
  "confidence": float (0.0-1.0),
  "reason": string (one sentence)
}

PASS if content describes:
- A specific incident, event, or occurrence in Yishun
- A person associated with Yishun making news
- A crime, accident, unusual event, positive community story in Yishun

REJECT if content is:
- General news mentioning Yishun only in passing
- Advertisements, property listings, event promotions
- Opinion pieces with no specific incident
- Clearly duplicate of something already archived
- Political content of any kind
- Food reviews, restaurant/hawker/cafe openings or reviews — UNLESS the content
  mentions injury, death, food poisoning, or a hygiene/safety violation
- COVID-19, coronavirus, or pandemic content where Yishun is not the specific
  location of a distinct incident (nationwide / Singapore-wide measures are noise)
- Property or real estate content: BTO launches, psf prices, showflat visits,
  property reviews, en-bloc news — UNLESS a specific incident occurred at the
  property (fire, crime, structural failure, etc.)
- Generic infrastructure updates: bus route changes, MRT line upgrades, road
  works, station renovation — with no specific incident attached
- Obituaries, death notices, or tributes to named individuals that describe
  only a natural passing with no incident

IMPORTANT OVERRIDE — even if a topic matches the REJECT list above, you must
PASS it if the content contains clear incident-signal language such as:
  death, dead, died, killed, murder, stab, injur, accident, crash, arrest,
  charged, jailed, convicted, sentenced, poison, outbreak, recall, unsafe,
  assault, attack, fire, explosion, flood, collapse, abuse, missing, found dead
"""

# Incident-signal keywords used by the pre-filter in filter_content().
# Any match → skip the REJECT-category check and always send to Groq.
# These are substring matches (lower-case), not whole-word.
_OVERRIDE_KEYWORDS = (
    "death", "dead", "died", "killed", "murder", "stab", "injur",
    "accident", "crash", "arrest", "charged", "jailed", "convicted",
    "sentenced", "poison", "outbreak", "recall", "unsafe", "assault",
    "attack", "fire", "explosion", "flood", "collapse", "abuse",
    "missing", "found dead",
)


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY is not set. "
            "Add it to .env or Cloud Run environment variables."
        )
    return Groq(api_key=api_key)


def _build_user_message(content: dict) -> str:
    """Format the scraped content dict into a prompt-ready string."""
    return (
        f"Source: {content.get('source_name', 'unknown')}\n"
        f"URL: {content.get('url', '')}\n\n"
        f"Title: {content.get('title', '')}\n\n"
        f"Content:\n{content.get('content', '')}"
    )


def _parse_response(text: str) -> dict:
    """
    Extract and validate the JSON object from the model's response.
    Handles markdown code fences and minor formatting noise.
    """
    # Strip ```json ... ``` or ``` ... ``` wrappers
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Fall back: grab the first {...} block in the response
        match = re.search(r"\{[\s\S]*?\}", text)
        if not match:
            raise ValueError(f"No JSON object found in model response: {text[:300]!r}")
        result = json.loads(match.group())

    # Validate required keys
    for key in ("is_relevant", "confidence", "reason"):
        if key not in result:
            raise ValueError(f"Model response missing required key '{key}': {result}")

    # Coerce types — model may return strings for booleans/floats
    result["is_relevant"] = bool(result["is_relevant"])
    result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
    result["reason"] = str(result["reason"])

    return result


def _has_override_keyword(content: dict) -> bool:
    """Return True if any incident-signal keyword appears in title+content."""
    text = (
        (content.get("title", "") + " " + content.get("content", "")).lower()
    )
    return any(kw in text for kw in _OVERRIDE_KEYWORDS)


def filter_content(content: dict) -> dict:
    """
    Run Stage 1 filter on a scraped content dict.

    Args:
        content: {
            "title":       str,
            "content":     str,
            "url":         str,
            "source_name": str,
        }

    Returns:
        {
            "is_relevant": bool,   # True = passes to Stage 2
            "confidence":  float,  # 0.0–1.0
            "reason":      str,    # one-sentence explanation
            "passes":      bool,   # confidence >= PASS_THRESHOLD and is_relevant
            "override":    bool,   # True if incident-signal override fired
            "usage":       dict,   # {prompt_tokens, completion_tokens} from the Groq response
        }
    """
    # Surface whether the override is active so the model sees it in the prompt
    # and callers can log it.
    override_active = _has_override_keyword(content)

    client = _get_client()
    user_message = _build_user_message(content)

    if override_active:
        # Prepend a hint so the model doesn't second-guess obvious incidents
        user_message = (
            "[NOTE: incident-signal keyword detected — apply OVERRIDE rule]\n\n"
            + user_message
        )

    logger.debug(
        "Stage 1 — calling Groq for: %s%s",
        content.get("title", "")[:80],
        " [override]" if override_active else "",
    )

    sleep_seconds = _rate_limiter.wait_if_needed(estimated_tokens=800)

    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": STAGE1_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.1,   # low temperature → consistent JSON output
        max_tokens=256,
    )

    _rate_limiter.record(
        completion.usage.prompt_tokens + completion.usage.completion_tokens
    )

    raw = completion.choices[0].message.content
    logger.debug("Stage 1 raw response: %s", raw)

    result = _parse_response(raw)
    result["passes"]   = result["is_relevant"] and result["confidence"] >= PASS_THRESHOLD
    result["override"] = override_active
    result["usage"] = {
        "prompt_tokens":     completion.usage.prompt_tokens,
        "completion_tokens": completion.usage.completion_tokens,
    }
    result["rate_limiter_sleep_seconds"] = sleep_seconds

    logger.info(
        "Stage 1 [%s] relevant=%s confidence=%.2f passes=%s%s | %s",
        content.get("source_name", "?"),
        result["is_relevant"],
        result["confidence"],
        result["passes"],
        " [override]" if override_active else "",
        result["reason"],
    )

    return result


# ---------------------------------------------------------------------------
# Tests — run directly:  python packages/agents/filters/stage1_filter.py
# ---------------------------------------------------------------------------

SAMPLES = [
    {
        "_label": "CLEARLY RELEVANT — knife attack at Yishun block",
        "title": "Man arrested after alleged knife attack at Yishun void deck",
        "content": (
            "A 34-year-old man was arrested on Tuesday after allegedly attacking his "
            "neighbour with a knife at the void deck of Block 725 Yishun Street 71. "
            "The victim, a 29-year-old man, sustained lacerations to his forearm and "
            "was taken to Khoo Teck Puat Hospital. He is in stable condition. "
            "Police confirmed they are investigating the case under the Penal Code for "
            "voluntarily causing hurt with a dangerous weapon."
        ),
        "url": "https://www.channelnewsasia.com/singapore/yishun-knife-attack-block-725",
        "source_name": "CNA",
    },
    {
        "_label": "NOISE — property listing mentioning Yishun",
        "title": "4-room HDB flat for sale near Yishun MRT — asking $488K",
        "content": (
            "Well-maintained 4-room HDB flat at Yishun Ave 4, walking distance to "
            "Yishun MRT and Northpoint City mall. Recently renovated kitchen and "
            "bathrooms. High floor with unblocked view. Lease commencement 1997. "
            "Owner-occupied, serious buyers only. Viewing strictly by appointment. "
            "Contact agent at 9XXX XXXX."
        ),
        "url": "https://www.propertyguru.com.sg/listing/yishun-4room-hdb-488k-12345",
        "source_name": "PropertyGuru",
    },
    {
        "_label": "BORDERLINE — Reddit eyewitness report, no confirmed details",
        "title": "Saw police cars outside Yishun MRT this morning, anyone know what happened?",
        "content": (
            "Was heading to work around 8am and counted at least 6 police cars parked "
            "outside Yishun MRT station with officers everywhere. A small crowd had "
            "gathered. Couldn't stop to find out more and haven't seen anything on the "
            "news. Did anyone else see this or know what was going on?"
        ),
        "url": "https://www.reddit.com/r/singapore/comments/abc123/yishun_mrt_police",
        "source_name": "Reddit Singapore",
    },
    # ── New reject-category tests ────────────────────────────────────────────
    {
        "_label": "REJECT — food review, no incident keywords",
        "title": "Best laksa in Yishun: we tried 5 stalls at Yishun Park Hawker Centre",
        "content": (
            "Yishun Park Hawker Centre has long been a local favourite. We spent a "
            "Saturday morning working our way through five laksa stalls and ranking "
            "them by broth depth, noodle texture, and value. Our top pick: Stall #12, "
            "which has been operating since 1989. A bowl costs $3.50. No frills, no "
            "Instagram aesthetic — just solid hawker fare. Highly recommended for a "
            "weekend breakfast."
        ),
        "url": "https://mothership.sg/2022/04/yishun-laksa-food-review/",
        "source_name": "Mothership",
    },
    {
        "_label": "REJECT — COVID/pandemic, not Yishun-specific",
        "title": "Vaccination centre at Yishun Community Club now open",
        "content": (
            "The Ministry of Health announced that a new COVID-19 vaccination centre "
            "has opened at Yishun Community Club to serve residents in the north of "
            "Singapore. Appointments can be booked via the national booking portal. "
            "The centre is open daily from 8am to 8pm. This is part of the national "
            "vaccination programme to achieve 80% coverage by August."
        ),
        "url": "https://www.channelnewsasia.com/singapore/yishun-cc-vaccination-centre",
        "source_name": "CNA",
    },
    {
        "_label": "PASS OVERRIDE — food venue, but food poisoning outbreak",
        "title": "Health authorities investigate Yishun hawker stall after 14 diners fall ill",
        "content": (
            "The Singapore Food Agency is investigating a Yishun hawker stall after "
            "14 diners reported symptoms of food poisoning following a meal there on "
            "Saturday evening. The stall, which sells chicken rice at Block 925 Yishun "
            "Central, has been ordered to cease operations pending investigation. "
            "Those affected reported vomiting, diarrhoea, and fever within hours. "
            "Three were hospitalised at Khoo Teck Puat Hospital."
        ),
        "url": "https://www.straitstimes.com/singapore/yishun-food-poisoning-hawker",
        "source_name": "The Straits Times",
    },
]


def run_tests() -> None:
    n = len(SAMPLES)
    print(f"\n{'=' * 64}")
    print(f"Stage 1 Filter — Test Run ({n} samples)")
    print(f"Model: {MODEL}   Pass threshold: confidence >= {PASS_THRESHOLD}")
    print(f"{'=' * 64}\n")

    passed = 0
    failed = 0

    for i, sample in enumerate(SAMPLES, 1):
        label = sample.pop("_label")
        print(f"[{i}/{n}] {label}")
        print(f"      Title: {sample['title'][:72]}")

        try:
            result = filter_content(sample)
            status = "PASS   ✓" if result["passes"] else "REJECT ✗"
            over   = "  [override]" if result.get("override") else ""
            print(f"      Result:     {status}{over}")
            print(f"      Relevant:   {result['is_relevant']}")
            print(f"      Confidence: {result['confidence']:.2f}")
            print(f"      Reason:     {result['reason']}")
            passed += 1
        except Exception as exc:
            print(f"      ERROR: {exc}")
            failed += 1

        print()

    print(f"{'=' * 64}")
    if failed:
        print(f"FAILED — {failed} error(s). Check GROQ_API_KEY and network.")
        sys.exit(1)
    else:
        print(f"All {passed} samples returned valid responses. Stage 1 filter OK.")
    print(f"{'=' * 64}\n")


if __name__ == "__main__":
    run_tests()
