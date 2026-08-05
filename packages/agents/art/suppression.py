"""
Guardrail #5 — image suppression.

An incident about suicide or self-harm must never get a generated image. The
frontend already degrades to the `PIXEL ART · COMING SOON` placeholder and
`og-default.jpg`, so suppression costs nothing: `pixel_art_url` stays null, no
error, no retry.

Pure, offline, no model call, no I/O. Callers are wired in B2.

## Why this is not the tag-only gate in the prompt

`docs/ART_PIPELINE.md` §4 and Track B's B1 both specify a tag-only check:

    return bool(set(incident.get("tags", [])) & SUPPRESS_TAGS)

`docs/EDGE_CASES_AND_HARDENING.md` §1.2 supersedes that, and §3 (→ B1) says in
terms: "Do not ship the tag-only version." The reason is architectural. `tags`
is produced by the Haiku classifier, so a tag-only gate asks a model to be
reliable about the one check that must not fail — and it sometimes will not emit
a `suicide` tag on a suicide story. Every other check in this programme moved
toward deterministic verification; this is the same move.

So the gate is an OR, not an AND: the model's tag, or a deterministic phrase
match over the incident's own title and summary. It fires on the Blk 737 card
whether or not the classifier tagged it.

## Deliberately narrow

Severity, death count and classifier confidence are **not** consulted. That was
considered and rejected as over-broad. Fatalities, violence, fires, crime scenes
and severity-5 incidents all generate normally — only suicide and self-harm are
suppressed.

## Total function

Any input returns True or False. It never raises, because a crash here means the
gate did not run at all. Where the answer cannot be computed the result is
`True` (suppress) — for a suppression gate the restrictive answer is the safe
one (EDGE_CASES §2 rule 4, fail closed). A gate stuck open generates images for
exactly the stories it exists to protect.
"""

SUPPRESS_TAGS = frozenset({"suicide", "self-harm"})

# Substring matches against the lowercased title + summary. Substring rather
# than word-boundary matching on purpose: over-suppression is free (a
# placeholder), under-suppression is not.
SUPPRESS_PHRASES = (
    "suicide",
    "self-harm",
    "self harm",
    "took his own life",
    "took her own life",
    "took their own life",
)


def _normalise_tags(raw) -> set[str]:
    """
    Tags as a lowercased set. Tolerates None, a bare string, and sequences
    carrying non-strings — all of which have been seen in `raw_content`.

    Whitespace and underscores fold to hyphens so `self harm` and `self_harm`
    match the canonical `self-harm` tag.
    """
    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = [raw]
    try:
        items = list(raw)
    except TypeError:
        return set()

    out: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower().replace("_", "-").replace(" ", "-")
        if cleaned:
            out.add(cleaned)
    return out


import re

# A forensic "manner of death" enumeration. Pathology reports and cold-case
# retrospectives routinely list suicide alongside homicide and accident while
# ruling causes IN or OUT — "the case could not be conclusively ruled a
# homicide, suicide, or accident" — which is a statement about investigative
# uncertainty, not suicide content. It produced a real false positive:
# yishun-schoolgirl-murder-industrial-park-oct-1989 (Liang Shan Shan, a
# stranger homicide with no suicide/self-harm tag anywhere) was suppressed on
# this exact sentence.
#
# Narrow and specific on purpose, matching the asymmetry the module already
# documents: a match against this exemption must be unambiguous, because
# failing to exempt costs nothing (a false-positive placeholder) while
# over-exempting could let a genuine suicide story through. It only strips
# this literal triad phrase — every other occurrence of "suicide" anywhere
# else in the same text, including a second, real mention, is still caught.
_MANNER_OF_DEATH_TRIAD = re.compile(
    r"homicide,?\s*suicide,?\s*(?:or|and)\s*accident"
    r"|accident,?\s*suicide,?\s*(?:or|and)\s*homicide",
    re.IGNORECASE,
)


def _incident_text(incident: dict) -> str:
    """Lowercased title + summary, with the manner-of-death triad stripped.

    Missing or non-string fields contribute ''. See _MANNER_OF_DEATH_TRIAD for
    why the strip happens here rather than in SUPPRESS_PHRASES.
    """
    parts = []
    for key in ("title", "summary"):
        value = incident.get(key)
        if isinstance(value, str):
            parts.append(value)
    text = " ".join(parts).lower()
    return _MANNER_OF_DEATH_TRIAD.sub(" ", text)


def suppress_image(incident: dict) -> bool:
    """
    True when guardrail #5 blocks image generation for this incident.

    Two independent conditions, either sufficient:
      1. the classifier tagged it `suicide` or `self-harm`
      2. the incident's own title or summary contains a suppression phrase
    """
    try:
        if not isinstance(incident, dict):
            return True

        if _normalise_tags(incident.get("tags")) & SUPPRESS_TAGS:
            return True

        text = _incident_text(incident)
        return any(phrase in text for phrase in SUPPRESS_PHRASES)
    except Exception:  # noqa: BLE001 — a gate that raises is a gate that did not run
        return True
