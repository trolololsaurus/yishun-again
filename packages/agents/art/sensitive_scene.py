"""
Guardrail #5 — respectful rendering for suicide / self-harm incidents.

`art/suppression.py` is still the DETECTOR — `suppress_image()` decides whether
an incident is a suicide / self-harm story. What changed (2026-08-09, operator
direction) is the POLICY once it is detected. It used to be "no image at all":
`pixel_art_url` stayed null and the frontend showed the placeholder. It is now a
single, fixed, deliberately non-graphic police-response tableau:

  * a low blue fast-deploy police privacy tent, panels shut, as the hero focal
  * several police officers (labelled POLICE, never "SPF"), men and women,
    Chinese/Malay/Indian; officers wear the standard police cap, never a tudung
  * blue-and-white police tape cordoning the area
  * a patrol car where the setting has road access ("if necessary")
  * the surrounding environment (HDB block / void deck / carpark / corridor)
    inferred ONLY as a place-type, never the act; the block's REAL number
    (`block_number`, else mined from the title/summary) painted on the facade —
    never an invented one

This is the convention Singapore media already use for such scenes: the response
and the cordon, never the person, the body or the method.

## Why the guardrail is not weakened by this

The guardrail's intent — never render graphic or inappropriate suicide imagery —
is kept airtight three ways:

  1. The scene is FULLY DETERMINISTIC. Haiku never writes it, so the scraped
     summary never becomes picture content. `infer_environment()` reads only a
     place-type keyword and `block_label()` only the address (block number);
     neither reads the act. Water settings deliberately fall through to the
     neutral foot-of-block default rather than depict water.
  2. It depicts only the aftermath cordon — never a body, the method, a fall,
     blood, a weapon, a victim or any distress. `scene_is_clean()` screens the
     assembled scene against a forbidden-word set and FAILS CLOSED.
  3. If anything is off — an unreadable incident, a scene that trips the clean
     check, or a safety refusal from the image model — the caller falls back to
     suppression (no image). We never mutate a sensitive scene to get one past
     the filter.

`SENSITIVE_INCIDENT_ART=suppress` restores the original no-image behaviour with
no code change. Any value other than the literal `respectful` is treated as
`suppress` (the more restrictive answer), so a typo cannot silently enable it.

Pure, offline, no model call, no I/O.
"""

import os
import re

# ── Mode switch ──────────────────────────────────────────────────────────────


def sensitive_art_mode() -> str:
    """
    'respectful' renders the tableau; anything else restores suppression.

    Read at call time, not import, so the rollback switch takes effect without a
    redeploy. Unknown / empty values resolve to 'suppress' — fail toward the
    original guardrail, never away from it.
    """
    mode = (os.getenv("SENSITIVE_INCIDENT_ART", "respectful") or "").strip().lower()
    return "respectful" if mode == "respectful" else "suppress"


# ── Environment inference (place-type only, never the act) ────────────────────

# (keyword substrings, setting clause, patrol-car fits). First match wins. Only
# the TYPE of place is inferred. Water terms (canal, pond, reservoir, drain) are
# intentionally absent so they fall through to the foot-of-block default rather
# than render water.
_ENVIRONMENTS = (
    (("multi-storey car", "multistorey car", "carpark", "car park", "parking"),
     "a multi-storey carpark deck, low concrete parapets and painted lot numbers, "
     "an HDB block rising behind it", True),
    (("void deck",),
     "an HDB void deck, the open pillared ground floor with banks of letterboxes "
     "and a notice board to one side", True),
    (("corridor",),
     "the open common corridor of an HDB block, a low painted parapet along one "
     "side and unit doors along the other", False),
    (("playground", "park", "garden"),
     "a neighbourhood green beside an HDB estate, a footpath, low shrubs and "
     "mature trees", True),
)

_DEFAULT_ENV = (
    "the landscaped grass at the foot of a mid-rise HDB block, a covered walkway "
    "to one side", True,
)


def infer_environment(incident) -> tuple[str, bool]:
    """(setting clause, patrol-car fits) inferred from the incident's place-type.

    Reads title + summary for a place-type keyword only. Never returns anything
    derived from the act. Any unreadable input yields the neutral default.
    """
    text = ""
    if isinstance(incident, dict):
        for key in ("title", "summary", "area_name"):
            value = incident.get(key)
            if isinstance(value, str):
                text += " " + value.lower()
    for keywords, clause, car in _ENVIRONMENTS:
        if any(k in text for k in keywords):
            return clause, car
    return _DEFAULT_ENV[0], _DEFAULT_ENV[1]


# ── The deterministic tableau ────────────────────────────────────────────────


def block_label(incident) -> str:
    """
    The incident's real block number as a bare token (e.g. '257', '800', '110A').

    Read from `block_number` first, else mined from the title / summary
    ("Block 257", "Blk 800", "Block 110A"). Empty string when none is stated —
    we never invent a number.
    """
    if not isinstance(incident, dict):
        return ""
    raw = incident.get("block_number")
    if raw is not None:
        m = re.match(r"^(?:blk\.?\s*|block\s*)?([0-9]{1,4}[a-zA-Z]?)$", str(raw).strip(), re.I)
        if m:
            return m.group(1).upper()
    text = ""
    for key in ("title", "summary"):
        v = incident.get(key)
        if isinstance(v, str):
            text += " " + v
    m = re.search(r"\b(?:block|blk\.?)\s*([0-9]{1,4}[a-zA-Z]?)\b", text, re.I)
    return m.group(1).upper() if m else ""


def sensitive_scene(incident) -> str:
    """
    The fixed, non-graphic police-response scene paragraph.

    The blue privacy tent is the focal point and is described shut, so nothing
    inside it is visible. Officers, tape and (where the setting has road access)
    a patrol car surround it. The environment and the real block number are the
    only things that vary — the block number is the incident's own, never
    invented.
    """
    env, car_fits = infer_environment(incident)
    block = block_label(incident)
    block_clause = (
        f" The block's facade carries its large painted block number, {block}, "
        "high up and clearly legible."
        if block else ""
    )
    car = (
        " A white police patrol car marked POLICE is parked at the kerb at the "
        "edge of the frame, its doors shut."
        if car_fits else ""
    )
    return (
        f"At {env}, Singapore police officers have set up a low blue fast-deploy "
        "police privacy tent, marked POLICE, as the clear focal point at the "
        "centre of the scene, its panels fully zipped shut so nothing inside is "
        f"visible.{block_clause} Several police officers in dark navy blue "
        "uniforms and standard police caps stand around it — men and women "
        "together, a natural mix of Chinese, Malay and Indian officers, one of "
        "them an Indian officer with darker brown skin — a couple speaking "
        "quietly, one writing on a clipboard, every one of them calm and "
        "unhurried. Blue-and-white police tape is strung between a lamp post and "
        f"a nearby railing to cordon the area off.{car} A few neighbourhood "
        "residents stand well back beyond the tape, watching quietly. The whole "
        "scene is still, subdued and respectful — an orderly police cordon, "
        "nothing dramatic taking place."
    )


# ── Defensive clean check ────────────────────────────────────────────────────

# The deterministic scene above contains none of these; the check exists so a
# future edit — to the scene or to an environment clause — cannot introduce the
# act, the body or a method without the caller noticing and falling back to
# suppression. Substring matching on purpose: over-rejection costs a placeholder.
_FORBIDDEN = (
    "body", "corpse", "dead", "death", "died", "dying", "blood", "bleed",
    "jump", "jumped", "fell", "fallen", "falling", "height", "noose", "hang",
    "hung", "knife", "blade", "wrist", "victim", "suicide", "self-harm",
    "self harm", "weapon", "injur", "wound",
)


def scene_is_clean(scene) -> bool:
    """
    True only when the scene names nothing of the act, the body or a method.

    Fails closed: a non-string, an empty string, or any forbidden substring
    returns False, and the caller falls back to suppression.
    """
    if not isinstance(scene, str) or not scene.strip():
        return False
    low = scene.lower()
    return not any(word in low for word in _FORBIDDEN)
