"""
Guardrail #5 respectful mode (art/sensitive_scene.py). Pure, offline.

Run: .venv/Scripts/python.exe test_sensitive_art.py

Load-bearing properties:
  - the deterministic scene NEVER names the body, the act or a method
    (scene_is_clean over the real scene, for every environment)
  - the environment is inferred as a place-TYPE only, never from the act; water
    settings fall through to the neutral default rather than depict water
  - the mode switch defaults to 'respectful' and fails toward 'suppress'
"""
import importlib
from unittest import mock

ss = importlib.import_module("art.sensitive_scene")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


print("mode switch:\n")

with mock.patch.dict("os.environ", {}, clear=True):
    check("default is respectful", ss.sensitive_art_mode() == "respectful")
for value, expect in (("respectful", "respectful"), ("RESPECTFUL", "respectful"),
                      (" respectful ", "respectful"), ("suppress", "suppress"),
                      ("off", "suppress"), ("", "suppress"), ("garbage", "suppress")):
    with mock.patch.dict("os.environ", {"SENSITIVE_INCIDENT_ART": value}, clear=False):
        check(f"mode({value!r}) -> {expect}", ss.sensitive_art_mode() == expect,
              f"-> {ss.sensitive_art_mode()}")

print("\nenvironment inference — place-type only:\n")

CASES = [
    ({"summary": "found at a multi-storey carpark"}, "carpark deck", True),
    ({"summary": "at the multistorey car park deck"}, "carpark deck", True),
    ({"title": "Man found at void deck of Blk 737"}, "void deck", True),
    ({"summary": "along the 12th-floor corridor"}, "common corridor", False),
    ({"summary": "at a neighbourhood playground"}, "neighbourhood green", True),
    ({"summary": "nothing specific here"}, "foot of a mid-rise HDB block", True),
]
for incident, fragment, car in CASES:
    clause, car_fits = ss.infer_environment(incident)
    check(f"{fragment!r} inferred", fragment in clause, f"-> {clause}")
    check(f"{fragment!r} car-fits={car}", car_fits is car, f"-> {car_fits}")

# The corridor (upper floor) is the one place a patrol car does NOT belong.
scene = ss.sensitive_scene({"summary": "along the 12th-floor corridor"})
check("no patrol car for an upper-floor corridor", "patrol car" not in scene)
scene_car = ss.sensitive_scene({"summary": "at the void deck"})
check("a patrol car for a ground-level void deck", "patrol car" in scene_car)

# Water settings must NOT render water — they fall through to the default.
water = ss.infer_environment({"summary": "recovered from the canal near the pond"})[0]
check("a water setting falls through to the HDB default (no water depicted)",
      "canal" not in water and "pond" not in water and "HDB block" in water)

print("\nthe scene is clean for EVERY environment:\n")

for incident, fragment, _car in CASES:
    scene = ss.sensitive_scene(incident)
    check(f"scene for {fragment!r} is clean", ss.scene_is_clean(scene),
          f"-> {scene}")
    check(f"scene for {fragment!r} clears the injection length bound",
          len(scene) <= 1500 and len(scene) >= 400, f"-> {len(scene)} chars")
    check(f"scene for {fragment!r} is the blue privacy tent, focal",
          "blue" in scene and "police privacy tent" in scene and "focal point" in scene)
    check(f"scene for {fragment!r} has the police tape cordon",
          "police tape" in scene)
    check(f"scene for {fragment!r} shows the three groups",
          "Chinese" in scene and "Malay" in scene and "Indian" in scene)

print("\nblock number — the incident's real one, never invented:\n")

check("block_number column is used",
      ss.block_label({"block_number": 257}) == "257")
check("a 'Blk 800' prefix is stripped",
      ss.block_label({"block_number": "Blk 800"}) == "800")
check("a trailing letter is kept (110A)",
      ss.block_label({"block_number": "Block 110A"}) == "110A")
check("mined from the title when the column is empty",
      ss.block_label({"title": "Man found at foot of Block 737 Yishun"}) == "737")
check("no block stated -> empty (never invented)",
      ss.block_label({"title": "Woman rescued on Yishun Ave 2", "summary": "no block here"}) == "")
check("the real block number is painted in the scene",
      "block number, 257" in ss.sensitive_scene({"block_number": 257}).lower()
      or "block number, 257" in ss.sensitive_scene({"block_number": 257}))
check("no block -> the scene names no specific number",
      "block number," not in ss.sensitive_scene({"title": "Yishun Ave 2 rescue"}))

print("\npolice depiction — POLICE label, officers not in tudung:\n")

_s = ss.sensitive_scene({"block_number": 257})
check("the tent/officers are labelled POLICE, not SPF",
      "POLICE" in _s and "SPF" not in _s)
check("no officer is put in a tudung", "tudung" not in _s.lower())
check("officers wear the standard police cap", "police cap" in _s.lower())

print("\nscene_is_clean — the defensive gate itself:\n")

check("a scene naming a body is NOT clean",
      ss.scene_is_clean("Officers stand beside the body under the tent.") is False)
check("a scene naming the method is NOT clean",
      ss.scene_is_clean("A knife lies on the corridor floor.") is False)
check("a scene naming a fall is NOT clean",
      ss.scene_is_clean("Residents point to where he fell.") is False)
check("the word 'suicide' is NOT clean",
      ss.scene_is_clean("A quiet suicide scene, cordoned off.") is False)
check("a non-string is NOT clean (fails closed)", ss.scene_is_clean(None) is False)
check("an empty string is NOT clean (fails closed)", ss.scene_is_clean("   ") is False)
check("a neutral cordon description IS clean",
      ss.scene_is_clean("A blue police privacy tent stands cordoned by tape.") is True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
