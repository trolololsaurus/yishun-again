"""
Image generation (Track B, B2 + B3).

Replaces the removed SDXL/Modal/LoRA pipeline with a Gemini image call. Reads
the FINISHED incident — title, summary, classification, severity, area_name —
never the raw source articles, so the picture reflects what the card says.

    suppression gate  →  Haiku writes the scene  →  template wrap  →  Gemini
                      →  centre-crop  →  R2  →  URL

Spec: `docs/ART_PIPELINE.md`. Retry behaviour follows
`docs/IMAGE_RETRY_AND_RECTIFY.md` §6, which explicitly supersedes the
"IMAGE_MAX_RETRIES=1, never on a safety refusal" rule in
`docs/EDGE_CASES_AND_HARDENING.md` §3 → B2.

## Why the return is a structure, not `str | None`

The operator has to be able to rectify a failure in the War Room, and "it
didn't work" is not enough to act on — they need to know what was tried and
what was refused. `ImageResult.attempts` carries every prompt with its outcome.

## Why retries soften rather than repeat

Gemini's safety filter is deterministic for a given prompt. Sending the same
text three times buys three identical refusals and three billed calls. Each rung
of the ladder is therefore a fresh Haiku rewrite of the scene paragraph —
progressively removing what likely triggered the refusal — while the template
constants stay byte-identical.

## Model string is configuration

`IMAGE_MODEL` is read from the environment and never hardcoded. Google has moved
under this project twice and its own deprecation page calls published shutdown
dates "the earliest possible dates" (ART_PIPELINE.md §1.1). A model swap must be
a config change, not a redeploy.

If `gemini-3.1-flash-lite-image` turns out not to expose aspect-ratio control,
every call lands in `_ASPECT_MISMATCH` and this module returns `invalid` rather
than a squashed image — the observed dimensions are logged on every call so that
shows up immediately rather than as a silent gap. The fix is one env var:
`IMAGE_MODEL=gemini-3.1-flash-image`.

Nothing here raises. Every failure is a status.
"""

import hashlib
import io
import logging
import os
import re
import time
from dataclasses import dataclass, field

from art.prompt_template import assemble_prompt
from art.suppression import suppress_image
from filters.model_call import create_checked

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

IMAGE_MODEL  = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-lite-image")
IMAGE_WIDTH  = int(os.getenv("IMAGE_WIDTH", "1200"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "630"))

# The scene writer is Haiku only. The task is constrained rewriting over at most
# ~1600 characters of summary, not composition (ART_PIPELINE.md §3).
SCENE_MODEL = os.getenv("IMAGE_SCENE_MODEL", "claude-haiku-4-5-20251001")

# Hard timeout on both external calls. This path blocks an operator's approve
# click, so a hang is worse than a failure.
IMAGE_TIMEOUT_S = float(os.getenv("IMAGE_TIMEOUT_S", "30"))

# Per-outcome attempt caps (IMAGE_RETRY_AND_RECTIFY.md §2). Named constants, not
# `while not ok:` — an exhausted budget is a logged failure, never another try.
REFUSAL_MAX_ATTEMPTS    = 3   # each rung materially different; see _SOFTENING
TRANSIENT_MAX_ATTEMPTS  = 2   # same prompt, backoff
VALIDATION_MAX_ATTEMPTS = 1   # same prompt

# Counts ATTEMPTS across a pass, not incidents: three rungs per incident would
# let an incident-based ceiling of 25 bill 75 calls.
IMAGE_MAX_ATTEMPTS_PER_RUN = int(os.getenv("IMAGE_MAX_ATTEMPTS_PER_RUN", "40"))

# Prompt-injection bound. Scene text derives from scraped news, so it is hostile
# input by default: capped in length and screened for directive markers. The
# template always wraps it, so style, palette and exclusions cannot be displaced
# from inside it.
SCENE_MAX_CHARS = int(os.getenv("SCENE_MAX_CHARS", "1500"))

# Consistency floor. A 567-character scene and an 831-character scene are not
# two versions of one look — the short one renders sparse and empty. Under this,
# the scene is rewritten ONCE and then accepted regardless, because a thin
# picture beats a missing one.
SCENE_MIN_CHARS = int(os.getenv("SCENE_MIN_CHARS", "560"))
SCENE_MIN_RETRIES = 1

# Low, not zero. The scene is a description of a fixed place and event, so
# run-to-run variation is drift rather than creativity — at 0.7 the same
# incident wandered between mid-morning and late afternoon and between an open
# streetfront and an enclosed room. Not 0.0: identical phrasing across every
# card would make the whole feed look stamped from one template.
SCENE_TEMPERATURE = float(os.getenv("SCENE_TEMPERATURE", "0.25"))

_DIRECTIVE_MARKERS = (
    "ignore previous", "ignore the previous", "ignore all previous",
    "disregard previous", "disregard the above",
    "system:", "assistant:", "instead generate", "instead, generate",
    "new instructions", "override the",
)

R2_PREFIX     = "pixel-art"
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE", "https://assets.yishunagain.com")
R2_ENDPOINT_TEMPLATE = "https://{account}.r2.cloudflarestorage.com"

# Requested generation ratio. 1200x630 is 1.905:1, not a standard ratio, so we
# ask for 16:9 and centre-crop — a uniform 6.7% vertical trim that leaves the
# pixel grid intact (ART_PIPELINE.md §5).
REQUEST_ASPECT_RATIO = "16:9"
_TARGET_ASPECT = IMAGE_WIDTH / IMAGE_HEIGHT
_SOURCE_ASPECT = 16 / 9
# Generous enough to absorb the model rounding to a real pixel size, tight
# enough that a square return (1.0) can never pass.
ASPECT_TOLERANCE = 0.06


# ── Result contract ──────────────────────────────────────────────────────────

@dataclass
class ImageResult:
    """
    Outcome of one incident's image generation.

    status:
        ok          url is set and the object is verified in R2
        suppressed  guardrail #5 — terminal, never retried, never rectified
        refused     safety filter refused every rung of the ladder
        transient   network/timeout/5xx exhausted its attempts
        invalid     the model returned unusable bytes or the wrong aspect
        skipped     the pass-level attempt ceiling was reached

    `suppressed` and `ok` are terminal. The rest are what the War Room
    rectification queue is filtered on (B4b).
    """
    url:          str | None = None
    status:       str = "invalid"
    attempts:     list[dict] = field(default_factory=list)
    final_prompt: str = ""

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "attempts": self.attempts,
            "final_prompt": self.final_prompt,
        }


class AttemptBudget:
    """
    Pass-level ceiling on billed image attempts.

    Explicit object rather than module state so a pass owns its own budget and
    tests do not leak counts into one another.
    """

    def __init__(self, limit: int = IMAGE_MAX_ATTEMPTS_PER_RUN):
        self.limit = limit
        self.used = 0

    def available(self) -> bool:
        return self.used < self.limit

    def consume(self) -> None:
        self.used += 1


# ── The softening ladder (IMAGE_RETRY_AND_RECTIFY.md §3) ─────────────────────

_SOFTENING = (
    # Rung 1 — as written.
    "",
    # Rung 2 — aftermath, not act.
    "This scene was REFUSED by the image model's safety filter. Rewrite it to "
    "remove what most likely triggered that refusal: visible injury, blood, "
    "weapons, bodies, physical violence, acute distress. Keep the setting, the "
    "time of day, the mood and the non-violent props. Depict the aftermath "
    "rather than the act.",
    # Rung 3 — environment only.
    "This scene was REFUSED again. Rewrite it as ENVIRONMENT ONLY: no human "
    "figures at all. Keep the setting, the light, the atmosphere and any "
    "neutral objects. Do not depict the incident.",
)


# ── Scene writer ─────────────────────────────────────────────────────────────

_SCENE_SYSTEM_PROMPT = """You write a single scene paragraph for a pixel-art \
illustration of a local news incident in Yishun, Singapore.

You write ONLY the scene. Art style, colour palette and content exclusions are \
appended by a template you do not control and must not restate.

WHAT THE PICTURE IS ABOUT
Work out what this story is actually about, and make THAT the subject. There are
two kinds and they need different pictures:
- Centred on a THING HAPPENING — a cockroach in a bowl, a fire in a window, a \
rescue, a brawl. Show that thing clearly and unmistakably, a touch larger than \
life so it reads at card size, but never so large it overwhelms what holds it. \
Give whoever is reacting to it a clearly legible expression.
- Centred on a PLACE or an OCCASION — a mall opening, a new facility, a \
milestone, a closure. Then the PLACE is the subject: show it at its most \
characteristic and alive. There is no incident to depict and no one to react. \
Do NOT invent a shocked bystander, a mishap, or a dramatic object to fill the \
gap. A calm, busy, well-observed picture of the place is the correct answer.

Decide which kind this is before you write. Getting it wrong is the worst \
failure available: a mall-opening story rendered as someone recoiling from \
their food is not a near miss, it is a different story.

REGISTER — HONEST, NOT CARTOONISH
Whatever the scene, draw it as a real moment in a real place.
- NO slapstick: no cartoon sweat-drops, no motion lines, no flailing limbs, no \
arms thrown in the air, nobody recoiling half out of their seat.
- Where someone IS reacting, keep their posture natural for what they are doing.
- Background figures mostly carry on with whatever that place involves.

OUTPUT
- One paragraph of flowing natural-language prose, 4-6 sentences, 600-1000 \
characters. Dense and specific. A thin two-line scene produces a sparse, \
empty-looking image, so name concrete props, background figures and \
architecture rather than gesturing at them.
- Never comma-separated tag soup.
- No preamble, no explanation, no quotes around it. The paragraph only.

STAY IN YOUR LANE
Art style, camera angle, framing, colour palette and content exclusions are \
appended by the template. Do NOT write about any of them. Never mention pixel \
art, sprites, resolution, rendering, "16-bit", "pixel-perfect", "isometric", \
"wide shot", "the frame", "the viewer", or any colour scheme. Describe the \
PLACE and WHAT IS HAPPENING in it, as if writing a location note.

TIME OF DAY
Unless the incident states otherwise, set the scene in late afternoon or early \
evening, with interior lights already on. Keep it consistent card to card.

PHYSICAL LOGIC — THE SCENE MUST BE POSSIBLE
The image model does not reason about cause and effect. It draws each thing you \
mention as its own event, so an ambiguous description becomes an impossible \
picture. A fire-rescue scene that mentioned the hosereel twice — once for the \
man aiming it, once for the two bracing it — came back with water spraying from \
the nozzle AND from the middle of the hose.
- NAME EACH OBJECT ONCE. If several people handle one thing, say so in a single \
clause: "three of them on one hosereel, the father at the nozzle". Never give \
the same object a second mention in a separate sentence.
- ONE SOURCE PER EFFECT. Water leaves the nozzle and nowhere else. Smoke rises \
from the thing that is burning. Say where the effect starts and where it goes.
- Say what each person's hands are actually doing, and give them nothing else \
to hold.
- Keep counts consistent and state them once — "two sons", not sons mentioned \
again later.
- Everything rests on something, hangs from something, or is held. Nothing floats.
- SAY WHAT EVERY PERSON IS STANDING ON. Give each figure a floor, a stool, a \
step, a ledge — "on the corridor floor", "at the parapet". A figure described \
only by what they are doing gets drawn hovering in mid-air.
- STATE WHERE THE TARGET IS. If someone aims, throws, sprays or points at \
something, put the target and the actor in the same clause with a direction \
between them — "aiming up and to the left at the burning window above them". \
Without it the model draws the jet arcing off into empty sky. Everyone should \
be looking at the thing they are reacting to.
Before you finish, re-read your paragraph and ask whether it could be a single \
photograph of one real moment. If any part could be read two ways, rewrite it.

A CROWDED, LIVED-IN PLACE
Busy and worked-in, never sparse — furnished with what the place this story \
actually names would really contain, and nothing borrowed from a different kind \
of place. Ordinary English lettering is welcome where that setting would really \
have it, never a real brand or shop name. Do not describe walls or ground as \
blank and bare.

FORBIDDEN VOCABULARY
- No SDXL incantations: "masterpiece", "8k", "highly detailed", "(weight:1.2)".
- Never negate render settings. "no anti-aliasing", "no blur", "no 3d render" \
do nothing to this model. State what IS there.

CHARACTERS - this rule is load-bearing, getting it wrong produces blobs
- Describe people positively as JRPG sprites: expressive anime-styled face, \
large defined eyes, readable mouth and brow, hair rendered as distinct strands, \
clothing with visible fold shading. Size them large enough in frame that the \
expression reads clearly.
- The people are SINGAPOREAN, not Japanese. The sprite manner is only how they \
are drawn, never who they are. Everyday Singaporean clothing and real local \
uniforms — never a kimono, a school seifuku, or a Japanese-style police uniform \
or cap.
- Reflect Singapore's population. Across the figures in a scene, show a natural \
mix of Chinese, Malay and Indian people — with the DISTINCT skin tones and \
features of each, not one look tinted differently — the way a Yishun coffeeshop, \
void deck or corridor actually looks. Give each group a legible cue: a Malay \
woman in a tudung headscarf and a Malay man sometimes in a black songkok cap; an \
Indian person with darker brown skin, an Indian woman perhaps in a sari or \
salwar kameez; a Chinese uncle in a singlet. Make sure Indian men and women \
actually appear — they are the ones most often dropped — not only Chinese and \
Malay figures. Where the story does not fix a person's race, vary it; do not \
draw everyone the same race.
- NEVER write "faceless", "silhouette", "stylised figure", "no detailed facial \
features", or "small in frame". Those are workarounds for an older model and \
they destroy this one's output.

LOCAL VOCABULARY - name Singaporean things BY NAME, then add the detail. The \
old rule here said this model does not know HDB and to translate it away; that \
was an older model's workaround and it backfired. "A tower face of repeating \
grid windows" with laundry poles is Hong Kong tenement imagery, so that is what \
came back. Naming the place is what anchors it.
- HDB block   -> "HDB block", pale rendered slab, open-air common corridor behind a low parapet
- void deck   -> "void deck", the open pillared ground floor beneath an HDB block
- laundry     -> laundry poles angled out from the window sills
- coffeeshop  -> neighbourhood coffeeshop (kopitiam), formica tables, stacked plastic stools
- police      -> Singapore Police Force officers, dark navy blue uniform
Singapore, never Hong Kong: no neon sign canyons, no caged balconies, no \
vertical hanging shop boards.

WHAT YOU MAY INVENT
You may add generic setting and atmosphere consistent with the incident: time \
of day, weather, lighting, architecture, ambient props, passers-by.
You may NOT invent incident facts. No injuries, causes, outcomes, vehicles, \
named people or specific identifiers (block numbers, unit numbers, business \
names) that the incident does not state. When in doubt, describe the place \
rather than asserting the event.

Never depict a real identifiable person or business."""


def _incident_field(incident: dict, key: str, default: str = "") -> str:
    value = incident.get(key)
    return value if isinstance(value, str) and value.strip() else default


def _scene_user_message(incident: dict, softening: str, refusal_reason: str | None) -> str:
    severity = incident.get("severity")
    lines = [
        f"TITLE: {_incident_field(incident, 'title', '(untitled)')}",
        f"CLASSIFICATION: {_incident_field(incident, 'classification', 'clown')}",
        f"SEVERITY: {severity if isinstance(severity, int) else 'unknown'} (1-5)",
        f"AREA: {_incident_field(incident, 'area_name', 'Yishun')}",
        "",
        "SUMMARY:",
        _incident_field(incident, "summary", "(no summary)"),
    ]
    if softening:
        lines += ["", softening]
    if refusal_reason:
        lines += ["", f"The API gave this reason for the refusal: {refusal_reason}"]
    lines += ["", "Write the scene paragraph."]
    return "\n".join(lines)


def scene_is_safe(scene: str) -> bool:
    """
    Reject a scene paragraph that is over-length or carries directive markers.

    Not a content judgement — an injection bound. Scene text descends from
    scraped articles, and a crafted one could otherwise try to talk past the
    template.
    """
    if not isinstance(scene, str) or not scene.strip():
        return False
    if len(scene) > SCENE_MAX_CHARS:
        return False
    lowered = scene.lower()
    return not any(marker in lowered for marker in _DIRECTIVE_MARKERS)


def _trim_to_sentence(text: str, limit: int) -> str:
    """
    Cut to `limit` at the last sentence end, falling back to a word boundary.

    A word-boundary cut leaves the prompt ending mid-clause ("...that would,
    within"), which reads to the image model as an unfinished thought. Ending on
    a full stop costs a sentence and buys a coherent prompt.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > limit // 2:
        return window[:cut + 1]
    return window.rsplit(" ", 1)[0]


def _one_scene(incident: dict, client, softening: str,
               refusal_reason: str | None, nudge: str) -> str:
    response = create_checked(
        client,
        call="art.write_scene",
        model=SCENE_MODEL,
        max_tokens=768,
        temperature=SCENE_TEMPERATURE,
        system=_SCENE_SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": _scene_user_message(incident, softening, refusal_reason) + nudge}],
    )

    parts = getattr(response, "content", None) or []
    text = ""
    for part in parts:
        chunk = getattr(part, "text", None)
        if isinstance(chunk, str):
            text += chunk

    scene = re.sub(r"\s+", " ", text).strip().strip('"')
    if not scene:
        raise RuntimeError("art.write_scene: model returned no text")
    if not scene_is_safe(scene):
        # Truncate rather than fail outright — an over-long paragraph is far
        # more common than an injection, and the cap is what makes it safe.
        scene = _trim_to_sentence(scene, SCENE_MAX_CHARS)
        if not scene_is_safe(scene):
            raise RuntimeError("art.write_scene: scene rejected by injection screen")
    return scene


def write_scene(incident: dict, client, *, rung: int = 0,
                refusal_reason: str | None = None) -> str:
    """
    One scene paragraph for the given ladder rung.

    Rewritten at most SCENE_MIN_RETRIES times when it comes back under
    SCENE_MIN_CHARS, then accepted as-is. Bounded, never a loop: a thin scene is
    a worse picture, not a failure, and blocking on it would cost the card.

    Raises on an unusable response so the caller can classify it as transient.
    """
    softening = _SOFTENING[min(rung, len(_SOFTENING) - 1)]
    scene = _one_scene(incident, client, softening, refusal_reason, "")

    for _ in range(SCENE_MIN_RETRIES):
        if len(scene) >= SCENE_MIN_CHARS:
            break
        logger.info("art: scene was %d chars (floor %d) — rewriting once",
                    len(scene), SCENE_MIN_CHARS)
        scene = _one_scene(
            incident, client, softening, refusal_reason,
            "\n\nYour previous attempt was too short and would render as an "
            "empty scene. Write it again at 600-1000 characters, naming more "
            "concrete props, background figures and architecture.",
        )
    return scene


# ── Image call ───────────────────────────────────────────────────────────────

class _Refusal(RuntimeError):
    """The safety filter returned no image part. Retryable, but only softened."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _Transient(RuntimeError):
    """Network, timeout or 5xx. Retryable with the same prompt."""


class _Invalid(RuntimeError):
    """Bytes that are not a usable image, or the wrong aspect. Barely retryable."""


def _default_genai_client():
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=int(IMAGE_TIMEOUT_S * 1000)),
    )


def _refusal_reason(response) -> str:
    """Best available explanation for a response that carried no image."""
    bits = []
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block:
        bits.append(f"block_reason={block}")
    for candidate in (getattr(response, "candidates", None) or []):
        finish = getattr(candidate, "finish_reason", None)
        if finish:
            bits.append(f"finish_reason={finish}")
        for rating in (getattr(candidate, "safety_ratings", None) or []):
            category = getattr(rating, "category", None)
            probability = getattr(rating, "probability", None)
            if category and probability:
                bits.append(f"{category}={probability}")
    return ", ".join(str(b) for b in bits) or "no image part and no reason given"


def _extract_image_bytes(response) -> bytes | None:
    """
    The image payload, or None when the response carried no image part.

    Deliberately does not assume `candidates[0].content.parts[0].inline_data`
    exists — a safety refusal comes back shaped like a normal response with the
    image part simply absent, and that is a routine outcome rather than an
    exception.
    """
    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                return bytes(data)
    return None


def _call_image_model(prompt: str, client) -> bytes:
    """One billed generation. Raises _Refusal / _Transient; never returns empty."""
    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=REQUEST_ASPECT_RATIO),
        )
    except Exception:  # noqa: BLE001 — stubbed client in tests, or an SDK without ImageConfig
        config = {"response_modalities": ["IMAGE"],
                  "image_config": {"aspect_ratio": REQUEST_ASPECT_RATIO}}

    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL, contents=prompt, config=config,
        )
    except Exception as exc:  # noqa: BLE001
        raise _Transient(f"{type(exc).__name__}: {exc}") from exc

    data = _extract_image_bytes(response)
    if data is None:
        raise _Refusal(_refusal_reason(response))
    return data


# ── Crop and validation ──────────────────────────────────────────────────────

def crop_to_target(data: bytes) -> bytes:
    """
    Centre-crop a 16:9 render to exactly IMAGE_WIDTH x IMAGE_HEIGHT.

    Trims vertically then scales UNIFORMLY. Never scales non-uniformly: the old
    path did 1024x1024 -> resize(1200, 630), a 1:1 -> 1.9:1 squash that produced
    unevenly sized pixels and horizontal stretch, which for pixel art defeats
    the entire aesthetic.

    A model that ignored the aspect request raises _Invalid rather than being
    salvaged — a missing image is a placeholder, a mangled one is on the front
    page.
    """
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise _Invalid(f"payload did not decode as an image: {exc}") from exc

    width, height = img.size
    if width <= 0 or height <= 0:
        raise _Invalid(f"degenerate image size {width}x{height}")

    aspect = width / height
    logger.info("art: model returned %dx%d (aspect %.3f; requested %s)",
                width, height, aspect, REQUEST_ASPECT_RATIO)
    if abs(aspect - _SOURCE_ASPECT) > ASPECT_TOLERANCE:
        raise _Invalid(
            f"expected ~{_SOURCE_ASPECT:.3f} ({REQUEST_ASPECT_RATIO}), got "
            f"{aspect:.3f} from {width}x{height} — IMAGE_MODEL may not support "
            f"aspect-ratio control"
        )

    target_height = int(round(width / _TARGET_ASPECT))
    if target_height > height:          # already flatter than the target
        target_width = int(round(height * _TARGET_ASPECT))
        left = (width - target_width) // 2
        box = (left, 0, left + target_width, height)
    else:
        top = (height - target_height) // 2
        box = (0, top, width, top + target_height)

    img = img.convert("RGB").crop(box)
    # NEAREST throughout: it is the only resampling filter that preserves the
    # stepped edges the whole style depends on.
    img = img.resize((IMAGE_WIDTH, IMAGE_HEIGHT), Image.NEAREST)

    if img.size != (IMAGE_WIDTH, IMAGE_HEIGHT):
        raise _Invalid(f"post-crop size {img.size} != ({IMAGE_WIDTH}, {IMAGE_HEIGHT})")

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ── R2 ───────────────────────────────────────────────────────────────────────

def _default_r2_client():
    """
    boto3 against R2 from process env.

    Shaped after `ops/backend_health.py::check_r2` rather than the old
    `art/generate_pixel_art.py::_r2_client` — that one ran inside a Modal
    container reading a Modal secret, and importing it constructs a Modal App at
    import time.
    """
    import boto3

    account = os.environ["CF_R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_TEMPLATE.format(account=account),
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload_to_r2(data: bytes, slug: str, client) -> str:
    """
    PUT the object, then HEAD it and confirm size and content type.

    The verification is not ceremony: a zero-byte or truncated object with a
    valid URL renders as a broken image on the live site, which is worse than no
    image at all. The URL is returned only after the HEAD agrees.

    ## Why the URL carries a ?v= content hash

    The key is stable (`pixel-art/{slug}.png`) so regeneration overwrites in
    place and never orphans objects. But the object is served with a one-year
    max-age, and a stable URL plus a long TTL means a regenerated image never
    reaches anyone who already loaded the old one — measured, not theorised: a
    regeneration under a changed prompt was still served as the previous bytes
    from cache. That silently defeats operator rectification (B4b), where the
    whole point is replacing an image someone has already seen.

    Hashing the bytes into the query string keeps the long TTL, keeps one object
    per incident, and changes the URL exactly when the picture changes.
    """
    bucket = os.environ["CF_R2_BUCKET_NAME"]
    key = f"{R2_PREFIX}/{slug}.png"

    client.put_object(Bucket=bucket, Key=key, Body=data,
                      ContentType="image/png", CacheControl="public, max-age=31536000")

    head = client.head_object(Bucket=bucket, Key=key)
    length = head.get("ContentLength")
    ctype = (head.get("ContentType") or "").lower()
    if length != len(data):
        raise _Invalid(f"R2 HEAD ContentLength {length} != {len(data)} bytes sent")
    if ctype != "image/png":
        raise _Invalid(f"R2 HEAD ContentType {ctype!r} != 'image/png'")

    version = hashlib.md5(data).hexdigest()[:8]
    return f"{R2_PUBLIC_BASE}/{key}?v={version}"


# ── Public API ───────────────────────────────────────────────────────────────

def _attempt(n: int, prompt: str, outcome: str, reason: str = "") -> dict:
    return {"n": n, "prompt": prompt, "outcome": outcome, "reason": reason}


def render_prompt(prompt: str, slug: str, *, incident: dict | None = None,
                  genai_client=None, r2_client=None) -> ImageResult:
    """
    Render ONE fixed prompt for one slug. No Haiku, no ladder, no budget.

    This is the operator rectification path (B4b). It deliberately shares
    nothing with `generate_image`'s retry logic:

    - **No scene writer.** The operator has supplied the whole prompt, including
      the template blocks. Re-running Haiku would discard their edit.
    - **No softening ladder.** The ladder exists because the safety filter is
      deterministic, so an automatic retry must change the prompt to be worth
      anything. A human who has just rewritten the prompt has already made that
      judgement; softening it again behind their back would render something
      they did not ask for and label it as theirs.
    - **No AttemptBudget.** `IMAGE_MAX_ATTEMPTS_PER_RUN` bounds an unattended
      pass. This is one operator clicking one button, not a loop
      (IMAGE_RETRY_AND_RECTIFY.md §7).

    Never raises. A refusal comes back as `status='refused'` with the reason, so
    the operator can see the filter rejected their wording and edit again.

    ## Guardrail #5 is enforced HERE, not only in the UI

    Pass `incident` and the suppression gate runs before anything is spent. The
    War Room's rectification queue already excludes suppressed rows by
    construction, but "the one check that must not fail" must not depend on a
    list filter staying correct through future edits — this is the same argument
    that made `suppress_image` deterministic rather than tag-only. Omitting
    `incident` skips the check, so callers that have the incident should pass it;
    `suppress_image` fails closed, so a partial dict is safe.
    """
    if incident is not None and suppress_image(incident):
        logger.info("art.render_prompt: suppressed by guardrail #5 — no calls made")
        return ImageResult(status="suppressed", final_prompt=prompt)

    if not isinstance(prompt, str) or not prompt.strip():
        return ImageResult(status="invalid",
                           attempts=[_attempt(1, "", "invalid", "empty prompt")])
    if not isinstance(slug, str) or not slug.strip():
        return ImageResult(status="invalid", final_prompt=prompt,
                           attempts=[_attempt(1, prompt, "invalid", "missing slug")])

    try:
        if genai_client is None:
            genai_client = _default_genai_client()
        raw = _call_image_model(prompt, genai_client)
        data = crop_to_target(raw)
        if r2_client is None:
            r2_client = _default_r2_client()
        url = upload_to_r2(data, slug, r2_client)
    except _Refusal as exc:
        logger.warning("art.render_prompt: refused — %s", exc.reason)
        return ImageResult(status="refused", final_prompt=prompt,
                           attempts=[_attempt(1, prompt, "refused", exc.reason)])
    except _Invalid as exc:
        logger.warning("art.render_prompt: invalid — %s", exc)
        return ImageResult(status="invalid", final_prompt=prompt,
                           attempts=[_attempt(1, prompt, "invalid", str(exc))])
    except Exception as exc:                      # noqa: BLE001
        logger.warning("art.render_prompt: transient — %s", exc)
        return ImageResult(status="transient", final_prompt=prompt,
                           attempts=[_attempt(1, prompt, "transient",
                                              f"{type(exc).__name__}: {exc}")])

    return ImageResult(url=url, status="ok", final_prompt=prompt,
                       attempts=[_attempt(1, prompt, "ok")])


def generate_image(incident: dict, *, anthropic_client=None, genai_client=None,
                   r2_client=None, budget: AttemptBudget | None = None) -> ImageResult:
    """
    Generate, crop, upload and verify one incident's image.

    Never raises. Every failure mode is a status on the returned ImageResult,
    and publication is never blocked on the outcome — the frontend already
    degrades to the placeholder and og-default.jpg.
    """
    # Guardrail #5 first: zero Haiku calls, zero image calls, and this never
    # enters the retry path or the rectification queue.
    if suppress_image(incident):
        logger.info("art: suppressed by guardrail #5 — no calls made")
        return ImageResult(url=None, status="suppressed")

    slug = _incident_field(incident, "slug")
    if not slug:
        return ImageResult(status="invalid",
                           attempts=[_attempt(0, "", "invalid", "incident has no slug")])

    classification = _incident_field(incident, "classification", "clown")
    budget = budget or AttemptBudget()
    attempts: list[dict] = []
    prompt = ""

    refusals = transients = invalids = 0
    rung = 0
    refusal_reason: str | None = None

    while True:
        if not budget.available():
            logger.warning("art: attempt ceiling %d reached — publishing without an image",
                           budget.limit)
            attempts.append(_attempt(len(attempts) + 1, prompt, "skipped",
                                     f"IMAGE_MAX_ATTEMPTS_PER_RUN={budget.limit} reached"))
            return ImageResult(status="skipped", attempts=attempts, final_prompt=prompt)

        n = len(attempts) + 1
        budget.consume()

        try:
            if anthropic_client is None:
                from filters.stage2_writer import _get_client
                anthropic_client = _get_client()
            scene = write_scene(incident, anthropic_client,
                                rung=rung, refusal_reason=refusal_reason)
            prompt = assemble_prompt(scene, classification)
        except Exception as exc:  # noqa: BLE001
            transients += 1
            attempts.append(_attempt(n, prompt, "transient", f"scene writer: {exc}"))
            if transients >= TRANSIENT_MAX_ATTEMPTS:
                return ImageResult(status="transient", attempts=attempts, final_prompt=prompt)
            time.sleep(min(2 ** transients, 8))
            continue

        try:
            if genai_client is None:
                genai_client = _default_genai_client()
            raw = _call_image_model(prompt, genai_client)
            data = crop_to_target(raw)

            if r2_client is None:
                r2_client = _default_r2_client()
            url = upload_to_r2(data, slug, r2_client)

        except _Refusal as exc:
            refusals += 1
            refusal_reason = exc.reason
            attempts.append(_attempt(n, prompt, "refused", exc.reason))
            logger.warning("art: refusal on rung %d — %s", rung + 1, exc.reason)
            if refusals >= REFUSAL_MAX_ATTEMPTS:
                return ImageResult(status="refused", attempts=attempts, final_prompt=prompt)
            rung += 1
            continue

        except _Invalid as exc:
            invalids += 1
            attempts.append(_attempt(n, prompt, "invalid", str(exc)))
            logger.warning("art: invalid output — %s", exc)
            if invalids > VALIDATION_MAX_ATTEMPTS:
                return ImageResult(status="invalid", attempts=attempts, final_prompt=prompt)
            continue

        except Exception as exc:  # noqa: BLE001 — network, R2, anything else
            transients += 1
            attempts.append(_attempt(n, prompt, "transient", f"{type(exc).__name__}: {exc}"))
            logger.warning("art: transient failure — %s", exc)
            if transients >= TRANSIENT_MAX_ATTEMPTS:
                return ImageResult(status="transient", attempts=attempts, final_prompt=prompt)
            time.sleep(min(2 ** transients, 8))
            continue

        attempts.append(_attempt(n, prompt, "ok"))
        logger.info("art: generated %s on attempt %d (rung %d)", url, n, rung + 1)
        return ImageResult(url=url, status="ok", attempts=attempts, final_prompt=prompt)
