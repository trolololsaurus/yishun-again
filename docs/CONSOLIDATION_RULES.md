# CONSOLIDATION RULES (v1 — Wang Zhijian prototype)

These rules govern how incidents are corroborated, de-duplicated, dated, and
concluded. Every rule here is a CONSTRAINT that reduces invention. When in
doubt, do less: state what is verified, omit what is not.

## DATE RULES
1. Never trust an existing timeline. Verify every date against primary
   sources before writing it. (A prior pass hallucinated execution dates —
   claimed 2010, when court records show the man was alive and appealing in
   2014.)
2. `incident_date` = when the event actually happened, never when it was
   sentenced or reported. Each legal-process step (charge, trial, verdict,
   appeal) becomes its own `source_timeline` entry on its own date.
3. Court judgments at elitigation.sg are the gold source for any Singapore
   crime that went to trial — reachable and authoritative. Check there first
   for any case with a verdict.

## CONCLUSION RULES
4. Only accept a conclusion (verdict, sentence, execution, acquittal,
   release) that is EXPLICITLY stated in a source. Never infer it.
5. If the final outcome is not in any reachable source, do NOT fabricate it.
   End the timeline at the last verified fact, and state the limitation
   plainly in the summary text (e.g. "final outcome not publicly reported").

## LIFECYCLE STATE (two states only — keep it simple)
6. CONCLUDED: a verdict, sentence, or clear end-of-legal-road is reported.
   `is_developing = FALSE`, `conclusion_type = 'verdict'`.
7. DEVELOPING: a recent case actively moving through the courts, where new
   reports are expected. `is_developing = TRUE`, `conclusion_type = NULL`.
   (Do not add further states or tags yet. Revisit only when data volume
   genuinely justifies finer distinctions — and prefer letting the pattern
   emerge from the data over inventing categories up front.)

## SOURCE TIERING
8. `source_urls` stores only authoritative sources: court records,
   established mainstream news, Wikipedia. Grokipedia and wiki.sg are
   research aids only — use them to spider into deeper links, never store
   them as citations.

## PATTERN / PHENOMENON LINKING
9. When multiple incidents share a signature (same act-type, same location,
   same period), create ONE umbrella "phenomenon" card and chain individual
   sourced incidents to it via `incident_links`. The phenomenon card is the
   bigger-picture hub; the individual cards are the evidence. When a NEW
   incident later matches an existing phenomenon's signature, chain it to the
   existing hub rather than creating an orphan card.
10. An individual card REQUIRES its own source. Never manufacture individual
    cards from an aggregate count (e.g. "35 cats killed") — that count lives
    in the phenomenon card's summary, not as 35 fabricated cards. The chain
    grows only as genuinely sourced individual reports appear.
11. A link is NOT an assertion of sameness. If a source — especially a court
    — explicitly separates an individual from the pattern, the link may still
    exist for context, BUT `agent_reason` must record the distinction. NEVER
    publish that a named person is part of a pattern when a source says
    otherwise. (Highest-priority guardrail — this is a defamation risk.)
    Example: the sentencing judge in the Yishun cat case stated Lee Wai Leong
    should NOT be conflated with the broader series; his card links to the
    phenomenon for context, but the reason records that he is the one
    prosecuted case, explicitly distinct from the unsolved pattern.

## META-RULE
12. These rules exist to keep you grounded, not creative. If a rule ever
   seems to require you to guess, infer, or fill a gap, that is the signal
   to STOP and record only what is verified. Saying "outcome not confirmed"
   is always preferable to inventing a plausible-sounding fact.
