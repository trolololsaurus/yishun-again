"""
The public site must never emit an incident column nobody chose to publish.

Run: .venv/Scripts/python.exe test_public_column_allowlist.py

RLS restricts the anon key to `is_published = TRUE` ROWS, and `tools/rls_audit.py`
proves that against the live DB. Neither says anything about COLUMNS: a public
reader doing `select('*')` emits whatever the table happens to hold, so a new
internal field goes public the day it is added, with no code change to review.

That is the exposure path that matters for art prompts specifically. Prompt cues
live on `war_room_queue` today (anon-invisible), but art generation is dormant
rather than deleted, and persisting the prompt onto `incidents` is the obvious
next step whenever it is switched back on. With `select('*')` that would publish
itself silently; with an allowlist it stays private until someone types the
column name into `apps/web/lib/publicColumns.ts` on purpose.

Static check — reads the TS sources as text. Offline, no DB, no network.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "apps" / "web"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


# Every public reader of `incidents` in the web app. Adding a new one without
# adding it here is itself the thing to catch, so the file walk is the input
# rather than a hardcoded list.
sources = sorted(WEB.rglob("*.ts")) + sorted(WEB.rglob("*.tsx"))
sources = [p for p in sources if "node_modules" not in p.parts and ".next" not in p.parts]
check("web sources found", len(sources) > 0, f"nothing under {WEB}")

# ── 1. No public reader may select('*') from incidents ───────────────────────
# `.from('incidents')` followed by `.select('*')` within a few lines.
SELECT_STAR = re.compile(
    r"\.from\(\s*['\"]incidents['\"]\s*\)[^;]{0,200}?\.select\(\s*['\"]\*['\"]",
    re.S,
)
offenders = []
for path in sources:
    text = path.read_text(encoding="utf-8", errors="replace")
    if SELECT_STAR.search(text):
        offenders.append(path.relative_to(WEB).as_posix())

check("no select('*') on incidents in apps/web", not offenders,
      f"\n      {offenders}")

# ── 2. The allowlist exists and excludes the internal columns ────────────────
allowlist_path = WEB / "lib" / "publicColumns.ts"
check("publicColumns.ts exists", allowlist_path.exists(), str(allowlist_path))

allow_src = allowlist_path.read_text(encoding="utf-8") if allowlist_path.exists() else ""
# Pull the concatenated string literals out of the export.
body = re.search(r"PUBLIC_INCIDENT_COLUMNS\s*=\s*(.*?)(?:\n\n|\Z)", allow_src, re.S)
columns = set()
if body:
    for chunk in re.findall(r"'([^']*)'", body.group(1)):
        columns.update(c for c in chunk.split(",") if c)

check("allowlist parsed", len(columns) > 5, f"got {sorted(columns)}")

# Columns that must never be public. `pixel_art_prompt` does not exist on
# `incidents` yet — that is exactly why it is listed: this assertion is the
# tripwire for the day someone adds it.
NEVER_PUBLIC = [
    "pixel_art_prompt",
    "proposed_pixel_prompt",
    "art_prompt",
    "agent_confidence",
    "chaos_contribution",
    "raw_content",
]
for col in NEVER_PUBLIC:
    check(f"'{col}' not in the public allowlist", col not in columns)

# A wildcard smuggled into the allowlist would defeat the whole check.
check("allowlist contains no wildcard", "*" not in columns)

# ── 3. Both single-incident readers use the allowlist ────────────────────────
for rel in ["app/api/incidents/[slug]/route.ts", "app/incidents/[slug]/page.tsx"]:
    p = WEB / rel
    src = p.read_text(encoding="utf-8") if p.exists() else ""
    check(f"{rel} uses PUBLIC_INCIDENT_COLUMNS", "PUBLIC_INCIDENT_COLUMNS" in src)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
