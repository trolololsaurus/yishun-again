"""
Static guards over the War Room rectification UI (Track B, B4b). Offline.

Run: .venv/Scripts/python.exe test_rectify_guards.py

The rectification queue is the one place in the product where an operator can
re-run image generation by hand, so it is also the one place where guardrail #5
could plausibly be subverted — by widening a query, by adding a helpful
"generate anyway" button, or by letting a suppressed row be flipped to some
other terminal state. None of that is reachable today and these assertions are
what keep it that way.

Modelled on test_public_column_allowlist.py: this reads the TypeScript as TEXT
rather than executing it, because there is no TS toolchain in this venv and the
properties worth protecting are structural. A green run here is not a
substitute for `npx tsc --noEmit`.
"""
import re
from pathlib import Path

WAR_ROOM = Path(__file__).resolve().parents[2] / "apps" / "war-room"
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


def read(*parts) -> str:
    p = WAR_ROOM.joinpath(*parts)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def code_only(src: str) -> str:
    """
    Source with comments and JSX comment blocks removed.

    Needed because several of these guards are phrased as "X must not appear",
    and the files deliberately DISCUSS the thing they must not do — the queue
    page carries a comment saying "do not widen this to .neq(...)". Matching raw
    text flags the warning as the violation.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)      # block + JSX comments
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)      # whole-line //
    return re.sub(r"(?<![:'\"])//[^\n'\"`]*$", "", src, flags=re.M)  # trailing //


types_ts    = read("lib", "types.ts")
page_tsx    = read("app", "rectify", "page.tsx")
card_tsx    = read("components", "RectifyCard.tsx")
rectify_ts  = read("app", "api", "incidents", "[id]", "rectify", "route.ts")
noimage_ts  = read("app", "api", "incidents", "[id]", "no-image", "route.ts")
reval_ts    = read("lib", "revalidate.ts")
artgen_ts   = read("lib", "artGenerate.ts")
nav_tsx     = read("components", "Nav.tsx")

print("files exist:\n")
for name, src in (("lib/types.ts", types_ts), ("app/rectify/page.tsx", page_tsx),
                  ("components/RectifyCard.tsx", card_tsx),
                  ("api/incidents/[id]/rectify/route.ts", rectify_ts),
                  ("api/incidents/[id]/no-image/route.ts", noimage_ts),
                  ("lib/revalidate.ts", reval_ts)):
    check(name, bool(src.strip()))

print("\nstatus vocabulary — one source of truth:\n")

m = re.search(r"RECTIFIABLE_STATUSES\s*=\s*\[(.*?)\]", types_ts, re.S)
statuses = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()
check("RECTIFIABLE_STATUSES is exactly refused/transient/invalid/skipped",
      statuses == {"refused", "transient", "invalid", "skipped"}, f"-> {sorted(statuses)}")
check("'suppressed' is NOT rectifiable", "suppressed" not in statuses)
check("'pending' is NOT rectifiable (never attempted — a backfill problem)",
      "pending" not in statuses)
check("'no_image_final' is NOT rectifiable (terminal)", "no_image_final" not in statuses)
for v in ("ok", "suppressed", "refused", "transient", "invalid",
          "skipped", "pending", "no_image_final"):
    check(f"ImageStatus knows '{v}'", f"| '{v}'" in types_ts or f"'{v}'" in types_ts)
check("Incident carries the three image columns",
      all(c in types_ts for c in ("image_status:", "image_prompt:", "image_attempts:")))
check("artGenerate re-exports the union instead of redeclaring it",
      "export type { ImageAttempt, ImageStatus }" in artgen_ts)
check("backend statuses are VALIDATED, not cast",
      "as ImageStatus" not in artgen_ts and "isImageStatus(" in artgen_ts)

print("\nguardrail #5 — not reachable from this UI:\n")

check("the queue is an allowlist (.in), not an exclusion",
      ".in('image_status', RECTIFIABLE_STATUSES)" in page_tsx)
check("the queue never uses .neq to build its filter", ".neq(" not in code_only(page_tsx))
check("the page never names 'suppressed' as something it queries",
      "'suppressed'" not in code_only(page_tsx))
check("the page lists only published incidents", ".eq('is_published', true)" in page_tsx)

# The card DECLARES image_status on its row type — it must never SEND one. Every
# write it makes leaves through a fetch body, so that is what to inspect.
_card_code = code_only(card_tsx)
_bodies = re.findall(r"JSON\.stringify\((.*?)\)", _card_code, re.S)
check("the card has NO override control — no request body carries image_status",
      not any("image_status" in b for b in _bodies), f"-> {_bodies}")
check("the card never assigns a status locally either",
      not re.search(r"image_status\s*[:=]\s*['\"]", _card_code))
check("the card never mentions 'suppressed' as an action",
      "'suppressed'" not in _card_code)
check("the rectify route refuses a suppressed incident with 422",
      "'suppressed'" in rectify_ts and "422" in rectify_ts)
check("the rectify route also rejects any non-rectifiable state",
      "RECTIFIABLE_STATUSES.includes" in rectify_ts)
check("the no-image route is CAS-guarded by the same list",
      ".in('image_status', RECTIFIABLE_STATUSES)" in noimage_ts)
check("a suppressed row therefore cannot become no_image_final",
      "'suppressed'" not in noimage_ts and "no_image_final" in noimage_ts)

print("\nrectification semantics:\n")

check("the success update is compare-and-set, not blind",
      ".in('image_status', RECTIFIABLE_STATUSES)" in rectify_ts)
check("a lost CAS answers 409 rather than silently succeeding", "409" in rectify_ts)
check("attempts are APPENDED and renumbered, never replaced",
      "...prior" in rectify_ts and "n: i + 1" in rectify_ts)
_ok_write = re.search(r"image_status:\s*'ok'", rectify_ts)
check("a failed render is still persisted so the operator sees the reason",
      bool(_ok_write) and rectify_ts.index("art.status !== 'ok'") < _ok_write.start())
check("the new pixel_art_url is written on success",
      bool(re.search(r"pixel_art_url:\s*art\.url", rectify_ts)),
      "-> the R2 key is stable and cached a year; only the ?v= suffix changes")

print("\nrevalidation — mandatory and reported:\n")

check("successful rectification calls the ISR hook",
      "revalidateIncident(" in rectify_ts)
check("the hook is called AFTER the row is updated",
      rectify_ts.index("revalidateIncident(") > rectify_ts.index(".update({"))
check("the hook is awaited (an unawaited fetch is frozen when the function returns)",
      "await revalidateIncident(" in rectify_ts)
check("the outcome is reported to the operator, not swallowed",
      "revalidated:" in rectify_ts)
check("the helper sends the exact Bearer format timingSafeEqual expects",
      "`Bearer ${secret}`" in reval_ts)
check("the helper validates the slug (the endpoint STRIPS rather than rejects)",
      "[a-z0-9-]" in reval_ts)
check("the helper refuses to follow redirects (apex/www must fail loudly)",
      "redirect: 'error'" in reval_ts)
check("missing config is an explicit reason, never a silent success",
      "not set on the War Room" in reval_ts)
check("the no-image route does NOT revalidate (pixel_art_url is unchanged)",
      "revalidateIncident" not in noimage_ts)

print("\nreachability and exposure:\n")

check("the view is reachable from the nav", "'/rectify'" in nav_tsx)
public_cols = (WEB / "lib" / "publicColumns.ts")
pc = public_cols.read_text(encoding="utf-8") if public_cols.exists() else ""
check("internal image columns are NOT exposed publicly",
      not any(c in pc for c in ("image_prompt", "image_status", "image_attempts")),
      "-> prompts would be served to the open internet")

# The card routes every request through one helper with a typed path, so the
# reachable endpoints are the union members, not literals in the fetch call.
paths = set(re.findall(r"post\(\s*'([a-z-]+)'", _card_code))
check("exactly two endpoints are called (retry-as-is and leave-pending add none)",
      paths == {"rectify", "no-image"}, f"-> {sorted(paths)}")
check("every request goes to /api/incidents/<id>/<path> and nowhere else",
      _card_code.count("fetch(") == 1
      and "/api/incidents/${item.id}/${path}" in _card_code)
check("'leave pending' issues no request at all",
      "onDismiss(item.id)" in _card_code)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
