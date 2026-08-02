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


# Characters that may legitimately precede a comment opener. Used to tell a real
# `//` from the tail of a regex literal such as /^https?:\/\// , where the last
# two slashes are an escape and the closing delimiter, not a comment.
_COMMENT_LEAD = set(" \t\r\n;{}()[],=+-&|!?:*")


def code_only(src: str) -> str:
    """
    Source with comments removed, so "X must not appear" guards are not tripped
    by a comment that DISCUSSES X — the queue page carries one saying "do not
    widen this to .neq(...)", and matching raw text flags the warning itself.

    ## Why this is a scanner and not three regexes

    It used to be: strip /*...*/, then whole-line //, then trailing //. Stripping
    block comments FIRST is the bug, because a line comment may legally contain
    `/*`. proxy.ts has one — "rate limit for /api/* (spec: ...)" — and that `/*`
    was read as a block-comment opener, swallowing everything up to the next
    `*/` 4,408 characters later, which was most of the file including the
    Cloudflare Access check.

    A guard that greps eaten source does not fail loudly. `X in code` guards go
    red (that is how this was found), but every `X not in code` guard passes
    VACUOUSLY — a safety harness silently stops being one. So the order cannot be
    a heuristic: comments have to be recognised in the order they actually open.

    Known limit: a regex literal containing `//` after a non-lead character is
    handled via _COMMENT_LEAD, but this is not a full JS lexer and does not try
    to be. It only has to be right about comments.
    """
    out: list[str] = []
    i, n = 0, len(src)
    state: str | None = None      # None | 'line' | 'block' | a quote character
    prev = "\n"                   # last character considered outside a comment

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if state is None:
            if c == "/" and nxt == "/" and prev in _COMMENT_LEAD:
                state, i = "line", i + 2
                continue
            if c == "/" and nxt == "*" and prev in _COMMENT_LEAD:
                state, i = "block", i + 2
                continue
            if c in "'\"`":
                state = c
            out.append(c)
            prev = c
            i += 1
        elif state == "line":
            if c == "\n":
                state, prev = None, "\n"
                out.append(c)
            i += 1
        elif state == "block":
            if c == "*" and nxt == "/":
                state, prev, i = None, " ", i + 2
            else:
                i += 1
        else:                                   # inside a string / template
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == state:
                state, prev = None, c
            i += 1

    return "".join(out)


# Regression test for the helper itself. If this fails, every "must not appear"
# guard below is untrustworthy, so it runs before any of them.
_probe = code_only("// a path glob like /api/* in a line comment\n"
                   "const KEEP_ME = 1\n"
                   "/* a real block comment */\n"
                   "const ALSO_KEPT = 2\n")
assert "KEEP_ME" in _probe and "ALSO_KEPT" in _probe, (
    f"code_only ate real code: {_probe!r}")
assert "line comment" not in _probe and "real block" not in _probe, (
    f"code_only left comments behind: {_probe!r}")


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


# ── Review fixes, 2026-08-02 ────────────────────────────────────────────────

approve_ts    = read("app", "api", "queue", "[id]", "approve", "route.ts")
# proxy.ts, NOT middleware.ts — Next 16 renamed the convention and refuses to
# build if both exist. This file carries Cloudflare Access AND the /api rate
# limit; both are asserted below.
proxy_ts      = read("proxy.ts")

_artgen_code  = code_only(artgen_ts)
_rectify_code = code_only(rectify_ts)
_approve_code = code_only(approve_ts)
_mw_code      = code_only(proxy_ts)


def _num(pattern: str, src: str):
    m = re.search(pattern, src)
    return int(m.group(1).replace("_", "")) if m else None


# #1 — Vercel terminates a function at the plan default (10-15 s) regardless of
# any AbortController. Both art routes must declare maxDuration, and the
# in-handler timeout must stay strictly UNDER it: the in-handler one degrades to
# a usable status, a platform kill returns nothing. In approve it is worse than
# a lost image — the art call precedes the INSERT, so a platform kill loses the
# whole approval.
check("the rectify route declares maxDuration",
      "export const maxDuration" in _rectify_code)
check("the approve route declares maxDuration",
      "export const maxDuration" in _approve_code)

_md_rect = _num(r"maxDuration\s*=\s*(\d+)", _rectify_code)
_md_appr = _num(r"maxDuration\s*=\s*(\d+)", _approve_code)
_t_art   = _num(r"ART_TIMEOUT_MS\s*=\s*Number\([^)]*\?\?\s*([\d_]+)\)", _artgen_code)
_t_rect  = _num(r"RECTIFY_TIMEOUT_MS\s*=\s*Number\([^)]*\?\?\s*([\d_]+)\)", _artgen_code)

check("ART_TIMEOUT_MS fits inside the approve route's maxDuration",
      None not in (_md_appr, _t_art) and _t_art / 1000 < _md_appr,
      f"-> maxDuration={_md_appr}s vs ART_TIMEOUT_MS={_t_art}ms")
check("RECTIFY_TIMEOUT_MS fits inside the rectify route's maxDuration",
      None not in (_md_rect, _t_rect) and _t_rect / 1000 < _md_rect,
      f"-> maxDuration={_md_rect}s vs RECTIFY_TIMEOUT_MS={_t_rect}ms")

# #2 — 422 is FastAPI's generic validation code. A missing X-Ops-Token returns
# one, as does a non-object body. Mapping it to 'suppressed' wrote a terminal,
# no-override guardrail-#5 state onto published incidents from a transport
# fault. Suppression must be read from the response body instead.
check("a 422 is NOT mapped to 'suppressed'",
      "422" not in _artgen_code, f"-> {[l for l in _artgen_code.splitlines() if '422' in l]}")
check("status still crosses the boundary validated, not cast",
      "isImageStatus(" in _artgen_code)

# #5 — the failure path used to fire and forget, so a rejected write still
# answered ok:true and the operator read a refusal reason that was never stored.
check("the rectify failure path checks its write result",
      "failErr" in _rectify_code and "failRow" in _rectify_code)
check("a failed CAS on the failure path answers 409, not 200",
      _rectify_code.count("status: 409") >= 2)

# #8 — unbounded JSONB growth on a published row, loaded 200 at a time.
check("attempt history is capped", "MAX_ATTEMPTS_KEPT" in _rectify_code)

# #7 — the general 60/min limit already existed in proxy.ts (NOT middleware.ts:
# Next 16 renamed the convention, and this app has always used proxy). What was
# missing is that a REQUEST limit is not a COST limit: 60/min against a route
# that spends $0.0336 per call is ~$2/min per IP.
check("proxy.ts still rate-limits /api at all",
      "rateLimited(" in _mw_code and "429" in _mw_code)
check("the money-spending routes get their own tighter bucket",
      "RL_ART_PATHS" in _mw_code and "RL_ART_LIMIT" in _mw_code)
check("the tighter bucket covers BOTH rectify and approve",
      "rectify" in _mw_code and "approve" in _mw_code)
check("the art bucket is stricter than the general one",
      (_num(r"RL_ART_LIMIT\s*=\s*(\d+)", _mw_code) or 10**9)
      < (_num(r"RL_LIMIT\s*=\s*(\d+)", _mw_code) or 0) or False)
# Rate limiting must not have displaced the auth gate it shares a file with.
check("Cloudflare Access verification is still present and fails closed",
      "jwtVerify(" in _mw_code and "503" in _mw_code)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
