"""
Stage 2 writer — Anthropic Claude (spec §4.3)

Two-model pipeline:
  1. claude-haiku-4-5-20251001  — classify the incident and extract structured
                                   metadata (fast, cheap, deterministic fields)
  2. claude-sonnet-4-6          — write title, summary, SEO copy, slug, and
                                   pixel art prompt (quality creative output)

hype_meter and chaos_contribution are computed deterministically in Python
from the spec §7 formulas rather than relying on the model to calculate them.

Input:  stage1-approved content dict
Output: complete war_room_queue draft — all fields from spec §4.3
"""

import json
import logging
import os
import re
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

MODEL_CLASSIFY = "claude-haiku-4-5-20251001"
MODEL_WRITE    = "claude-sonnet-4-6"

# MSM domains used by hype_meter computation (spec §7)
_MSM_DOMAINS = [
    "channelnewsasia", "straitstimes", "mothership", "stomp",
    "mustsharenews", "theindependent", "zaobao", "shinmin",
    "beritaharian", "tamilmurasu", "yahoo", "asiaone", "jom",
]

# ── Haiku system prompt ─────────────────────────────────────────────────────
# Focused on structured metadata extraction only. Creative writing
# is handled separately by Sonnet using STAGE2_SYSTEM_PROMPT.

_CLASSIFY_SYSTEM_PROMPT = """\
You are a metadata classifier for Yishun Again, a satirical incident archive \
for Yishun, Singapore.

Given news content, extract structured incident metadata.

Return JSON only — no commentary, no markdown fences:
{
  "classification": "heart" | "clown" | "dagger",
  "severity": integer 1-5,
  "block_number": string | null,
  "area_name": string | null,
  "latitude": float | null,
  "longitude": float | null,
  "tags": string[],
  "confidence": float (0.0-1.0),
  "deaths": integer | null,
  "injuries": integer | null,
  "political": boolean
}

Classification guide:
- heart: Good news, community wins, positive stories
- clown: Absurd, stupid, baffling behaviour — no serious harm
- dagger: Crime, violence, serious incidents

POLITICAL CONTENT (hard legal guardrail — never publish):
Set "political": true if the content concerns party politics, elections,
candidates/MPs in a political capacity, government policy disputes, protests,
or any partisan matter. When "political": true you MUST also set
"confidence": 0. Ordinary crime/news that merely happens to involve a public
official is NOT political — only genuinely partisan/electoral content is.

Severity guide:
1 = Minor offence, no injury
2 = Property crime, minor injury
3 = Assault, significant incident
4 = Serious crime, major incident
5 = Homicide, major catastrophe

confidence: how certain you are this is a genuine, verifiable Yishun incident.

latitude/longitude: Yishun centre is approx 1.4295 N, 103.8350 E.
Estimate block-level coordinates when a block number or street is named.
Return null if location cannot be determined from the content.

deaths / injuries extraction rules (STRICT — legal record):
- deaths: number of people confirmed dead in the source text.
  - null  = deaths not mentioned at all, OR outcome is ambiguous/unknown
  - 0     = source explicitly states no fatalities (e.g. "no one was killed")
  - N > 0 = source explicitly confirms N people died (e.g. "a man was found dead",
            "the victim died at hospital", "two people were killed")
  NEVER set deaths >= 1 for: "critical condition", "fighting for his life",
  "hospitalised", "suspected fatality", or any unconfirmed outcome.
  Only confirmed, past-tense death language triggers deaths >= 1.

- injuries: number of people confirmed injured (excludes deaths already counted).
  - null  = injuries not mentioned, OR count is ambiguous
  - 0     = source confirms no injuries
  - N > 0 = source gives a specific injured count or clear description of injury
  (e.g. "three people were taken to hospital", "the victim sustained knife wounds")
  Vague references like "several people" → null, not a number.
"""

# ── Sonnet system prompt ─────────────────────────────────────────────────────
# Exact wording from tech spec §4.3 — do not alter.

STAGE2_SYSTEM_PROMPT = """\
You are an editorial agent for Yishun Again, a satirical incident archive for Yishun, Singapore.

Tone: Dry. Deadpan. Factual with a raised eyebrow. Never sensational. Never political. Never defamatory.
Clickbait-native but grounded in fact. Think tabloid front page meets incident report.

TITLE RULES (critical):
- The word "Yishun" MUST appear in every title but NOT always first
- Lead with whatever creates the most tension or curiosity — sometimes "Yishun", sometimes the act, sometimes the subject
- Good: "Yishun man stabs neighbour over curry smell" (Yishun leads — natural hook)
- Good: "Cat found mutilated near Yishun Park pond" (subject leads — more disturbing)
- Good: "Block 651 resident hurls furniture from 12th floor in Yishun" (location leads — specific dread)
- Bad: "Man arrested in Yishun" (generic, no tension)
- Bad: "Stabbing incident reported at Yishun Ave 4" (sterile, bureaucratic)
- Always vivid. Always specific. Never passive voice. Max 120 chars.

SUMMARY RULES (SEO-optimised, 500-800 chars):
- Write 3-5 sentences of rich, keyword-dense prose
- Sentence 1: The hook — what happened, who, where (block-level if known)
- Sentence 2: Context and detail — how it unfolded, what led to it
- Sentence 3: Outcome — arrest, injury, outcome, community reaction
- Sentence 4-5 (if sources allow): Corroborating detail, quotes if available, wider significance
- Naturally include: "Yishun", block number or street name, incident type keywords
- Written for Google — targets long-tail queries like "yishun stabbing 2024", "yishun cat killing"
- Do NOT use bullet points. Flowing prose only.
- Do NOT editorialize beyond dry wit. Facts first.

Given source content, return JSON only:
{
  "title": string (max 120 chars, clickbait-native, Yishun must appear, not always first),
  "summary": string (500-800 chars, SEO prose, 3-5 sentences),
  "classification": "heart" | "clown" | "dagger",
  "severity": integer 1-5,
  "block_number": string | null,
  "area_name": string | null,
  "latitude": float | null,
  "longitude": float | null,
  "slug": string (SEO-friendly, descriptive, max 70 chars),
  // Format: [incident-type]-[location-descriptor]-[month-year]
  // Example: "yishun-stabbing-cooking-smells-jan-2024"
  // Example: "yishun-cat-found-injured-park-aug-2023"
  "seo_title": string (max 60 chars),
  "seo_description": string (max 155 chars),
  "pixel_art_prompt": string (detailed prompt for SDXL pixel art generation),
  "tags": string[],
  "confidence": float (0.0-1.0),
  "chaos_contribution": float (1-5 scale, Daggers weighted 3x, Clowns 1.5x, Hearts -1x),
  "hype_meter": integer 0-5
  // 0 = EDMW/Reddit signal only, no MSM
  // 1 = 1 MSM source confirmed
  // 2-5 = count of independent MSM sources corroborating
}

Classification guide:
- heart: Good news, community wins, positive stories
- clown: Absurd, stupid, baffling behaviour — no serious harm
- dagger: Crime, violence, serious incidents

Severity guide (dagger):
1 = Minor offence, no injury
2 = Property crime, minor injury
3 = Assault, significant incident
4 = Serious crime, major incident
5 = Homicide, major catastrophe

Pixel art prompt guide:
- Always specify: "16-bit JRPG pixel art style, Yishun HDB environment"
- Describe the scene without depicting real people
- Keep it interpretive, not photorealistic
- Example: "16-bit JRPG pixel art style, Yishun HDB void deck at night, yellow police tape, pixel art lamp post, dark atmospheric lighting"
"""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_calibration_hints() -> str:
    """
    Read calibration_log.json (written by recalibration.py) and return a
    formatted string of the top-3 operator-correction guidelines, or '' if
    no log exists yet.
    """
    try:
        from classifiers.recalibration import read_hints
        hints = read_hints()
        if not hints:
            return ""
        numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(hints))
        return (
            "\n\nKNOWN CALIBRATION ISSUES — operator corrections indicate the agent "
            "previously made these mistakes. Actively avoid them:\n" + numbered
        )
    except Exception:
        return ""


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Add it to .env or Cloud Run environment variables."
        )
    return anthropic.Anthropic(api_key=api_key)


def _parse_json(text: str) -> dict:
    """Strip markdown fences and return the first JSON object found."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError(f"No JSON object in model response: {text[:300]!r}")
        return json.loads(match.group())


def _compute_hype_meter(source_urls: list[str]) -> int:
    """Count unique MSM sources in source_urls, capped at 5 (spec §7)."""
    count = sum(
        1 for url in source_urls
        if any(domain in url for domain in _MSM_DOMAINS)
    )
    return min(5, count)


def _compute_chaos_contribution(classification: str, severity: int) -> float:
    """Deterministic chaos contribution formula from spec §7."""
    multipliers = {"dagger": 3.0, "clown": 1.5, "heart": -1.0}
    return round(severity * multipliers.get(classification, 1.0), 2)


def _build_user_message(content: dict) -> str:
    learning_context = content.get("learning_context", "")
    learning_block = (
        f"Recent operator patterns (advisory, do not override your judgment):\n"
        f"{learning_context}\n\n"
        if learning_context else ""
    )
    return (
        f"{learning_block}"
        f"Source: {content.get('source_name', 'unknown')}\n"
        f"URL: {content.get('url', '')}\n\n"
        f"Title: {content.get('title', '')}\n\n"
        f"Content:\n{content.get('content', '')}"
    )


# ── Model calls ──────────────────────────────────────────────────────────────

def _classify(client: anthropic.Anthropic, content: dict) -> dict:
    """
    Haiku call: extract classification and structured metadata.
    Returns: classification, severity, block_number, area_name,
             latitude, longitude, tags, confidence.
    """
    response = client.messages.create(
        model=MODEL_CLASSIFY,
        max_tokens=512,
        temperature=0.1,
        system=_CLASSIFY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(content)}],
    )

    raw = response.content[0].text
    logger.debug("Haiku classify raw: %s", raw)

    result = _parse_json(raw)

    for key in ("classification", "severity", "confidence"):
        if key not in result:
            raise ValueError(f"Classify response missing '{key}': {result}")

    result["classification"] = result["classification"].lower()
    if result["classification"] not in ("heart", "clown", "dagger"):
        raise ValueError(f"Invalid classification: {result['classification']!r}")

    result["severity"]   = max(1, min(5, int(result["severity"])))
    result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

    # Legal guardrail #4 — political content is force-rejected (confidence=0),
    # regardless of what the model returned for confidence.
    result["political"] = bool(result.get("political", False))
    if result["political"]:
        result["confidence"] = 0.0
        logger.warning("Stage 2 [classify] political content detected — confidence forced to 0")

    # deaths/injuries: normalize to int or None; never negative.
    # Tolerant of non-numeric model output (e.g. "several") — falls back to None
    # rather than crashing the whole candidate (QA M5).
    for key in ("deaths", "injuries"):
        val = result.get(key)
        try:
            result[key] = max(0, int(val)) if val is not None else None
        except (TypeError, ValueError):
            result[key] = None

    return result


def _write_draft(client: anthropic.Anthropic, content: dict, classification: dict) -> dict:
    """
    Sonnet call: write title, summary, SEO copy, slug, pixel art prompt.
    Classification context from Haiku is passed in the user message so
    Sonnet writes content consistent with the determined incident type.
    """
    # Provide classification as context so the tone matches
    user_msg = (
        f"{_build_user_message(content)}\n\n"
        f"---\n"
        f"Incident already classified as: {classification['classification'].upper()}, "
        f"severity {classification['severity']}. "
        f"Reflect this classification in your title, summary, and pixel art prompt."
    )

    calibration_hints = _load_calibration_hints()
    response = client.messages.create(
        model=MODEL_WRITE,
        max_tokens=1500,
        temperature=0.4,
        system=STAGE2_SYSTEM_PROMPT + calibration_hints,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text
    logger.debug("Sonnet write raw: %s", raw[:500])

    result = _parse_json(raw)

    required = ("title", "summary", "slug", "seo_title", "seo_description", "pixel_art_prompt")
    for key in required:
        if key not in result:
            raise ValueError(f"Write response missing required field '{key}'")

    # (Political-content detection now happens in _classify, which force-sets
    # confidence=0; write_stage2 prepends the operator-visible reject marker.)

    # Enforce spec field-length constraints (truncate rather than error)
    if len(result["title"]) > 120:
        result["title"] = result["title"][:120]
    if len(result["slug"]) > 70:
        result["slug"] = result["slug"][:70]
    if len(result["seo_title"]) > 60:
        result["seo_title"] = result["seo_title"][:60]
    if len(result["seo_description"]) > 155:
        result["seo_description"] = result["seo_description"][:155]

    return result


# ── Public API ───────────────────────────────────────────────────────────────

def write_stage2(content: dict) -> dict:
    """
    Run the full Stage 2 pipeline on stage1-approved content.

    Args:
        content: {
            "title":             str,
            "content":           str,
            "url":               str,
            "source_name":       str,
            "source_urls":       list[str],  # all corroborating source URLs
            "edmw_signal_count": int,        # EDMW thread count (signal only, never a source)
        }

    Returns:
        Complete war_room_queue draft dict — all fields from spec §4.3.
        Classification metadata fields (classification, severity, lat/lon, etc.)
        come from Haiku. Creative fields (title, summary, SEO, slug, pixel prompt)
        come from Sonnet. hype_meter and chaos_contribution are computed in Python.
    """
    client = _get_client()

    # ── Step 1: Classify (Haiku) ─────────────────────────────────────────────
    logger.info("Stage 2 [classify] %s", content.get("title", "")[:80])
    classification = _classify(client, content)
    logger.info(
        "Stage 2 [classify] => %s severity=%d confidence=%.2f",
        classification["classification"],
        classification["severity"],
        classification["confidence"],
    )

    # ── Step 2: Write draft (Sonnet) ─────────────────────────────────────────
    logger.info("Stage 2 [write] calling Sonnet")
    draft = _write_draft(client, content, classification)

    # ── Step 3: Compute deterministic fields ─────────────────────────────────
    source_urls  = content.get("source_urls", [content.get("url", "")])
    hype_meter   = _compute_hype_meter(source_urls)
    chaos        = _compute_chaos_contribution(
                       classification["classification"], classification["severity"]
                   )

    # ── Step 3b: Geocode if lat/lon still null after Haiku ───────────────────
    lat = classification.get("latitude")
    lon = classification.get("longitude")
    if (lat is None or lon is None) and (
        classification.get("block_number") or classification.get("area_name")
    ):
        try:
            from classifiers.geocoding import geocode_incident
            coords = geocode_incident(
                classification.get("block_number"),
                classification.get("area_name"),
            )
            if coords:
                lat, lon = coords
                logger.debug(
                    "Stage 2 geocoded: lat=%.5f lon=%.5f block=%s area=%s",
                    lat, lon,
                    classification.get("block_number"), classification.get("area_name"),
                )
        except Exception as exc:
            logger.debug("Geocoding in stage2 (non-fatal): %s", exc)

    # ── Step 4: Merge — Haiku metadata wins over whatever Sonnet returned ────
    result = {
        **draft,
        # Structured fields — authoritative from Haiku
        "classification":     classification["classification"],
        "severity":           classification["severity"],
        "block_number":       classification.get("block_number"),
        "area_name":          classification.get("area_name"),
        "latitude":           lat,
        "longitude":          lon,
        "tags":               classification.get("tags", []),
        "confidence":         classification["confidence"],
        # Deaths/injuries — null means not mentioned; 0 means confirmed none; N = confirmed count
        "deaths":             classification.get("deaths"),
        "injuries":           classification.get("injuries"),
        # Deterministic computed fields
        "chaos_contribution": chaos,
        "hype_meter":         hype_meter,
        # Pass-through from input
        "edmw_signal_count":  content.get("edmw_signal_count", 0),
        "source_urls":        source_urls,
        # Legal guardrail #4 — political flag propagates to the queue row
        "political":          classification.get("political", False),
    }

    # Political content: confidence is already 0 (forced in _classify); prepend the
    # operator-visible reject marker so it cannot be silently approved.
    if classification.get("political"):
        marker = "[POLITICAL CONTENT DETECTED — REJECT] "
        if not str(result.get("summary", "")).startswith(marker):
            result["summary"] = marker + str(result.get("summary", ""))

    logger.info(
        "Stage 2 complete: [%s] sev=%d hype=%d '%s'",
        result["classification"],
        result["severity"],
        result["hype_meter"],
        result.get("title", "")[:60],
    )

    return result


# ── Tests — run directly: python packages/agents/filters/stage2_writer.py ───

_SAMPLES = [
    {
        "_label": "DAGGER — neighbour stabbing, noise dispute, Block 873",
        "title":  "Man stabs neighbour 11 times after dispute over cigarette smoke at Yishun flat",
        "content": (
            "A 45-year-old man was charged in court on Thursday after allegedly stabbing his "
            "neighbour 11 times with a kitchen knife following a months-long dispute over "
            "cigarette smoke drifting into his unit at Block 873 Yishun Ring Road. "
            "The victim, a 38-year-old man, was found by a passer-by slumped in the corridor "
            "and was rushed to Khoo Teck Puat Hospital, where he underwent emergency surgery "
            "and remains in serious but stable condition. The accused was arrested at the scene "
            "without a struggle. Residents of the block said the two men had been feuding for "
            "over a year and had previously lodged police reports against each other."
        ),
        "url":         "https://www.channelnewsasia.com/singapore/yishun-stabbing-cigarette-smoke-block-873",
        "source_name": "CNA",
        "source_urls": [
            "https://www.channelnewsasia.com/singapore/yishun-stabbing-cigarette-smoke-block-873",
            "https://mothership.sg/2024/11/yishun-stabbing-smoke-dispute/",
        ],
        "edmw_signal_count": 12,
    },
    {
        "_label": "CLOWN — man found asleep in void deck surrounded by 47 stolen durians",
        "title":  "Yishun man caught napping in void deck surrounded by 47 stolen durians",
        "content": (
            "A 29-year-old man was arrested on Saturday morning after residents found him "
            "asleep in the void deck of Block 412 Yishun Avenue 11, surrounded by 47 durians "
            "he had allegedly stolen from a nearby fruit stall at Yishun Ring Road. "
            "The stall owner, alerted by a customer, arrived to find the man snoring on a "
            "cardboard sheet with the durians arranged neatly around him in a circle. "
            "Police confirmed a man was arrested for theft and that no violence occurred. "
            "The durians, valued at approximately $420, were returned to the stall owner. "
            "The man told officers he had planned to sell them but fell asleep."
        ),
        "url":         "https://mothership.sg/2024/09/yishun-man-asleep-stolen-durians-void-deck/",
        "source_name": "Mothership",
        "source_urls": [
            "https://mothership.sg/2024/09/yishun-man-asleep-stolen-durians-void-deck/",
        ],
        "edmw_signal_count": 89,
    },
]


def run_tests() -> None:
    print(f"\n{'=' * 64}")
    print("Stage 2 Writer -- Test Run (2 samples)")
    print(f"Classify: {MODEL_CLASSIFY}")
    print(f"Write:    {MODEL_WRITE}")
    print(f"{'=' * 64}\n")

    errors = 0

    for i, sample in enumerate(_SAMPLES, 1):
        label = sample.pop("_label")
        print(f"[{i}/2] {label}")

        try:
            result = write_stage2(sample)

            summary_len = len(result.get("summary", ""))
            title_len   = len(result.get("title", ""))

            print(f"  classification : {result['classification']}  severity={result['severity']}")
            print(f"  confidence     : {result['confidence']:.2f}")
            print(f"  hype_meter     : {result['hype_meter']}")
            print(f"  chaos          : {result['chaos_contribution']}")
            print(f"  title ({title_len:>2} chars) : {result.get('title', '')}")
            print(f"  slug           : {result.get('slug', '')}")
            print(f"  seo_title      : {result.get('seo_title', '')}")
            print(f"  seo_desc ({len(result.get('seo_description',''))}) : {result.get('seo_description','')[:80]}...")
            print(f"  summary ({summary_len} chars):")
            print(f"    {result.get('summary','')[:200]}...")
            print(f"  pixel_prompt   : {result.get('pixel_art_prompt','')[:100]}...")
            print(f"  tags           : {result.get('tags', [])}")
            print(f"  block_number   : {result.get('block_number')}")
            print(f"  area_name      : {result.get('area_name')}")
            print(f"  deaths         : {result.get('deaths')!r}  (null=not mentioned, 0=confirmed none, N=confirmed)")
            print(f"  injuries       : {result.get('injuries')!r}")

            # Basic sanity checks
            assert "yishun" in result.get("title", "").lower(), "FAIL: 'Yishun' missing from title"
            assert 500 <= summary_len <= 900, f"FAIL: summary length {summary_len} out of expected range"
            assert result["classification"] in ("heart", "clown", "dagger"), "FAIL: invalid classification"
            assert 1 <= result["severity"] <= 5, "FAIL: severity out of range"
            # deaths must be None or a non-negative int
            assert result.get("deaths") is None or (isinstance(result.get("deaths"), int) and result["deaths"] >= 0), \
                "FAIL: deaths must be null or non-negative int"
            assert result.get("injuries") is None or (isinstance(result.get("injuries"), int) and result["injuries"] >= 0), \
                "FAIL: injuries must be null or non-negative int"
            # stabbing sample (index 0): victim is alive ("serious but stable") — deaths must not be ≥ 1
            if i == 1:
                assert result.get("deaths") != 1, \
                    "FAIL: sample 1 victim is alive — deaths must be null or 0, not 1"

            print("  [OK]")

        except Exception as exc:
            print(f"  [ERROR] {exc}")
            errors += 1

        print()

    print(f"{'=' * 64}")
    if errors:
        print(f"FAILED -- {errors} error(s).")
        sys.exit(1)
    else:
        print("All samples produced valid drafts. Stage 2 writer OK.")
    print(f"{'=' * 64}\n")


if __name__ == "__main__":
    run_tests()
