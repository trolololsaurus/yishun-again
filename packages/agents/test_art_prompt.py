"""
The War Room's art-prompt viewer must show the prompt Modal would really send.

Run: .venv/Scripts/python.exe test_art_prompt.py

`apps/war-room/lib/artPrompt.ts` is a hand-kept mirror of `_build_prompt` /
`NEGATIVE_PROMPT` in `art/generate_pixel_art.py` — the War Room is TypeScript on
Vercel and cannot call into the Modal app, so there is no way to share one
definition. A mirror that drifts is worse than no viewer at all: it shows the
operator a prompt that nothing generates.

This reads the TS source as text and re-implements the mirror's arithmetic in
Python, so any edit to either side that changes the output shows up here.
Offline — no network, no imports from the TS toolchain.
"""
import importlib
import re
from pathlib import Path

gpa = importlib.import_module("art.generate_pixel_art")

TS_PATH = Path(__file__).resolve().parents[2] / "apps" / "war-room" / "lib" / "artPrompt.ts"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


ts = TS_PATH.read_text(encoding="utf-8") if TS_PATH.exists() else ""
check("TS mirror exists", bool(ts), f"missing {TS_PATH}")


def ts_moods(src: str) -> dict[str, str]:
    """Pull CLASSIFICATION_MOOD out of the TS object literal."""
    block = re.search(
        r"CLASSIFICATION_MOOD:\s*Record<string,\s*string>\s*=\s*\{(.*?)\n\}",
        src, re.S,
    )
    if not block:
        return {}
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))


def ts_negative(src: str) -> str:
    """Pull NEGATIVE_PROMPT out, joining its concatenated string literals."""
    block = re.search(r"NEGATIVE_PROMPT\s*=\s*(.*?)\n\n", src, re.S)
    if not block:
        return ""
    return "".join(re.findall(r"'([^']*)'", block.group(1)))


def ts_build(src: str, classification: str, area_name) -> str:
    """Re-implement the mirror's template from the TS template literal."""
    tpl = re.search(r"`HD-2D pixel art,(.*?)`\s*\)", src, re.S)
    if not tpl:
        return ""
    body = "HD-2D pixel art," + tpl.group(1)
    body = re.sub(r"`\s*\+\s*\n\s*`", "", body)          # join the concat seams
    moods = ts_moods(src)
    return (
        body
        .replace("${location}", area_name or "Yishun")
        .replace("${mood}", moods.get(classification, moods.get("custom", "")))
    )


# ── 1. The mood table must agree, key for key ────────────────────────────────
py_moods = gpa.CLASSIFICATION_MOOD
mirror_moods = ts_moods(ts)
check("mood keys match", set(py_moods) == set(mirror_moods),
      f"py={sorted(py_moods)} ts={sorted(mirror_moods)}")
for key in sorted(py_moods):
    check(f"mood[{key}] matches", py_moods[key] == mirror_moods.get(key),
          f"\n      py: {py_moods[key]}\n      ts: {mirror_moods.get(key)}")

# ── 2. Negative prompt must agree ────────────────────────────────────────────
check("negative prompt matches", gpa.NEGATIVE_PROMPT == ts_negative(ts),
      f"\n      py: {gpa.NEGATIVE_PROMPT}\n      ts: {ts_negative(ts)}")

# ── 3. Full prompt must agree across every classification + the area fallback ─
CASES = [
    ("dagger", "Yishun Avenue 11"),
    ("clown",  "Yishun Ring Road"),
    ("heart",  "Chong Pang"),
    ("custom", "Khatib"),
    ("dagger", None),   # area_name NULL  → "Yishun"
    ("clown",  ""),     # area_name empty → "Yishun" (Python `or`, TS `||`)
    ("bogus",  "Yishun Central"),  # unknown classification falls back to custom
]
for classification, area in CASES:
    expected = gpa._build_prompt("irrelevant title", classification, area)
    actual   = ts_build(ts, classification, area)
    label    = f"prompt({classification}, {area!r})"
    check(label, expected == actual, f"\n      py: {expected}\n      ts: {actual}")

# ── 4. The title really is ignored — the viewer says so on screen ────────────
a = gpa._build_prompt("Man argues with void deck chair", "clown", "Yishun Ave 11")
b = gpa._build_prompt("Totally different headline",      "clown", "Yishun Ave 11")
check("title does not affect the prompt", a == b)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
