"""
The deterministic half of the image prompt (Track B, B3).

Without a LoRA nothing enforces visual consistency between cards. This template
does the job the 456 MB of weights was supposed to: Haiku writes ONLY the scene
paragraph, and the style, palette and exclusions are constants wrapped around it
so they cannot drift card to card — or be overridden from inside scene text
derived from a scraped article.

    STYLE_PREAMBLE          constant  (ART_PIPELINE.md §3.3)
    <scene paragraph>       Haiku
    PALETTE[classification] constant  (§3.4)
    CONTENT_EXCLUSIONS      constant  (§3.5)

STYLE_PREAMBLE and CONTENT_EXCLUSIONS are verbatim from the spec. They were
arrived at by test — do not paraphrase them.

Pure, offline, no model call.
"""

# ART_PIPELINE.md §3.3 — stated positively, never as negation. "no anti-aliasing"
# and "no 3d render" do nothing to a Gemini image model; naming the medium does.
#
# AMENDED 2026-07-31 on operator direction: "isometric view" was removed.
# Combined with the old palette's "deep near-black background" it reliably
# produced a floating cutaway-room diorama boxed in a black void — a game asset
# rather than an establishing shot. Camera and framing now live in COMPOSITION
# below, stated explicitly rather than left to the model. §3.3 in the spec was
# marked LOCKED; it has been updated to match this string.
STYLE_PREAMBLE = (
    "16-bit era Japanese RPG sprite art. Chunky visible pixels with hard-edged "
    "shading bands and a limited colour palette per element, in the manner of "
    "hand-drawn console sprite work — every edge stepped, never smooth. Dense, "
    "highly detailed background work with many small readable props."
)

# The guardrail that keeps every card the same KIND of picture.
#
# Without this the model chose its own framing per render, and the two ends of
# that range are not variations on a theme — one is a wide establishing shot of
# a place, the other is a doll's-house box floating in black. Consistency across
# the feed is exactly the job the LoRA was supposed to do, so it has to be said
# in words instead.
COMPOSITION = (
    "Wide establishing shot of the location, viewed from a three-quarter angle "
    "at roughly standing eye level, as a detailed side-scrolling game "
    "background. The scene fills the entire frame edge to edge and continues "
    "past all four edges — this is a place, not an object sitting in space. "
    "The person at the centre of the incident is in the mid-foreground, doing "
    "whatever the moment calls for, turned enough towards the camera to read "
    "clearly at roughly a third of the frame height, placed naturally within "
    "the scene rather than looming over it. Include only the figures the scene "
    "actually calls for — never add a seated diner, a bystander at a table or "
    "any other filler person the incident does not describe. "
    "Fill the whole canvas with the location: no floating "
    "diorama, no cutaway room, no isometric doll's-house box, no vignette, no "
    "border, no letterboxing, and never an empty dark void framing the scene."
)

# Physical coherence. Added 2026-07-31 after a fire-rescue render came back with
# water spraying from BOTH the nozzle and the middle of the hose, because the
# scene named the hosereel twice — once for the man aiming it, once for the two
# holding it. The model does not reason about causality; it renders each mention
# as its own event. So the rule is stated to the image model directly, in
# addition to the scene writer being told not to create the ambiguity.
PHYSICAL_COHERENCE = (
    "Everything in the frame must be physically possible in a single moment. "
    "One source per effect: water leaves a hose only at the nozzle, smoke rises "
    "only from what is burning, one light casts one set of shadows. An object "
    "held by several people is still ONE object, continuous along its length. "
    "Nothing appears twice. Objects rest on surfaces, hang from fixings, or are "
    "held in a hand — nothing floats. Every person is standing on a floor, "
    "sitting on something, or holding on to something solid; nobody hovers, "
    "leaps through empty air, or stands on nothing. Limbs, hands and fingers "
    "attach normally and are counted correctly. "
    "Aim must match the target: whatever someone points, throws, sprays or "
    "directs travels TOWARDS the thing it is meant for, and that thing lies in "
    "that direction within the frame. A jet of water aimed at a fire arcs at "
    "the fire, never off into empty air, and everyone looks at what they are "
    "reacting to."
)

# Locale anchor. Added 2026-08-02 on operator direction, after renders drifted to
# Hong Kong.
#
# The drift was earned, not random. Both this template and the scene writer used
# to describe Singaporean housing WITHOUT naming it — "a tower face of repeating
# grid windows", laundry poles, dense low-rise shopfronts. That description fits
# a Hong Kong tenement at least as well as an HDB block, and the model went with
# the more heavily represented one. The fix is to name the place: "HDB" and
# "Singapore Police Force" are specific enough to pull the whole frame with them,
# where a paragraph of generic description was not.
#
# Kept deliberately short. Every added sentence competes with the scene for the
# model's attention, and the operator's standing note is that over-specification
# makes frames emptier and more hallucinated, not more accurate. Three anchors,
# one line of negation, nothing else.
LOCAL_SETTING = (
    "The setting is Singapore. Residential towers are HDB public housing: pale "
    "rendered slab blocks with open-air common corridors behind low parapets, "
    "laundry poles angled out from the window sills, and an open pillared void "
    "deck at ground level. Any police are Singapore Police Force officers in "
    "dark navy blue. The people are Singaporean, with the distinct skin tones "
    "and features of all three major groups clearly present: Chinese, Malay and "
    "Indian. Malay women often wear a tudung headscarf and Malay men sometimes "
    "a black songkok cap; Indian residents have darker brown skin, an Indian "
    "woman perhaps in a sari or salwar kameez. Show Indian faces as readily as "
    "Chinese and Malay ones. Clothing and uniforms are contemporary Singaporean."
)

# ART_PIPELINE.md §3.5 — always present. Earlier attempts filled frames with
# garbled pseudo-signage, and a recognisable storefront in an image depicting an
# incident is a defamation exposure. Both are cheaper to prevent here than to
# catch after. Gemini has no negative prompt parameter, so this is stated inline.
#
# RELAXED 2026-07-31 on operator direction. The blanket "no text, no lettering,
# no signage, no numerals" was enforced hard for one round and the result was
# worse, not safer: with every menu board, notice and price list rendered as a
# blank colour panel, the frames read as empty and sterile, and the scene writer
# burned its word budget describing writing-free wall content instead of the
# incident. A coffeeshop with nothing written anywhere in it does not look like
# a coffeeshop.
#
# What is retained is the half that carries actual legal weight: no logos, no
# brand names, no identifiable real business. A recognisable storefront in an
# image depicting an incident is a defamation exposure, and that has nothing to
# do with whether a chalkboard has words on it. Garbled pseudo-lettering in the
# background is a cosmetic issue and is now accepted deliberately.
#
# NEGATION REMOVED 2026-08-02 on operator direction, after it rendered ITSELF.
# A render came back with "NAMING NO REAL BUSINESS" painted across a shop awning
# and repeated on a pillar notice: this block is the only part of the prompt that
# talks ABOUT signage and lettering, and the image model — which is deciding what
# to write on signs at that moment — took its words as the content to write.
# Intermittent (two earlier renders were clean) but unmistakable when it fires.
#
# The wording is now positive and describes only what signage IS. The defamation
# control is not weakened by this, because it was never the primary one: the
# scene writer is forbidden to invent business names at all, so the image model
# is never handed a real brand to draw. This block is the second layer, and a
# second layer that paints itself into the frame is worse than one phrased as a
# description.
CONTENT_EXCLUSIONS = (
    "All businesses in frame are invented and generic. Any lettering is "
    "incidental set dressing that belongs to this setting — a hand-written "
    "board, a notice, a painted marking — in English, naming only made-up "
    "places."
)

# ART_PIPELINE.md §3.4. Every palette shares the same bones — amber-dominant
# practical lighting, deepest tones falling to near-black, sparing accents — so
# the three read as one system on the feed.
#
# AMENDED 2026-07-31: "Deep near-black background" is gone. Read literally it
# is an instruction to put the scene ON a black backdrop, and that is what the
# model did — a lit room floating in a black surround. It is now a statement
# about the tonal range, and the shadow it describes has to belong to the
# location. Where the setting opens outdoors, daylight beyond the frontage is
# explicitly allowed: it reads bright against the dim interior, which is the
# look the operator approved, and it removes the standing contradiction between
# a palette that assumed night and scenes that kept choosing afternoon.
#
# The classification colours are LOCKED (CLAUDE.md hard constraints): they are
# referenced here and never altered.
#   heart  GOOD VIBES  #4ECDC4 teal-cyan
#   clown  ABSURDITIES #FFE66D bright yellow
#   dagger DARK EVENTS #FF6B6B coral red
_LIGHTING = (
    "Warm and naturalistic, evenly lit by whatever light sources that place "
    "actually has. Muted earthy palette of worn, lived-in surfaces. Soft "
    "natural contrast with warm mid-tone shadows; nothing crushed to black, "
    "no harsh spotlighting, no heavy vignette."
)

_PALETTES = {
    "heart": (
        f"{_LIGHTING} Sparing teal-cyan accents (#4ECDC4) picking out the warm "
        "focal points. Coral used only as a faint secondary."
    ),
    "clown": (
        f"{_LIGHTING} Sparing bright-yellow accents (#FFE66D) picking out the "
        "absurd detail. Teal used only as a faint secondary."
    ),
    "dagger": (
        f"{_LIGHTING} Sparing coral-red accents (#FF6B6B) at the focal point. "
        "Teal used only as a faint secondary."
    ),
}

# An unknown or missing classification gets the neutral absurdities palette
# rather than raising — an image with slightly wrong accents beats no image.
_DEFAULT_PALETTE_KEY = "clown"


def palette_for(classification) -> str:
    """Palette string for a classification. Unknown values fall back, never raise."""
    key = classification if isinstance(classification, str) else ""
    return _PALETTES.get(key.strip().lower(), _PALETTES[_DEFAULT_PALETTE_KEY])


def assemble_prompt(scene: str, classification) -> str:
    """
    Wrap a Haiku-written scene paragraph in the deterministic template.

    Order matters. Style, composition and physical coherence lead, so framing
    and the rules of the world are established before the model reads any scene
    content; the locale anchor sits immediately before the scene, so "Singapore"
    and "HDB" are the freshest thing in context when the scene names a place;
    the scene sits in the middle; palette and exclusions land last, where they
    are least likely to be forgotten. Nothing inside the scene text can displace
    any of the six.
    """
    scene_text = scene.strip() if isinstance(scene, str) else ""
    return "\n\n".join((
        STYLE_PREAMBLE,
        COMPOSITION,
        PHYSICAL_COHERENCE,
        LOCAL_SETTING,
        scene_text,
        palette_for(classification),
        CONTENT_EXCLUSIONS,
    ))
