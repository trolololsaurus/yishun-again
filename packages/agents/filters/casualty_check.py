"""
Deterministic deaths/injuries validation for Stage 2.

The classify prompt describes deaths and injuries as a LEGAL RECORD with STRICT
rules. This module is the reason not to take the model's word for it: a wrong
death count on a published incident is the single most damaging factual error
this archive can make, and it is exactly the kind the prompt's own warning list
("critical condition", "fighting for his life") shows the model is prone to.

Pure, deterministic, offline — no model call, no I/O.

## The contract, and why it only ever FLAGS

It never corrects the model's value and never overwrites it. Language is
ambiguous and this is a regex; a validator confident enough to rewrite a legal
record would be more dangerous than the error it prevents. So it answers one
narrower question — "does the source language agree with the number the model
returned?" — and when it does not, the row is flagged for a human. Ambiguity
flags. Absence of evidence flags nothing.
"""

import re

# ── Confirmed, past-tense death language ────────────────────────────────────
# Only outcomes that have already happened. Deliberately excludes anything that
# describes a state the victim might survive.
_CONFIRMED_DEATH = [
    r"\bwas killed\b", r"\bwere killed\b", r"\bwas found dead\b",
    r"\bfound (?:him|her|them)? ?dead\b", r"\bfound dead\b",
    r"\bpronounced dead\b", r"\bdeclared dead\b", r"\bcertified dead\b",
    r"\bdied\b", r"\bdies\b", r"\bdead on arrival\b",
    r"\bsuccumbed to (?:his|her|their) injuries\b",
    r"\bfatally (?:injured|stabbed|shot|struck|wounded)\b",
    r"\bkilled (?:in|by|after|when|during)\b",
    r"\bhis death\b", r"\bher death\b", r"\btheir deaths\b",
    r"\bthe deceased\b", r"\bdeath of\b",
]

# Explicitly NOT a death — the prompt's own exclusion list, plus the phrasings
# that most often read as one.
_UNCONFIRMED = [
    r"\bcritical condition\b", r"\bfighting for (?:his|her|their) li(?:fe|ves)\b",
    r"\bhospitalised\b", r"\bhospitalized\b", r"\btaken to hospital\b",
    r"\bconveyed to\b", r"\bserious but stable\b", r"\bstable condition\b",
    r"\bsuspected\b", r"\bfeared dead\b", r"\bpresumed dead\b",
    r"\bsevere injuries\b", r"\blife-threatening\b",
]

# Negations. "no one was killed" contains "was killed" but reports the opposite,
# so a bare keyword match would invert the meaning of the sentence.
_NEGATION = re.compile(
    r"\b(?:no|not|nobody|no-one|no one|none|never|without any|there were no)\b",
    re.IGNORECASE,
)
_NEGATION_WINDOW = 45      # chars before a match to scan for a negation cue

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# "three people were taken to hospital", "2 men were injured"
_INJURY_COUNT = re.compile(
    r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:other\s+|more\s+)?"
    r"(?:people|persons|person|men|man|women|woman|residents|workers|victims|others|"
    r"passengers|pedestrians|firefighters|officers)?\s*"
    r"(?:were|was|had been)?\s*"
    r"(?:been\s+)?"
    r"(?:injured|hurt|wounded|taken to hospital|sent to hospital|conveyed to hospital)\b",
    re.IGNORECASE,
)


def _matches(text: str, patterns: list[str]) -> list[str]:
    """Every pattern hit whose immediate left context carries no negation cue."""
    out = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            window = text[max(0, m.start() - _NEGATION_WINDOW):m.start()]
            if _NEGATION.search(window):
                continue
            out.append(m.group(0).strip().lower())
    return sorted(set(out))


def analyse(text: str) -> dict:
    """
    What the SOURCE says about casualties.

    Returns {confirmed_death, unconfirmed_only, death_phrases, unconfirmed_phrases,
    injury_count}. `injury_count` is the largest explicit count found, or None —
    largest because a follow-up report often restates a partial figure.
    """
    text = text or ""
    death = _matches(text, _CONFIRMED_DEATH)
    unconf = _matches(text, _UNCONFIRMED)

    counts = []
    for m in _INJURY_COUNT.finditer(text):
        # Same negation guard as the death patterns, and it is load-bearing here:
        # "no one was injured" matches \b(one)...(injured) and would otherwise be
        # read as an explicit count of 1 — inverting the sentence. Measured on
        # live queue rows, this was the single largest source of false flags.
        window = text[max(0, m.start() - _NEGATION_WINDOW):m.start()]
        if _NEGATION.search(window):
            continue
        tok = m.group(1).lower()
        counts.append(int(tok) if tok.isdigit() else _NUMBER_WORDS.get(tok, 0))
    counts = [c for c in counts if c > 0]

    return {
        "confirmed_death":     bool(death),
        "unconfirmed_only":    bool(unconf) and not death,
        "death_phrases":       death[:6],
        "unconfirmed_phrases": unconf[:6],
        "injury_count":        max(counts) if counts else None,
    }


def validate(source_text: str, deaths, injuries) -> dict:
    """
    Cross-check the model's deaths/injuries against the source language.

    Returns {"ok": bool, "flags": [ {field, reason, model_value, source_evidence} ]}.
    `ok` False means "a human should look at this", never "the model is wrong and
    here is the right number".

    Deliberately silent when the source says nothing: a story that never mentions
    casualties and a model that returned null are in agreement.
    """
    a = analyse(source_text)
    flags = []

    def _int(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    d, i = _int(deaths), _int(injuries)

    if d is not None and d >= 1 and not a["confirmed_death"]:
        flags.append({
            "field": "deaths", "model_value": d,
            "reason": ("model reported a death but no confirmed past-tense death "
                       "language appears in the source"),
            "source_evidence": a["unconfirmed_phrases"] or [],
        })
    if (d is None or d == 0) and a["confirmed_death"]:
        flags.append({
            "field": "deaths", "model_value": d,
            "reason": "source confirms a death but the model reported none",
            "source_evidence": a["death_phrases"],
        })

    src_inj = a["injury_count"]
    if src_inj is not None and i is not None and i != src_inj:
        flags.append({
            "field": "injuries", "model_value": i,
            "reason": f"source states an explicit injured count of {src_inj}",
            "source_evidence": [str(src_inj)],
        })
    if src_inj is not None and i is None:
        flags.append({
            "field": "injuries", "model_value": None,
            "reason": f"source states an explicit injured count of {src_inj} "
                      f"but the model reported none",
            "source_evidence": [str(src_inj)],
        })

    return {"ok": not flags, "flags": flags}
