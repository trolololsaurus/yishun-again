"""
Image generation + prompt template (art/generate_image.py, art/prompt_template.py).
Pure and offline — every external call is stubbed. No network, no API keys, no R2.

Run: .venv/Scripts/python.exe test_image_generation.py

Load-bearing properties:
  - a suppressed incident costs ZERO calls and is never marked refused
  - a refusal softens the scene rather than resending it (the filter is
    deterministic; resending buys identical refusals and identical bills)
  - a wrong-aspect return is REJECTED, never squashed to fit
  - the R2 URL is returned only after a HEAD confirms the object
  - nothing raises out of generate_image under any failure
"""
import importlib
import io
import re
from unittest import mock

from PIL import Image

tmpl = importlib.import_module("art.prompt_template")
gi = importlib.import_module("art.generate_image")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


# ── Stubs ────────────────────────────────────────────────────────────────────

class _TextPart:
    def __init__(self, text): self.text = text


class _AnthropicResponse:
    def __init__(self, text): self.content = [_TextPart(text)]


class _Messages:
    def __init__(self, owner): self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.boom:
            raise RuntimeError("anthropic exploded")
        return _AnthropicResponse(self._owner.scenes[
            min(len(self._owner.calls) - 1, len(self._owner.scenes) - 1)])


def scene(label: str) -> str:
    """
    A stub scene long enough to clear SCENE_MIN_CHARS.

    Tests that count Haiku calls must not accidentally trip the length-floor
    rewrite — otherwise every call assertion is off by one and the failure looks
    like a ladder bug.
    """
    return (f"{label}. A neighbourhood coffeeshop under its evening lights, formica "
            "tables in rows and stacked plastic stools along the margins, a server "
            "crossing the tiled floor with a tray, other diners bent over their bowls, "
            "the open frontage giving onto a street where a tower face of repeating "
            "grid windows rises beyond, laundry poles jutting from its balconies, and "
            "the last of the daylight going amber over the parked bicycles at the kerb "
            "while the ceiling strip lights hum on above the counter and its stacked "
            "crockery, a cat threading between the table legs, a stack of red plastic "
            "crates by the drinks stall, and a bus shelter half-visible further down "
            "the pavement past the row of parked motorcycles.")


class FakeAnthropic:
    def __init__(self, scenes=(scene("A quiet undercroft"),), boom=False):
        self.scenes, self.boom, self.calls = list(scenes), boom, []
        self.messages = _Messages(self)


class _Inline:
    def __init__(self, data): self.data = data


class _ImgPart:
    def __init__(self, data): self.inline_data = _Inline(data)


class _Content:
    def __init__(self, parts): self.parts = parts


class _Candidate:
    def __init__(self, parts, finish=None):
        self.content, self.finish_reason, self.safety_ratings = _Content(parts), finish, []


class _GenResponse:
    def __init__(self, parts, finish=None):
        self.candidates = [_Candidate(parts, finish)]
        self.prompt_feedback = None


class _Models:
    def __init__(self, owner): self._owner = owner

    def generate_content(self, **kwargs):
        self._owner.calls.append(kwargs)
        outcome = self._owner.outcomes[
            min(len(self._owner.calls) - 1, len(self._owner.outcomes) - 1)]
        if outcome == "refuse":
            return _GenResponse([], finish="SAFETY")          # no image part
        if outcome == "boom":
            raise ConnectionError("network down")
        if outcome == "corrupt":
            return _GenResponse([_ImgPart(b"not-a-png")])
        if outcome == "square":
            return _GenResponse([_ImgPart(png(800, 800))])
        return _GenResponse([_ImgPart(png(1600, 900))])       # "ok" — real 16:9


class FakeGenai:
    def __init__(self, outcomes=("ok",)):
        self.outcomes, self.calls = list(outcomes), []
        self.models = _Models(self)


class FakeR2:
    def __init__(self, head_length=None, head_type="image/png"):
        self.puts, self.heads = [], []
        self._head_length, self._head_type = head_length, head_type

    def put_object(self, **kwargs):
        self.puts.append(kwargs)

    def head_object(self, **kwargs):
        self.heads.append(kwargs)
        body = self.puts[-1]["Body"]
        return {"ContentLength": self._head_length if self._head_length is not None else len(body),
                "ContentType": self._head_type}


def png(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (13, 13, 13)).save(buf, format="PNG")
    return buf.getvalue()


R2_ENV = {
    "CF_R2_ACCOUNT_ID": "acct", "CF_R2_ACCESS_KEY_ID": "kid",
    "CF_R2_SECRET_ACCESS_KEY": "sec", "CF_R2_BUCKET_NAME": "yishun-assets",
}


def run(incident, *, scenes=(scene("A quiet undercroft"),), outcomes=("ok",),
        r2=None, budget=None, boom=False):
    a, g = FakeAnthropic(scenes, boom=boom), FakeGenai(outcomes)
    r2 = r2 if r2 is not None else FakeR2()
    with mock.patch.dict("os.environ", R2_ENV, clear=False), \
         mock.patch.object(gi.time, "sleep"):
        res = gi.generate_image(incident, anthropic_client=a, genai_client=g,
                                r2_client=r2, budget=budget)
    return res, a, g, r2


INCIDENT = {
    "slug": "yishun-cat-rescued-2026-07", "title": "Cat rescued from Yishun tree",
    "summary": "SCDF officers coaxed a cat down from a tree near a Yishun block on Tuesday evening.",
    "classification": "heart", "severity": 1, "area_name": "Yishun Ring Road",
}


# ── B3: template ─────────────────────────────────────────────────────────────

print("prompt template (B3) — style, palette and exclusions are constants:\n")

for cls, colour in (("heart", "#4ECDC4"), ("clown", "#FFE66D"), ("dagger", "#FF6B6B")):
    p = tmpl.assemble_prompt("A cat sits on a low parapet.", cls)
    check(f"{cls}: contains the verbatim style preamble", tmpl.STYLE_PREAMBLE in p)
    check(f"{cls}: contains the composition guardrail", tmpl.COMPOSITION in p)
    check(f"{cls}: contains the full exclusion string", tmpl.CONTENT_EXCLUSIONS in p)
    check(f"{cls}: carries its own locked colour {colour}", colour in p)
    others = {"#4ECDC4", "#FFE66D", "#FF6B6B"} - {colour}
    check(f"{cls}: does not carry another classification's colour",
          not any(o in p for o in others), f"-> {p}")
    check(f"{cls}: scene sits inside the template", "A cat sits on a low parapet." in p)
    check(f"{cls}: contains the physical-coherence block", tmpl.PHYSICAL_COHERENCE in p)
    check(f"{cls}: framing is established BEFORE the scene is read",
          p.index(tmpl.COMPOSITION) < p.index("A cat sits on a low parapet."))
    check(f"{cls}: the rules of the world land before the scene too",
          p.index(tmpl.PHYSICAL_COHERENCE) < p.index("A cat sits on a low parapet."))

# ── The regression that produced the floating-diorama render ─────────────────
#
# "isometric view" in the style preamble plus "deep near-black background" in
# the palette rendered a lit cutaway room boxed in a black void — a game asset,
# not an establishing shot. Both phrasings are gone and must stay gone.

print("\nframing guardrails — the diorama regression:\n")

_all_prompts = " ".join(tmpl.assemble_prompt("x", c) for c in ("heart", "clown", "dagger"))
check("no prompt instructs an isometric view", "isometric view" not in _all_prompts.lower())
check("no prompt asks for a near-black BACKGROUND",
      "near-black background" not in _all_prompts.lower(),
      "-> a backdrop instruction is what produced the void")
check("every prompt demands the scene fill the frame edge to edge",
      _all_prompts.lower().count("fills the entire frame") == 3)
for banned in ("floating diorama", "cutaway room", "doll's-house box",
               "letterboxing", "empty dark void"):
    check(f"every prompt rules out: {banned}", _all_prompts.lower().count(banned) == 3)
check("lighting is warm and evenly lit, not a spotlit void",
      _all_prompts.count("Warm and naturalistic, evenly lit") == 3)
check("nothing is crushed to black and there is no vignette",
      _all_prompts.count("nothing crushed to black") == 3)

check("exclusions land LAST, where they are least likely to be forgotten",
      tmpl.assemble_prompt("x", "clown").rstrip().endswith(tmpl.CONTENT_EXCLUSIONS))

# ── What the picture is about ────────────────────────────────────────────────
#
# Two distinct failures bracket this. A render came back with NO COCKROACH IN
# THE BOWL — a lovely picture of a coffeeshop with the story missing. Then a
# mall-opening story rendered as a woman recoiling from a prawn, because the
# prompt demanded a reactor and an oversized object that the story did not
# contain. Requiring both unconditionally manufactures an incident where there
# is none, so the rule now forks on what kind of story it is.

print("\nwhat the picture is about — incident vs place:\n")

check("the scene writer must decide which kind of story this is",
      "two kinds and they need different pictures" in gi._SCENE_SYSTEM_PROMPT)
check("a thing-happening story shows the thing, slightly oversized",
      "a touch larger than life" in gi._SCENE_SYSTEM_PROMPT)
check("oversizing stays in proportion",
      "never so large it overwhelms what holds it" in gi._SCENE_SYSTEM_PROMPT)
check("a place/occasion story makes the PLACE the subject",
      "the PLACE is the subject" in gi._SCENE_SYSTEM_PROMPT)
check("inventing a reactor for a place story is barred",
      "Do NOT invent a shocked bystander" in gi._SCENE_SYSTEM_PROMPT)
check("inventing a mishap for a place story is barred",
      "a mishap, or a dramatic object to fill the" in gi._SCENE_SYSTEM_PROMPT)
check("a calm busy picture is named the CORRECT answer for a place story",
      "is the correct answer" in gi._SCENE_SYSTEM_PROMPT)
check("the observed mall failure is recorded in the prompt itself",
      "a mall-opening story rendered as someone recoiling from" in gi._SCENE_SYSTEM_PROMPT)

# ── Register: expressive but grounded ────────────────────────────────────────
#
# Two failed calibrations bracket this. Too flat and the card reads as
# documentary; too broad and it reads as slapstick — sweat-drops, flailing arms,
# a face filling half the frame. The operator's reference sits between: a clearly
# legible reaction on someone still sitting normally at their table.

print("\nregister — expressive but grounded:\n")

check("a reaction is required only WHERE someone is reacting",
      "Where someone IS reacting" in gi._SCENE_SYSTEM_PROMPT)
check("slapstick is ruled out by name",
      "NO slapstick" in gi._SCENE_SYSTEM_PROMPT)
for tic in ("sweat-drops", "motion lines", "flailing limbs", "arms thrown in the air"):
    check(f"the cartoon tic is barred: {tic}", tic in gi._SCENE_SYSTEM_PROMPT)
check("posture stays natural for whatever the person is doing",
      "natural for what they are doing" in gi._SCENE_SYSTEM_PROMPT)
# The register itself used to hardcode the coffeeshop ("seated at the table…
# chopsticks or spoon still in hand", "nearby diners carry on with their meals"),
# which is a large part of why a mall opening rendered as a noodle scene.
for leak in ("chopsticks", "over the bowl", "nearby diners", "their own meals"):
    check(f"the register no longer hardcodes the coffeeshop: {leak!r}",
          leak not in gi._SCENE_SYSTEM_PROMPT.lower())
check("the template seats the figure in the scene rather than looming",
      "rather than looming over it" in tmpl.COMPOSITION)
check("the figure is sized to roughly a third of frame height",
      "a third of the frame height" in tmpl.COMPOSITION)

# ── Set dressing: busy, not blank ────────────────────────────────────────────
#
# Blanking every lettered surface made the frames read empty and sterile, and
# cost the scene writer its word budget. Text is allowed again; what is retained
# is the half with legal weight — no real named business.

print("\nset dressing — busy, and text is allowed:\n")

check("the setting must read busy and lived-in",
      "Busy and worked-in, never sparse" in gi._SCENE_SYSTEM_PROMPT)
check("blank walls are ruled out",
      "blank and bare" in gi._SCENE_SYSTEM_PROMPT)
check("the exclusion no longer bans text outright",
      "No text, no lettering" not in tmpl.CONTENT_EXCLUSIONS)
# Asserted by INTENT, not by literal wording. These used to pin the exact string
# "No logos, no brand names, no identifiable real business", which made the guard
# fail the moment the phrasing was fixed rather than the meaning changed.
check("the defamation control survives — businesses are invented, not real",
      "invented" in tmpl.CONTENT_EXCLUSIONS
      and "made-up" in tmpl.CONTENT_EXCLUSIONS)
check("setting-appropriate lettering is permitted as set dressing",
      "belongs to this setting" in tmpl.CONTENT_EXCLUSIONS)

# The blocks that the IMAGE MODEL can mistake for content must not be phrased as
# negations. CONTENT_EXCLUSIONS is the only part of the prompt that talks about
# signage and lettering, and while it was phrased "no identifiable real
# business... naming no real establishment" a render came back with "NAMING NO
# REAL BUSINESS" painted across an awning — the model was choosing sign text at
# that moment and used the instruction as the text. LOCAL_SETTING is held to the
# same rule because it is adjacent and describes the scene.
#
# COMPOSITION and PHYSICAL_COHERENCE deliberately KEEP their negations: they
# describe framing and physics, not writing, so there is nothing for the model to
# letter, and each one fixes a defect that was observed and corrected (floating
# diorama, water from mid-hose, figures standing on nothing).
_NEG = re.compile(r"\b(?:no|never|nothing|nobody|not)\b", re.I)
for _name in ("CONTENT_EXCLUSIONS", "LOCAL_SETTING"):
    _val = getattr(tmpl, _name)
    check(f"{_name} is phrased positively (it can be rendered as signage)",
          not _NEG.search(_val), f"-> {_val!r}")

# ── Setting generalisation ───────────────────────────────────────────────────
#
# The look was tuned entirely on one coffeeshop incident, and the constants
# absorbed that setting. A fire rescue rendered a woman eating noodles at a
# table beside Japanese menu boards; a carpark brawl rendered a man drinking
# coffee and reading a newspaper next to a body in a pool of blood. All three
# constants were naming coffeeshop furniture and the model obeyed literally.

print("\nsetting generalisation — the constants must not assume a coffeeshop:\n")

_all3 = " ".join(tmpl.assemble_prompt("x", c) for c in ("heart", "clown", "dagger"))
# "table" and "diner" survive only inside the prohibition ("never add a seated
# diner, a bystander at a table"), so match the PRESCRIPTIVE phrasings instead.
for leak in ("sits at a table", "menu board", "chalkboard", "price list",
             "open frontage", "terracotta", "wall tile", "other diners"):
    check(f"no coffeeshop furniture prescribed by the constants: {leak!r}",
          leak not in _all3.lower(), "-> found in template constants")
check("the framing names the incident's own subject, not a seated figure",
      "The person at the centre of the incident" in tmpl.COMPOSITION)
check("filler people are ruled out by name",
      "never add a seated diner" in tmpl.COMPOSITION)
# Was: 'check("signage the setting would not have is ruled out", "Do not add
# signage the setting would not have" in CONTENT_EXCLUSIONS)'. That sentence was
# a negation and is gone — see the leak note above. Positively phrased there is
# one clause, not two, and it is already asserted where lettering is permitted
# ("belongs to this setting"), so a second check here would only re-test the
# same string. What is worth pinning instead is that the block still SAYS
# something about lettering at all, rather than being emptied.
check("the exclusion still governs lettering",
      "lettering" in tmpl.CONTENT_EXCLUSIONS.lower())
check("the scene writer furnishes from the story's own setting",
      "the place this story actually names" in gi._SCENE_SYSTEM_PROMPT)
# The per-setting prop lists were deleted: they fixed cross-setting bleed but
# listed "coffeeshop" first, handing Haiku a kopitiam whenever the story was
# vague. That is how a mall opening became a noodle scene.
check("no per-setting prop list remains to bias the default",
      "coffeeshop: chalked boards" not in gi._SCENE_SYSTEM_PROMPT)
check("cross-setting imports are still barred, generically",
      "nothing borrowed from a different kind" in gi._SCENE_SYSTEM_PROMPT)
check("lettering is pinned to English (renders came back in Japanese)",
      "in English" in tmpl.CONTENT_EXCLUSIONS
      and "English lettering" in gi._SCENE_SYSTEM_PROMPT)

# ── Physical coherence ───────────────────────────────────────────────────────
#
# A fire-rescue render came back with water spraying from the nozzle AND from
# the middle of the hose, because the scene named the hosereel twice — once for
# the man aiming it, once for the two bracing it. The image model draws each
# mention as its own event, so the fix is on both sides: the scene writer must
# not create the ambiguity, and the image model is told the rule directly.

print("\nphysical coherence — an ambiguous mention becomes an impossible picture:\n")

_all_c = " ".join(tmpl.assemble_prompt("x", c) for c in ("heart", "clown", "dagger"))
check("one-source-per-effect is stated to the image model",
      _all_c.count("One source per effect") == 3)
check("the hose case is named explicitly",
      "water leaves a hose only at the nozzle" in tmpl.PHYSICAL_COHERENCE)
check("a shared object stays ONE object",
      "still ONE object, continuous along its length" in tmpl.PHYSICAL_COHERENCE)
check("duplication is barred", "Nothing appears twice" in tmpl.PHYSICAL_COHERENCE)
check("floating objects are barred", "nothing floats" in tmpl.PHYSICAL_COHERENCE)
check("limb and finger counts are called out",
      "counted correctly" in tmpl.PHYSICAL_COHERENCE)
# Second render fixed the double-jet but aimed the water away from the fire.
check("aim must match target", "Aim must match the target" in tmpl.PHYSICAL_COHERENCE)
check("the empty-air failure is named", "never off into empty air" in tmpl.PHYSICAL_COHERENCE)
# Third render fixed the aim and produced a man hovering between floors: the
# float rule covered objects but not people.
check("people are grounded, not just objects",
      "nobody hovers, leaps through empty air, or stands on nothing" in tmpl.PHYSICAL_COHERENCE)
check("the scene writer must give every figure a surface",
      "SAY WHAT EVERY PERSON IS STANDING ON" in gi._SCENE_SYSTEM_PROMPT)
check("gaze follows the reaction",
      "everyone looks at what they are reacting to" in tmpl.PHYSICAL_COHERENCE)
check("the scene writer must place the target with a direction",
      "STATE WHERE THE TARGET IS" in gi._SCENE_SYSTEM_PROMPT)
check("the scene writer is shown the shape of a correct clause",
      "aiming up and to the left at the burning window above them" in gi._SCENE_SYSTEM_PROMPT)

check("the scene writer is told WHY ambiguity breaks the render",
      "draws each thing you mention as its own event" in gi._SCENE_SYSTEM_PROMPT)
check("the observed hosereel failure is recorded in the prompt itself",
      "from the middle of the hose" in gi._SCENE_SYSTEM_PROMPT)
check("naming an object twice is barred",
      "NAME EACH OBJECT ONCE" in gi._SCENE_SYSTEM_PROMPT)
check("shared handling must be one clause",
      "three of them on one hosereel" in gi._SCENE_SYSTEM_PROMPT)
check("the scene writer runs a single-photograph self-check",
      "could be a single photograph of one real moment" in gi._SCENE_SYSTEM_PROMPT)
check("physical logic is established before the prop lists",
      gi._SCENE_SYSTEM_PROMPT.index("PHYSICAL LOGIC")
      < gi._SCENE_SYSTEM_PROMPT.index("A CROWDED, LIVED-IN PLACE"))

# ── Suppression ──────────────────────────────────────────────────────────────

print("\nsuppression — terminal, and free:\n")

sup_incident = dict(INCIDENT, tags=["suicide"])
res, a, g, r2 = run(sup_incident)
check("suppressed status", res.status == "suppressed", f"-> {res.status}")
check("suppressed url is None", res.url is None)
check("ZERO Haiku calls", len(a.calls) == 0, f"-> {len(a.calls)}")
check("ZERO image calls", len(g.calls) == 0, f"-> {len(g.calls)}")
check("ZERO R2 writes", len(r2.puts) == 0)
check("suppressed is never marked refused", res.status != "refused")
check("suppressed records no attempts", res.attempts == [], f"-> {res.attempts}")

# The amendment: no tag, but the summary says it.
res, a, g, _ = run(dict(INCIDENT, tags=["police"],
                        summary="Police said the death was an apparent suicide."))
check("phrase-only suppression also costs zero calls",
      res.status == "suppressed" and not a.calls and not g.calls)

# ── Happy path ───────────────────────────────────────────────────────────────

print("\nhappy path:\n")

res, a, g, r2 = run(INCIDENT)
check("status ok", res.status == "ok", f"-> {res.status} {res.attempts}")
check("url is the public R2 URL with a content version",
      res.url.startswith("https://assets.yishunagain.com/pixel-art/yishun-cat-rescued-2026-07.png?v="),
      f"-> {res.url}")
check("exactly one Haiku call", len(a.calls) == 1)
check("exactly one image call", len(g.calls) == 1)
check("the scene model is Haiku", a.calls[0]["model"] == gi.SCENE_MODEL)
check("16:9 was requested", "16:9" in str(g.calls[0].get("config")))
check("one attempt recorded, outcome ok", len(res.attempts) == 1
      and res.attempts[0]["outcome"] == "ok")
check("final_prompt carries the template", tmpl.STYLE_PREAMBLE in res.final_prompt)
check("R2 object is verified with a HEAD after the PUT", len(r2.heads) == 1)
check("uploaded as image/png", r2.puts[0]["ContentType"] == "image/png")
check("key is pixel-art/{slug}.png",
      r2.puts[0]["Key"] == "pixel-art/yishun-cat-rescued-2026-07.png")

# ── Cache busting ────────────────────────────────────────────────────────────
#
# Measured, not theorised: regenerating under a changed prompt still served the
# PREVIOUS bytes from cache, because the key is stable and max-age is a year.
# That silently defeats operator rectification, whose entire purpose is
# replacing an image someone has already seen.

print("\ncache busting — a stable key with a one-year TTL hides regenerations:\n")

import hashlib as _h  # noqa: E402

_a, _b = png(1600, 900), png(1344, 756)
r2a, r2b, r2c = FakeR2(), FakeR2(), FakeR2()
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    ua = gi.upload_to_r2(_a, "s", r2a)
    ub = gi.upload_to_r2(_b, "s", r2b)
    ua_again = gi.upload_to_r2(_a, "s", r2c)

check("the object key does NOT change (no orphaned objects)",
      r2a.puts[0]["Key"] == r2b.puts[0]["Key"] == "pixel-art/s.png")
check("the URL DOES change when the bytes change", ua != ub, f"-> {ua} vs {ub}")
check("the version is a hash of the content, not a timestamp",
      ua.endswith(_h.md5(_a).hexdigest()[:8]), f"-> {ua}")
check("identical bytes produce an identical URL (a no-op republish is stable)",
      ua_again == ua, f"-> {ua_again} vs {ua}")
check("the long max-age is retained for CDN performance",
      "max-age=31536000" in r2a.puts[0]["CacheControl"])

# ── Refusal ladder ───────────────────────────────────────────────────────────

print("\nrefusal ladder — each rung is materially different:\n")

res, a, g, _ = run(INCIDENT, scenes=(scene("one"), scene("two"), scene("three")),
                   outcomes=("refuse", "refuse", "ok"))
check("refused twice then ok returns status ok", res.status == "ok", f"-> {res.status}")
check("three attempts recorded", len(res.attempts) == 3, f"-> {len(res.attempts)}")
check("the first two are marked refused",
      [x["outcome"] for x in res.attempts] == ["refused", "refused", "ok"])
check("each rung issued a FRESH Haiku rewrite", len(a.calls) == 3, f"-> {len(a.calls)}")
check("rung 2 instructs aftermath-not-act",
      "aftermath" in a.calls[1]["messages"][0]["content"].lower())
check("rung 3 instructs environment-only, no human figures",
      "no human figures" in a.calls[2]["messages"][0]["content"].lower())
check("the API refusal reason is fed into the rung-2 rewrite",
      "SAFETY" in a.calls[1]["messages"][0]["content"])
check("the prompts actually differ between rungs",
      len({x["prompt"] for x in res.attempts}) == 3)

res, _, g, r2 = run(INCIDENT, scenes=(scene("s1"), scene("s2"), scene("s3")),
                    outcomes=("refuse", "refuse", "refuse"))
check("three refusals return status refused", res.status == "refused", f"-> {res.status}")
check("all three prompts are retained for the operator", len(res.attempts) == 3)
check("no fourth call is made", len(g.calls) == 3, f"-> {len(g.calls)}")
check("nothing was uploaded", len(r2.puts) == 0)
check("url is None on exhaustion", res.url is None)

# ── Other failure modes ──────────────────────────────────────────────────────

print("\nother failures — all statuses, no exceptions:\n")

res, _, g, _ = run(INCIDENT, outcomes=("corrupt", "corrupt"))
check("corrupt bytes -> invalid", res.status == "invalid", f"-> {res.status}")
check("validation retries exactly once", len(g.calls) == 2, f"-> {len(g.calls)}")

res, _, g, _ = run(INCIDENT, outcomes=("square", "square"))
check("wrong aspect -> invalid (never squashed)", res.status == "invalid")
check("the reason names the aspect problem",
      "aspect" in res.attempts[-1]["reason"].lower(), f"-> {res.attempts[-1]['reason']}")

res, _, g, _ = run(INCIDENT, outcomes=("boom", "boom"))
check("network failure -> transient", res.status == "transient", f"-> {res.status}")
check("transient retries exactly twice", len(g.calls) == 2, f"-> {len(g.calls)}")

res, _, _, r2 = run(INCIDENT, r2=FakeR2(head_length=3))
check("R2 HEAD length mismatch is not reported as success", res.status != "ok",
      f"-> {res.status}")
check("a short object never yields a URL", res.url is None)

res, _, _, _ = run(INCIDENT, r2=FakeR2(head_type="text/html"))
check("R2 HEAD content-type mismatch is not reported as success", res.status != "ok")

res, a, g, _ = run(INCIDENT, boom=True)
check("scene-writer failure -> transient, never raises", res.status == "transient")
check("no image call is made without a scene", len(g.calls) == 0)

res, _, _, _ = run(dict(INCIDENT, slug=""))
check("missing slug -> invalid, never raises", res.status == "invalid")

# ── render_prompt: the operator rectification path (B4b) ─────────────────────
#
# Deliberately shares nothing with the ladder. The operator wrote the prompt, so
# re-running Haiku would discard their edit; and softening it behind their back
# would render something they did not ask for and label it theirs.

print("\nrender_prompt — one fixed prompt, no Haiku, no ladder, no budget:\n")

PROMPT = "A tiled corridor at dusk with bicycles along the parapet."

g, r2 = FakeGenai(("ok",)), FakeR2()
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    res = gi.render_prompt(PROMPT, "yishun-x", genai_client=g, r2_client=r2)
check("render_prompt succeeds and returns the R2 URL",
      res.status == "ok"
      and res.url.startswith("https://assets.yishunagain.com/pixel-art/yishun-x.png?v="),
      f"-> {res.status} {res.url}")
check("it makes exactly ONE image call", len(g.calls) == 1)
check("it sends the operator's prompt VERBATIM, unrewritten",
      g.calls[0]["contents"] == PROMPT, f"-> {g.calls[0]['contents'][:60]!r}")
check("final_prompt echoes what was sent", res.final_prompt == PROMPT)
check("one attempt recorded", len(res.attempts) == 1 and res.attempts[0]["outcome"] == "ok")

g = FakeGenai(("refuse", "refuse", "ok"))
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    res = gi.render_prompt(PROMPT, "yishun-x", genai_client=g, r2_client=FakeR2())
check("a refusal is NOT softened and NOT retried", len(g.calls) == 1, f"-> {len(g.calls)} calls")
check("the refusal surfaces as status 'refused'", res.status == "refused")
check("the operator is told why the filter refused",
      "SAFETY" in (res.attempts[0].get("reason") or ""), f"-> {res.attempts[0]}")

g = FakeGenai(("square",))
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    res = gi.render_prompt(PROMPT, "yishun-x", genai_client=g, r2_client=FakeR2())
check("a wrong-aspect return is invalid, not retried",
      res.status == "invalid" and len(g.calls) == 1)

g = FakeGenai(("boom",))
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    res = gi.render_prompt(PROMPT, "yishun-x", genai_client=g, r2_client=FakeR2())
check("a network failure is transient, not retried",
      res.status == "transient" and len(g.calls) == 1)

check("an empty prompt is rejected without any call",
      gi.render_prompt("   ", "yishun-x", genai_client=FakeGenai(), r2_client=FakeR2()).status == "invalid")
check("a missing slug is rejected without any call",
      gi.render_prompt(PROMPT, "", genai_client=FakeGenai(), r2_client=FakeR2()).status == "invalid")

# Guardrail #5 must hold HERE, not only in the War Room's queue filter. The
# rectification UI excludes suppressed rows by construction, but the one check
# that must not fail cannot rest on a list filter staying correct forever.
SUICIDE = {"title": "Man found dead at Blk 737", "tags": ["police"],
           "summary": "Police said the death was an apparent suicide."}
g = FakeGenai(("ok",))
res = gi.render_prompt(PROMPT, "yishun-x", incident=SUICIDE,
                       genai_client=g, r2_client=FakeR2())
check("render_prompt suppresses a guardrail-#5 incident", res.status == "suppressed")
check("suppression costs ZERO image calls", len(g.calls) == 0, f"-> {len(g.calls)}")
check("a suppressed rectification yields no URL", res.url is None)
check("suppression works via the PHRASE check, with no suicide tag",
      "suicide" not in [t.lower() for t in SUICIDE["tags"]])

g = FakeGenai(("ok",))
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    res = gi.render_prompt(PROMPT, "yishun-x", incident={"title": "Cat rescued", "tags": []},
                           genai_client=g, r2_client=FakeR2())
check("a non-suppressed incident still renders normally", res.status == "ok")

with mock.patch.dict("os.environ", R2_ENV, clear=False):
    check("omitting incident keeps the old call signature working",
          gi.render_prompt(PROMPT, "yishun-x", genai_client=FakeGenai(("ok",)),
                           r2_client=FakeR2()).status == "ok")

_main_src = open("main.py", encoding="utf-8").read()
_rectify = _main_src[_main_src.index("async def rectify_incident_art"):
                     _main_src.index("async def autonomy_status")]
# A caller-declared suppression is still honoured — but as a RESULT, not as a
# status code. 422 is FastAPI's generic request-validation code: a missing
# X-Ops-Token header returns one, and so does a body that is valid JSON but not
# an object. The War Room used to map 422 -> status 'suppressed' and write that
# to incidents.image_status, which is terminal, excluded from
# RECTIFIABLE_STATUSES and has no operator override — so a transport fault
# permanently marked a published story as guardrail #5. Both halves are pinned
# here: suppression must come back in the body, and this handler must not use
# 422 for anything.
check("/art/rectify answers a caller-declared suppression with an ImageResult",
      'ImageResult(status="suppressed"' in _rectify
      and "guardrail #5" in _rectify.lower())
check("/art/rectify never signals suppression with HTTP 422",
      "status_code=422" not in _rectify)
check("/art/rectify passes the incident through for the real gate",
      "incident=incident" in _rectify)
check("/art/rectify never builds an AttemptBudget", "AttemptBudget" not in _rectify)
check("/art/rectify never calls the laddered generator",
      "generate_image(" not in _rectify)

_rp_src = open(gi.__file__, encoding="utf-8").read()
_rp = _rp_src[_rp_src.index("def render_prompt"):_rp_src.index("def generate_image")]
check("render_prompt never calls the scene writer", "write_scene" not in _rp)
check("render_prompt never touches the softening ladder",
      "_SOFTENING" not in _rp and "rung" not in _rp)
# The docstring NAMES AttemptBudget to explain its absence, so match on use:
# no budget parameter in the signature and no instantiation in the body.
_rp_sig = _rp[:_rp.index('"""')]
check("render_prompt takes no budget parameter", "budget" not in _rp_sig, f"-> {_rp_sig!r}")
check("render_prompt never instantiates an AttemptBudget", "AttemptBudget(" not in _rp)
with mock.patch.dict("os.environ", R2_ENV, clear=False):
    check("render_prompt never raises on any failure class",
          all(isinstance(gi.render_prompt(PROMPT, "s", genai_client=FakeGenai((o,)),
                                          r2_client=FakeR2()), gi.ImageResult)
              for o in ("refuse", "boom", "corrupt", "square")))

# ── Pass-level ceiling ───────────────────────────────────────────────────────

print("\nattempt ceiling — counts attempts, not incidents:\n")

res, a, g, r2 = run(INCIDENT, budget=gi.AttemptBudget(limit=0))
check("ceiling reached -> status skipped", res.status == "skipped", f"-> {res.status}")
check("no calls are made once the ceiling is hit", not a.calls and not g.calls)
check("skipped is distinguishable from refused/invalid",
      res.status not in ("refused", "invalid", "transient"))

b = gi.AttemptBudget(limit=2)
run(INCIDENT, scenes=(scene("s1"), scene("s2"), scene("s3")), outcomes=("refuse", "refuse", "ok"), budget=b)
check("a shared budget is consumed by ATTEMPTS across the ladder", b.used == 2, f"-> {b.used}")

b = gi.AttemptBudget(limit=3)
run(INCIDENT, budget=b)
run(INCIDENT, budget=b)
check("the same budget carries across incidents in a pass", b.used == 2, f"-> {b.used}")

check("the default ceiling is 40 attempts", gi.AttemptBudget().limit == 40)

# ── Lazy default clients ─────────────────────────────────────────────────────
#
# Every test above injects its clients, so the `if client is None` branches never
# execute. They shipped once referencing a function that does not exist
# (`_get_anthropic_client`; the real name is `_get_client`), which surfaced only
# on a live run. These assert the symbols resolve without calling them.

print("\nlazy default clients — the branches injection never reaches:\n")

import filters.stage2_writer as _sw  # noqa: E402

check("the Anthropic fallback resolves to a real symbol", callable(getattr(_sw, "_get_client", None)))
check("the R2 fallback exists", callable(getattr(gi, "_default_r2_client", None)))
check("the Gemini fallback exists", callable(getattr(gi, "_default_genai_client", None)))

_src = open(gi.__file__, encoding="utf-8").read()
check("generate_image imports the Anthropic client by its ACTUAL name",
      "import _get_client" in _src and "_get_anthropic_client" not in _src)

# ── Assembled prompt samples ─────────────────────────────────────────────────

print("\n" + "=" * 78)
print("ASSEMBLED PROMPT — one per classification")
print("=" * 78)
for cls in ("heart", "clown", "dagger"):
    print(f"\n--- {cls} ---")
    print(tmpl.assemble_prompt(
        "Two SCDF officers in orange coveralls look up at a tabby cat perched on a "
        "low parapet, an open-air corridor stretching behind them, evening light "
        "spilling from the windows of the tower face opposite.", cls))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
