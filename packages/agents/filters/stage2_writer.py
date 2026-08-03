"""
Stage 2 writer — Anthropic Claude (spec §4.3)

Two-call pipeline (both Haiku since the 2026-07 eval — see MODEL_WRITE):
  1. MODEL_CLASSIFY — classify the incident and extract structured
                      metadata (fast, cheap, deterministic fields)
  2. MODEL_WRITE    — write title, summary, SEO copy and slug
                      (env-overridable; STAGE2_WRITE_MODEL rolls back to Sonnet)

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

try:
    from filters.model_call import create_with_headroom
except ImportError:  # pragma: no cover
    # This module documents itself as runnable directly
    # (python packages/agents/filters/stage2_writer.py), which puts filters/ on
    # sys.path instead of packages/agents/, so the package-qualified import
    # cannot resolve. Both spellings must work.
    from model_call import create_with_headroom

load_dotenv(override=False)

logger = logging.getLogger(__name__)

MODEL_CLASSIFY = "claude-haiku-4-5-20251001"
# Env-overridable so a rollback is a config change, not a redeploy.
#
# Moved from claude-sonnet-4-6 after the 3.1 eval (tools/eval_l2_write.py) over 30
# real inputs — 15 single-source, 15 clustered with 3-9 sources. On the gate
# metric (ungrounded numbers and proper nouns, artifact-filtered) Haiku matched
# Sonnet on the multi-source half, 4 occurrences across 2/15 drafts vs Sonnet's 4
# across 3/15, and matched it on format compliance (100% on slug, title, and both
# SEO limits for both models). It also ran ~24% shorter and breached the 1600-char
# ceiling far less often (4/30 vs 10/29).
MODEL_WRITE    = os.getenv("STAGE2_WRITE_MODEL", "claude-haiku-4-5-20251001")

# ── Summary length: arithmetic, not an instruction the model self-polices ────
#
# The prompt has always said "as long as the sources genuinely support" with a
# ~1600 char ceiling. The 3.1 eval showed both models ignore it on merged
# multi-source input — Sonnet exceeded 1600 on 10 of 29 drafts (worst: 2765).
# So the budget is now computed and passed as a hard number per call.
#
# RATIO derived from 30 real approved summaries measured against their own source
# bodies. The distribution is strongly bimodal:
#     single-source  median 0.612   (p25 0.494, p75 0.754)
#     multi-source   median 0.104   (p25 0.086, p75 0.138)
# A ~6x gap, because a merged cluster carries far more text than any summary
# needs. Using the multi median would cap a 1343-char single source at 139 chars,
# so the SINGLE-source end is what binds. 0.75 is that half's p75: it clears 75%
# of real approved single-source summaries untruncated, while any cluster above
# ~2.1k source chars still saturates the 1600 ceiling (every multi item in the
# sample was >= 3633). A thin story cannot sprawl; a five-source cluster gets the
# full budget.
STAGE2_SUMMARY_RATIO = float(os.getenv("STAGE2_SUMMARY_RATIO", "0.75"))
SUMMARY_HARD_CEILING = 1600
# Floor, so a very short source cannot produce a two-sentence stub. Also keeps
# the module's own smoke test invariant (300 <= len(summary)) reachable.
SUMMARY_FLOOR = 400

# ── Output caps ─────────────────────────────────────────────────────────────
# Env-overridable because raising a cap is the entire fix for a truncation, and
# an operator should be able to apply it without a redeploy. Measured usage on
# the largest inputs in the archive: classify 167/512, write 763/2048 — so both
# carry ~2-3x headroom today. filters/model_call guards them anyway: it retries
# once at double, and a second truncation raises loudly instead of surfacing as
# an unparseable-JSON error.
CLASSIFY_MAX_TOKENS = int(os.getenv("STAGE2_CLASSIFY_MAX_TOKENS", "512"))
WRITE_MAX_TOKENS = int(os.getenv("STAGE2_WRITE_MAX_TOKENS", "2048"))

# MSM domains used by hype_meter computation (spec §7)
_MSM_DOMAINS = [
    "channelnewsasia", "straitstimes", "mothership", "stomp",
    "mustsharenews", "theindependent", "zaobao", "shinmin",
    "beritaharian", "tamilmurasu", "yahoo", "asiaone",
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

latitude/longitude: ALWAYS return null. Never estimate coordinates — they are
resolved downstream by a deterministic geocoder. Instead, capture location as
text with block-level precision where the source allows:
- block_number: the HDB block number ONLY (e.g. "349", "512C"). Null if no
  block is named. Never put street names or landmarks here.
- area_name: the most specific location text in the source — street name
  ("Yishun Avenue 11"), or a named place (hospital, mall, hawker centre, MRT
  station, park, e.g. "Khoo Teck Puat Hospital"), falling back to "Yishun".

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

SUMMARY RULES (SEO-optimised — length follows the sources, not a quota):
- Write rich, keyword-dense prose — as long as the sources genuinely support
  (a well-covered story can run to ~1600 chars / 5-9 sentences; a thinly-sourced
  one should be shorter). Never pad to length with unverified detail — a shorter,
  fully-grounded summary beats a longer one padded with invented specifics.
- Sentence 1: The hook — what happened, who, where (block-level if known)
- Sentence 2: Context and detail — how it unfolded, what led to it
- Sentence 3: Outcome — arrest, injury, outcome, community reaction
- Sentences 4-9 (as sources allow): Corroborating detail, quotes, timeline of developments, wider significance
- Naturally include: "Yishun", block number or street name, incident type keywords
- Written for Google — targets long-tail queries like "yishun stabbing 2024", "yishun cat killing"
- Do NOT use bullet points. Flowing prose only.
- Do NOT editorialize beyond dry wit. Facts first.
- PARAGRAPHS: separate paragraphs with a blank line (\\n\\n in the JSON string).
  2-4 sentences each, broken where the story turns — what happened / how it
  unfolded / the outcome or aftermath. A summary of 3 sentences or fewer stays
  a single paragraph. This is a formatting instruction, not a length one: do
  not add sentences to justify another paragraph.

Given source content, return JSON only:
{
  "title": string (max 120 chars, clickbait-native, Yishun must appear, not always first),
  "summary": string (SEO prose, paragraphs separated by "\\n\\n"; up to ~1600 chars, only as far as the sources support — never pad to length),
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
    """
    Count unique MSM DOMAINS in source_urls, capped at 5 (spec §7).

    Dedup by domain, matching classifiers/corroboration.py — two CNA links to
    the same story are one outlet, not two. (Legacy field: the frontend now
    derives lightning from corroboration_count, but the two implementations
    should not disagree.)
    """
    domains = {
        domain
        for url in source_urls
        for domain in _MSM_DOMAINS
        if domain in url
    }
    return min(5, len(domains))


def _compute_chaos_contribution(classification: str, severity: int) -> float:
    """Deterministic chaos contribution formula from spec §7."""
    multipliers = {"dagger": 3.0, "clown": 1.5, "heart": -1.0}
    return round(severity * multipliers.get(classification, 1.0), 2)


# Caps on the extra source text handed to Sonnet. A 5-source story adds roughly
# 3k input tokens at these limits — enough for the delta between reports without
# unbounded cost on heavily-covered incidents.
MAX_EXTRA_SOURCES  = 5
EXTRA_SOURCE_CHARS = 2_500


def _format_additional_sources(content: dict) -> str:
    """
    Render the other outlets' reports of the same incident.

    Multi-source stories used to reach Stage 2 as a single article: the
    aggregator kept every URL and timeline entry but only the primary's text, so
    a block number in one report, a charge detail in another and an eyewitness
    quote in a third were all invisible to the writer. The system prompt already
    asks for corroborating detail "as sources allow" — it simply never had more
    than one source to work from.

    Signal sources (EDMW/HWZ) are excluded upstream and must never appear here:
    guardrail #2 and spec §4.1 forbid quoting forum content.
    """
    articles = content.get("source_articles") or []
    primary_url = content.get("url", "")

    others = [
        a for a in articles
        if a.get("url") != primary_url
        and (a.get("content") or "").strip()
        and a.get("source_type") != "signal"
    ][:MAX_EXTRA_SOURCES]

    if not others:
        return ""

    parts = [
        f"\n\n---\nADDITIONAL REPORTS OF THE SAME INCIDENT ({len(others)}). "
        "Use them to corroborate the primary report and to add specifics it "
        "omits — block numbers, ages, charges, timings, quotes. Do not repeat "
        "the same fact twice, and never assert anything no source states."
    ]
    for i, a in enumerate(others, start=2):
        when = f" ({a['date']})" if a.get("date") else ""
        parts.append(
            f"\n\n[{i}] {a.get('source_name') or 'unknown'} — {a.get('url', '')}{when}\n"
            f"Title: {a.get('title', '')}\n"
            f"{(a.get('content') or '')[:EXTRA_SOURCE_CHARS]}"
        )
    return "".join(parts)


# ── Source text + summary budget ────────────────────────────────────────────

def _non_signal_articles(content: dict) -> list[dict]:
    """Guardrail #2: signal (EDMW/Reddit) bodies are never source material."""
    return [a for a in (content.get("source_articles") or [])
            if a.get("source_type") != "signal"]


def non_signal_source_text(content: dict) -> str:
    """Everything the draft is allowed to be grounded in, concatenated."""
    parts = [content.get("title", ""), content.get("content", "")]
    for a in _non_signal_articles(content):
        parts.append(a.get("title", ""))
        parts.append(a.get("content", ""))
    return "\n".join(p for p in parts if p)


def summary_char_budget(content: dict) -> int:
    """
    The hard summary ceiling for THIS draft: min(1600, RATIO x source chars),
    floored. Deterministic — the model is told the number rather than asked to
    judge "as far as the sources support".
    """
    arts = _non_signal_articles(content)
    total = (sum(len(a.get("content") or "") for a in arts) if arts
             else len(content.get("content") or ""))
    return max(SUMMARY_FLOOR, min(SUMMARY_HARD_CEILING, int(STAGE2_SUMMARY_RATIO * total)))


# ── Groundedness post-check ─────────────────────────────────────────────────

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Two or more consecutive capitalised words: "Khoo Teck Puat", "Tower Transit".
_PROPER_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,}))+\b")


def find_ungrounded(summary: str, source_text: str, date_str: str = "") -> dict:
    """
    Numbers and capitalised multi-word proper nouns in `summary` that appear
    nowhere in the source text. Pure and deterministic.

    Deliberately CONSERVATIVE, because a false positive costs a full
    regeneration. Two classes of lexical artifact are excluded, both measured on
    the 3.1 eval's 60 real drafts:

      * 4-digit years — the incident year reaches the model through the `date`
        field, not the article body, so "in 2026" is grounded but would not match.
      * a phrase whose TAIL matches the source. A sentence-initial capital glues
        an ordinary word onto a real name ("On August", "As Jethro"), and a
        rewritten reference differs only by an article ("The Yishun Ring Road").

    The cost of that conservatism is missing an invention that merely extends a
    real name. That is the right direction: the flag blocks auto-publish, so a
    miss degrades to today's behaviour while a false positive burns a model call.
    """
    src_nums = {m.group().replace(",", "") for m in _NUM_RE.finditer(source_text or "")}
    src_nums |= set(re.findall(r"\d+", date_str or ""))
    src_low = (source_text or "").lower()

    # The incident date reaches the model as "2026-08-15", so a summary that
    # writes "in August" is grounded in a fact the source text does not spell.
    # Without this, "On August" / "In January" flag on every dated story.
    m = re.match(r"\d{4}-(\d{2})-\d{2}", date_str or "")
    if m and 1 <= int(m.group(1)) <= 12:
        full = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")[int(m.group(1)) - 1]
        src_low = f"{src_low}\n{full} {full[:3]}"

    numbers = []
    for m in _NUM_RE.finditer(summary or ""):
        v = m.group().replace(",", "").rstrip(".")
        if not v or v in src_nums:
            continue
        if len(v) == 4 and v.isdigit() and 1980 <= int(v) <= 2100:
            continue
        numbers.append(v)

    proper_nouns = []
    for m in _PROPER_RE.finditer(summary or ""):
        phrase = m.group().strip()
        if phrase.lower() in src_low:
            continue
        tail = phrase.split(None, 1)
        if len(tail) == 2 and tail[1].lower() in src_low:
            continue
        proper_nouns.append(phrase)

    return {"numbers": sorted(set(numbers)), "proper_nouns": sorted(set(proper_nouns))}


def _enforce_groundedness(client, content: dict, classification: dict, draft: dict):
    """
    Check the draft, regenerate ONCE on failure, flag if it fails again.

    Returns (draft, report). NEVER raises and never silently passes: a checker
    error or a failed regeneration both degrade to flagged, because "we could not
    verify this" and "this is verified" must not look the same downstream.
    """
    date_str = content.get("date") or ""
    try:
        src = non_signal_source_text(content)
        found = find_ungrounded(draft.get("summary", ""), src, date_str)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("Stage 2 groundedness check errored — flagging: %s", exc)
        return draft, {"checked": False, "flagged": True, "attempts": 1,
                       "reason": f"checker_error: {exc}"[:200]}

    if not (found["numbers"] or found["proper_nouns"]):
        return draft, {"checked": True, "flagged": False, "attempts": 1}

    logger.warning("Stage 2 groundedness: regenerating once — ungrounded %s", found)
    try:
        retry = _write_draft(client, content, classification)
        found2 = find_ungrounded(retry.get("summary", ""), src, date_str)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("Stage 2 groundedness regeneration failed — flagging: %s", exc)
        return draft, {"checked": True, "flagged": True, "attempts": 1, **found}

    if not (found2["numbers"] or found2["proper_nouns"]):
        logger.info("Stage 2 groundedness: regeneration recovered the draft")
        return retry, {"checked": True, "flagged": False, "attempts": 2, "recovered": True}

    logger.warning("Stage 2 groundedness: still ungrounded after retry — flagged %s", found2)
    return retry, {"checked": True, "flagged": True, "attempts": 2, **found2}


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
        f"URL: {content.get('url', '')}\n"
        f"Date: {content.get('date') or 'unknown'}\n\n"
        f"Title: {content.get('title', '')}\n\n"
        f"Content:\n{content.get('content', '')}"
        f"{_format_additional_sources(content)}"
    )


# ── Model calls ──────────────────────────────────────────────────────────────

def _classify(client: anthropic.Anthropic, content: dict) -> dict:
    """
    Haiku call: extract classification and structured metadata.
    Returns: classification, severity, block_number, area_name,
             latitude, longitude, tags, confidence.
    """
    response, _retried = create_with_headroom(
        client,
        call="stage2._classify",
        env_var="STAGE2_CLASSIFY_MAX_TOKENS",
        model=MODEL_CLASSIFY,
        max_tokens=CLASSIFY_MAX_TOKENS,
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

    # Legal guardrail #4 is read FIRST, before any field validation can raise.
    #
    # This used to sit below the classification/severity/confidence coercion,
    # and `result["classification"].lower()` threw AttributeError whenever the
    # model returned `"classification": null` — which is exactly what it tends
    # to do on a political story, because it is being told to reject rather
    # than categorise. The guardrail was therefore unreachable for a subset of
    # the very content it exists to catch: the candidate died on an exception,
    # so confidence was never forced to 0, the "[POLITICAL CONTENT DETECTED
    # — REJECT]" marker was never prepended, and the operator email and
    # `agent_events` warning row never fired. A silent crash is worse than a
    # silently-zeroed row, which is the failure the 2026-07-30 alerting was
    # added to fix in the first place.
    #
    # Observed live 2026-08-02 on an MP-resignation article surfaced by the
    # WordPress search source.
    result["political"] = bool(result.get("political", False))
    if result["political"]:
        result["confidence"] = 0.0
        logger.warning("Stage 2 [classify] political content detected — confidence forced to 0")

    classification = result.get("classification")
    classification = classification.lower() if isinstance(classification, str) else ""
    if classification not in ("heart", "clown", "dagger"):
        if not result["political"]:
            raise ValueError(f"Invalid classification: {result.get('classification')!r}")
        # Political rows are rejected on confidence, not on category, but the
        # column is NOT NULL downstream — give it a valid placeholder so the
        # guardrail's own reject path can complete and alert.
        logger.warning(
            "Stage 2 [classify] political content returned classification=%r — "
            "defaulting to 'dagger' so the guardrail-#4 reject path completes",
            result.get("classification"))
        classification = "dagger"
    result["classification"] = classification

    result["severity"]   = max(1, min(5, int(result.get("severity") or 1)))
    result["confidence"] = 0.0 if result["political"] else \
        max(0.0, min(1.0, float(result.get("confidence") or 0.0)))

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


# Month abbreviations for slug date suffixes — fixed list, no locale/strftime.
_SLUG_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec")

# A trailing date the model may append to a slug: "-jun-2024", "-2024", etc.
_SLUG_DATE_SUFFIX = re.compile(
    r'-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)?-?(?:19|20)\d{2}$'
)


def _sanitise_slug_charset(slug: str) -> str:
    """
    Force the LLM-authored slug into ^[a-z0-9-]+$ before it can reach
    incidents.slug. The model occasionally emits unicode, spaces or '/' —
    a published row with such a slug is unroutable (the frontend's slug
    sanitiser strips those characters, so the page 404s forever).
    """
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    return re.sub(r"-{2,}", "-", slug).strip("-")


def _stamp_slug_date(slug: str, date_str: str | None, max_len: int = 70) -> str:
    """
    Force the slug's trailing -month-year to match the authoritative incident
    date, overwriting whatever the model produced.

    The model is never given the incident date (see _build_user_message pre-fix),
    so it guessed the slug's year — pattern-matching the prompt's examples or the
    article's content — and 2026 incidents shipped at -2020/-2024/-2025 URLs. The
    date is data, not a creative choice, so it is stamped deterministically here.

    date_str: "YYYY-MM-DD" from content["date"]. If absent/unparseable, any
    trailing date the model guessed is stripped and the base returned, so a
    dateless item never carries a fabricated year (its real date is set later at
    operator approval).
    """
    slug = _sanitise_slug_charset(slug)
    base = _SLUG_DATE_SUFFIX.sub("", slug).rstrip("-")

    m = re.match(r'(\d{4})-(\d{2})-\d{2}', date_str or "")
    if not m or not (1 <= int(m.group(2)) <= 12):
        return base or slug  # dateless / unparseable: no fabricated year
    suffix = f"{_SLUG_MONTHS[int(m.group(2)) - 1]}-{m.group(1)}"

    base = base[:max_len - len(suffix) - 1].rstrip("-")   # leave room for -suffix
    return f"{base}-{suffix}"


def _write_draft(client: anthropic.Anthropic, content: dict, classification: dict) -> dict:
    """
    Sonnet call: write title, summary, SEO copy and slug.
    Classification context from Haiku is passed in the user message so
    Sonnet writes content consistent with the determined incident type.
    """
    # Provide classification as context so the tone matches, plus the computed
    # summary budget for THIS draft. The number is arithmetic (see
    # summary_char_budget) rather than a judgement the model has to make — the
    # 3.1 eval showed the prose ceiling being ignored on multi-source input.
    budget = summary_char_budget(content)
    user_msg = (
        f"{_build_user_message(content)}\n\n"
        f"---\n"
        f"Incident already classified as: {classification['classification'].upper()}, "
        f"severity {classification['severity']}. "
        f"Reflect this classification in your title and summary.\n"
        f"HARD LIMIT: the summary must be at most {budget} characters. This is "
        f"derived from how much source material actually exists for this story — "
        f"do NOT pad to reach it, and never add detail no source states."
    )

    calibration_hints = _load_calibration_hints()
    response, retried = create_with_headroom(
        client,
        call="stage2._write_draft",
        env_var="STAGE2_WRITE_MAX_TOKENS",
        model=MODEL_WRITE,
        max_tokens=WRITE_MAX_TOKENS,
        temperature=0.4,
        system=STAGE2_SYSTEM_PROMPT + calibration_hints,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text
    logger.debug("write raw: %s", raw[:500])

    result = _parse_json(raw)
    if retried:
        # Recovered, so the draft is complete and valid — this does NOT block
        # publishing. It is recorded because a recurring retry means the cap is
        # too tight for the shape of story now arriving, and that is only
        # visible if each occurrence leaves a trace on the row.
        result["_write_truncation_retry"] = {"cap": WRITE_MAX_TOKENS,
                                             "retried_at": WRITE_MAX_TOKENS * 2}

    # pixel_art_prompt is deliberately NOT required: it is no longer requested in
    # the system prompt (the War Room approve route hardcodes pixel_art_url=None,
    # so nothing consumed it) and a model that still volunteers it must not be
    # treated as valid-or-invalid on that basis. Absence is the expected case.
    required = ("title", "summary", "slug", "seo_title", "seo_description")
    for key in required:
        if key not in result:
            raise ValueError(f"Write response missing required field '{key}'")

    # (Political-content detection now happens in _classify, which force-sets
    # confidence=0; write_stage2 prepends the operator-visible reject marker.)

    # Enforce spec field-length constraints (truncate rather than error)
    if len(result["title"]) > 120:
        result["title"] = result["title"][:120]
    # Stamp the slug's date from the authoritative incident date, never the
    # model's guess (also enforces the 70-char limit). See _stamp_slug_date.
    result["slug"] = _stamp_slug_date(result["slug"], content.get("date"))
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
        come from Haiku. Creative fields (title, summary, SEO, slug) come from
        Sonnet. hype_meter and chaos_contribution are computed in Python.
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

    # ── Step 2: Write draft ──────────────────────────────────────────────────
    if classification.get("political"):
        # Guardrail #4, continued. _classify (above) already forces confidence
        # to 0 and survives a null/invalid classification — but write_stage2
        # used to call the writer model UNCONDITIONALLY regardless of that
        # flag, asking it to draft dry tabloid copy about an "incident" that,
        # by definition, isn't one. Haiku correctly refuses outright with
        # plain prose ("I cannot process this submission... Out of scope...")
        # instead of JSON, and `_parse_json` then raises "No JSON object in
        # model response" — an exception with NO try/except around it here.
        #
        # That propagated uncaught into the cluster-write phase
        # (ingestion/orchestrator.py), which has no way to tell a genuine
        # transient failure from a deterministic refusal: it holds the whole
        # cluster `unresolved` and retries it. A political candidate never
        # stops refusing, so it — and every candidate merged into the same
        # cluster, innocent or not — gets stuck behind the watermark's retry
        # floor and re-spends a Haiku write call on the identical refusal
        # every single day. Observed live 2026-08-03, the same MP-resignation
        # article that first exposed the guardrail-#4 crash in `_classify`.
        #
        # A political row is never read: confidence is already 0, and the
        # marker appended below makes the rejection unmissable. There is
        # nothing for a drafted title/summary to accomplish, so skip the model
        # call and synthesize the handful of fields the rest of this function
        # (and its callers) require.
        logger.info("Stage 2 [write] skipped — political content (guardrail #4)")
        draft = {
            "title":           (content.get("title") or "Political content")[:120],
            "summary":         "",
            "slug":            _stamp_slug_date("political-content-not-drafted",
                                                content.get("date")),
            "seo_title":       (content.get("title") or "Political content")[:60],
            "seo_description": "Rejected under guardrail #4 — political content "
                               "is never drafted or published."[:155],
        }
        grounding = {"checked": False, "flagged": False, "skipped": "political"}
    else:
        logger.info("Stage 2 [write] calling %s (summary budget %d chars)",
                    MODEL_WRITE, summary_char_budget(content))
        draft = _write_draft(client, content, classification)

        # ── Step 2b: Groundedness — regenerate once, then flag ───────────────
        draft, grounding = _enforce_groundedness(client, content, classification, draft)

    # ── Step 3: Compute deterministic fields ─────────────────────────────────
    source_urls  = content.get("source_urls", [content.get("url", "")])
    hype_meter   = _compute_hype_meter(source_urls)
    chaos        = _compute_chaos_contribution(
                       classification["classification"], classification["severity"]
                   )

    # ── Step 3b: Geocode if lat/lon still null after Haiku ───────────────────
    lat = classification.get("latitude")
    lon = classification.get("longitude")
    # No block/area guard: the geocoder mines an address out of the title and
    # summary when both columns are null, and returns immediately (no HTTP) when
    # there is nothing usable anywhere. Guarding on the columns is what left
    # block-in-the-headline stories unpinned.
    if lat is None or lon is None:
        try:
            from classifiers.geocoding import geocode_incident
            coords = geocode_incident(
                classification.get("block_number"),
                classification.get("area_name"),
                extra_text=draft.get("title"),
                location_text=draft.get("summary"),
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
        # Carry the candidate date through to consolidation. consolidation.check
        # runs on this draft alone (not the orchestrator's `item`), and its
        # _judge_pair uses date proximity to decide same-incident — but the date
        # was never in the draft, so the judge always saw 'unknown' and lost that
        # signal for every source. It bit reddit hardest: reddit titles are
        # casual and overlap MSM headlines weakly, so the date was the
        # disambiguator that would have linked a reddit post to its existing
        # incident instead of minting a duplicate. Only added when present so a
        # dateless item stays honestly dateless (and never overrides item['date']
        # with an empty value in build_queue_row's {**item, **draft} merge).
        **({"date": content["date"]} if content.get("date") else {}),
        # Legal guardrail #4 — political flag propagates to the queue row
        "political":          classification.get("political", False),
        # Groundedness verdict. build_queue_row spreads the draft into
        # raw_content, so this lands as raw_content._groundedness and
        # ops/auto_publish holds a flagged row for review.
        "_groundedness":      grounding,
    }

    # ── Deterministic deaths/injuries cross-check ────────────────────────────
    # Never corrects the model's value — deaths/injuries stay exactly as
    # classified above. It only records whether the SOURCE LANGUAGE agrees, and
    # a disagreement blocks auto-publish. Wrapped because a regex bug must not
    # take down a pass: on error the row is flagged, never silently passed.
    try:
        from filters.casualty_check import validate as _casualty_validate
        verdict = _casualty_validate(
            non_signal_source_text(content),
            classification.get("deaths"),
            classification.get("injuries"),
        )
        if not verdict["ok"]:
            logger.warning("Stage 2 casualty check disagrees with the source: %s",
                           verdict["flags"])
            result["_casualty_check"] = {"flagged": True, **verdict}
        else:
            result["_casualty_check"] = {"flagged": False, "ok": True, "flags": []}
    except Exception as exc:                      # noqa: BLE001
        logger.warning("Stage 2 casualty check errored — flagging: %s", exc)
        result["_casualty_check"] = {"flagged": True, "ok": False,
                                     "reason": f"checker_error: {exc}"[:200], "flags": []}

    # Political content: confidence is already 0 (forced in _classify); prepend the
    # operator-visible reject marker so it cannot be silently approved.
    if classification.get("political"):
        marker = "[POLITICAL CONTENT DETECTED — REJECT] "
        if not str(result.get("summary", "")).startswith(marker):
            result["summary"] = marker + str(result.get("summary", ""))
        # A DISTINCT marker, not just confidence 0. Under unattended operation a
        # confidence-0 row is indistinguishable from any other low-confidence row
        # — it never publishes and nothing is said. This is what lets the
        # orchestrator tell the two apart and raise an audible alert. It does not
        # weaken the guardrail: confidence is already forced to 0 in _classify and
        # nothing here can raise it.
        result["_political_flagged"] = {
            "detected_at":           "stage2_classify",
            "confidence_forced_to":  0.0,
            "source_url":            content.get("url", ""),
            "source_name":           content.get("source_name", ""),
        }

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
            print(f"  tags           : {result.get('tags', [])}")
            print(f"  block_number   : {result.get('block_number')}")
            print(f"  area_name      : {result.get('area_name')}")
            print(f"  deaths         : {result.get('deaths')!r}  (null=not mentioned, 0=confirmed none, N=confirmed)")
            print(f"  injuries       : {result.get('injuries')!r}")
            # pixel_art_prompt is no longer requested; report whether the model
            # volunteered it anyway, so a silent regression is visible here.
            print(f"  draft keys     : {sorted(result.keys())}")
            print(f"  pixel_art_prompt present? {'pixel_art_prompt' in result}")

            # Basic sanity checks
            assert "yishun" in result.get("title", "").lower(), "FAIL: 'Yishun' missing from title"
            assert 300 <= summary_len <= 1600, f"FAIL: summary length {summary_len} out of expected range"
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
