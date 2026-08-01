"""
Guardrail #5 image suppression (art/suppression.py). Pure, offline.

Run: .venv/Scripts/python.exe test_image_suppression.py

The load-bearing property is the OR: the deterministic phrase check must catch a
suicide story the classifier failed to tag. That is the Blk 737 case and it is
the whole reason this is not the tag-only gate in ART_PIPELINE.md §4.

The second property is totality — the gate must return a bool for any input,
including garbage, because a raising gate is a gate that did not run.
"""
import importlib

sup = importlib.import_module("art.suppression")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


print("tag-based suppression:\n")

check("suicide tag suppresses",
      sup.suppress_image({"tags": ["suicide"], "title": "t", "summary": "s"}))
check("self-harm tag suppresses",
      sup.suppress_image({"tags": ["self-harm"], "title": "t", "summary": "s"}))
check("tag matching is case-insensitive",
      sup.suppress_image({"tags": ["Suicide"], "title": "t", "summary": "s"}))
check("'self_harm' and 'self harm' tag spellings fold to the canonical tag",
      sup.suppress_image({"tags": ["self_harm"]}) and sup.suppress_image({"tags": ["self harm"]}))
check("a suppress tag among unrelated tags still suppresses",
      sup.suppress_image({"tags": ["police", "yishun", "suicide"]}))

print("\nnon-suppression — deliberately narrow (severity/deaths NOT consulted):\n")

check("severity 5 with a death and no suppress tag does NOT suppress",
      not sup.suppress_image({
          "tags": ["fire", "fatal"], "severity": 5, "deaths": 1,
          "title": "Fatal fire at Yishun carpark",
          "summary": "One man was pronounced dead after a car fire at Blk 512.",
      }))
check("empty tag list does not suppress",
      not sup.suppress_image({"tags": [], "title": "Cat stuck in tree", "summary": "SCDF responded."}))
check("tags absent entirely does not suppress",
      not sup.suppress_image({"title": "Cat stuck in tree", "summary": "SCDF responded."}))
check("violence without a suppress phrase does not suppress",
      not sup.suppress_image({
          "tags": ["assault"],
          "title": "Man slashed at Yishun coffeeshop",
          "summary": "A 34-year-old was taken to hospital with knife wounds.",
      }))

print("\nphrase-based suppression — the amendment (EDGE_CASES §1.2):\n")

# The whole point of the amendment: classifier emitted no suppress tag.
check("NO suicide tag but 'suicide' in the summary IS suppressed (Blk 737 case)",
      sup.suppress_image({
          "tags": ["police"],
          "title": "Man found at foot of Yishun block",
          "summary": "Police said the death was an apparent suicide and no foul play is suspected.",
      }))
check("'suicide' in the title alone suppresses",
      sup.suppress_image({"tags": [], "title": "Suicide prevention drive at Yishun", "summary": ""}))
check("'took his own life' suppresses",
      sup.suppress_image({"tags": [], "title": "t", "summary": "The coroner found he took his own life."}))
check("'took her own life' suppresses",
      sup.suppress_image({"tags": [], "summary": "She took her own life, the court heard."}))
check("'took their own life' suppresses",
      sup.suppress_image({"tags": [], "summary": "The teenager took their own life."}))
check("'self harm' unhyphenated in prose suppresses",
      sup.suppress_image({"tags": [], "summary": "He was treated for self harm injuries."}))
check("phrase matching is case-insensitive",
      sup.suppress_image({"tags": [], "title": "SUICIDE AT YISHUN", "summary": ""}))

print("\ntotality — never raises, fails closed:\n")

check("tags=None does not raise and does not suppress",
      sup.suppress_image({"tags": None, "title": "Cat rescued", "summary": "All well."}) is False)
check("tags as a bare string is handled",
      sup.suppress_image({"tags": "suicide"}) is True)
check("a bare non-suppress string tag does not suppress",
      sup.suppress_image({"tags": "fire", "title": "Fire", "summary": "Blaze."}) is False)
check("non-string tag members are skipped, not fatal",
      sup.suppress_image({"tags": [None, 42, {"a": 1}, "fire"], "title": "Fire", "summary": ""}) is False)
check("a suppress tag survives non-string neighbours",
      sup.suppress_image({"tags": [None, 42, "suicide"]}) is True)
check("title/summary None does not raise",
      sup.suppress_image({"tags": [], "title": None, "summary": None}) is False)
check("non-string title/summary does not raise",
      sup.suppress_image({"tags": [], "title": 42, "summary": ["x"]}) is False)
check("empty dict does not raise and does not suppress",
      sup.suppress_image({}) is False)

# Fail closed: an input the gate cannot read is treated as suppressed, because
# the restrictive answer is the safe one for a suppression gate.
check("None incident fails CLOSED (suppresses)", sup.suppress_image(None) is True)
check("a string incident fails CLOSED (suppresses)", sup.suppress_image("nope") is True)
check("a list incident fails CLOSED (suppresses)", sup.suppress_image([1, 2]) is True)


class _Exploding(dict):
    def get(self, *_a, **_k):
        raise RuntimeError("boom")


check("an incident whose .get() raises fails CLOSED (suppresses)",
      sup.suppress_image(_Exploding()) is True)

check("every return is a real bool",
      all(isinstance(sup.suppress_image(x), bool)
          for x in ({}, None, "x", {"tags": ["suicide"]}, _Exploding())))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
