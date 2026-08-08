# Web Restructure — Feed/Map split, shared Chaos sidebar, mobile bottom sheet

**Status, as at 2026-08-07:** Phase 1 LANDED on branch `web-restructure` (not
merged, not deployed); Phases 2–6 not started. This is the agreed plan of record
for the `apps/web` restructure. As phases land, mark them and move the detail
into `FRONTEND_SPEC.md` (the canonical frontend doc), leaving this file as the
historical plan.
**Owns:** the decisions and phase order. **Companion to:** `FRONTEND_SPEC.md`
(canonical, wins on any disagreement once code lands), `CLAUDE.md` §"Frontend
Theme".
**Recon basis:** the codebase read on 2026-08-06/07 — route structure, the four
prop-coupled panels, `IncidentFeed`'s react-window list, `IncidentMap`'s circle
layer, and the image backfill (`tools/backfill_images.py`, commit `5e999ab`)
that left **161 of 167 published incidents with a `pixel_art_url`** (5
`suppressed`, 1 `refused`). Images are therefore the DEFAULT state of a card,
not the exception — the feed is designed around having them.

---

## Scope

- Split Feed and Map into two routes: **Feed → `/`**, **Map → `/map`**.
- A **shared Chaos Index sidebar** on both routes, with year-filter state
  persisting across navigation via a **`?year=` URL param**.
- **Mobile:** the sidebar becomes a bottom sheet — a slim persistent bar showing
  the score, swiping up for the breakdown + year filter.
- **Feed** gets incident images + infinite scroll (currently 20 loaded, then a
  react-window list).
- **Map** pins become classification emoji (❤️ 🤡 💀) with a hover/tap preview
  showing an image thumb + short teaser.

### HARD CONSTRAINT

The classification colours are **locked — never change the values**:

| Class  | Emoji | Colour    |
|--------|-------|-----------|
| heart  | ❤️    | `#4ECDC4` teal |
| clown  | 🤡    | `#FFE66D` yellow |
| dagger | 💀    | `#FF6B6B` coral |

These live in `lib/utils.ts` (`PIN_COLOR`, `CLASS_COLOR`), `tailwind.config.js`
(several aliases each), `globals.css` (CSS vars), and one inline literal at
`IncidentCard.tsx:131`. Treat `PIN_COLOR` as the source; reference it, never add a
fourth copy, never edit the values.

---

## Target architecture

```
app/
├── (hud)/                    ← route group: shared sidebar + shell, no URL segment
│   ├── layout.tsx            ← <HudShell>: main area + ChaosSidebar (desktop) / BottomSheet (mobile)
│   ├── page.tsx              ← FEED  (was the map homepage)  →  "/"
│   └── map/page.tsx          ← MAP                            →  "/map"
├── about/page.tsx            ← unchanged
├── timeline/…                ← left in place this pass (Decision D)
└── incidents/[slug]/…        ← unchanged
```

**State model:** `?year=` and `?class=` are the single source of truth, read by
every consumer via `useSearchParams`, written via
`router.replace(..., { scroll: false })`. Nav links carry the current query string
forward, so both filters survive `/ ↔ /map` navigation. That is the entire
persistence mechanism — no context, no store. This replaces today's
`HomeClient` local `useState` for `selectedYear`/`activeFilter`, which dies on
navigation.

---

## Decisions taken

Each is reversible; rationale in-line. Overturn any by editing this section
before the relevant phase.

**A. Year drives the client, not the server.** The page server components keep
SSR'ing the **current year** (preserving `revalidate = 60` ISR); the client reads
`?year=` and re-fetches when it differs — today's `IncidentFeed` pattern. Reading
`searchParams` in the page would force dynamic rendering and kill ISR. Cost: a
shared `/?year=2024` link paints current-year first, then corrects. Acceptable —
the param is for cross-nav persistence, not deep-link SEO. *(If first-paint
correctness is wanted over ISR: read `searchParams` server-side per page instead —
one line each, flagged at each site.)*

**B. Map pins → HTML `maplibregl.Marker`, not a symbol layer.** Emoji in a symbol
layer render from the style's PBF glyph set (Noto Sans via OpenFreeMap), which
lacks emoji — they come out as tofu / monochrome. HTML markers render emoji
natively, guaranteed, with full control over the hover/tap preview. Price:
`map.setFilter()` and `source.setData()` no longer drive pins — we manage the
marker array by hand (Phase 4). At ~50–150 markers/year the perf cost is nil.

**C. Feed images via `next/image` + `loading="lazy"`.** ~19 × 1200×630 through a
raw `<img>` is heavy and CLS-prone. `next/image` gives lazy loading, `srcset`,
reserved dimensions. Needs `images.remotePatterns` for `assets.yishunagain.com`
in `next.config.js`; CSP `img-src 'self'` already covers the `/_next/image`
optimizer origin. *(Zero-config fallback: raw `<img loading="lazy" width height>`;
the trade the other way is Vercel image-optimization billing.)*

**D. Leave `/timeline` in place this pass.** It is reusable as a *pattern* (its
IntersectionObserver infinite scroll), not by deletion — it also carries severity
+ free-form year controls the sidebar lacks. Extract the scroll logic into a
shared hook, build Feed on it, leave `/timeline` working. Redirect/kill is a
follow-up once Feed reaches parity — not load-bearing here.

**E. Drop `react-window`.** The feed abandons `FixedSizeList` (fixed 152px rows
can't hold variable-height image cards) for the proven IntersectionObserver
sentinel. `react-window` becomes unused → removed from `package.json`.

---

## Phase 1 — Foundation: route group, shell, params, Nav/SEO

- **New `lib/params.ts`** (pure, unit-tested): `parseYear(sp)`, `parseClass(sp)`
  (reusing `sanitiseYear`/`sanitiseClassification`), and `withParams(pathname, sp,
  patch)` for query-preserving hrefs.
- **New `app/(hud)/layout.tsx`** → a client `<HudShell>` laying out `{children}`
  beside `<ChaosSidebar>` (desktop) and mounting `<BottomSheet>` (mobile). Owns
  the flex/height budget `HomeClient` owns today.
- **Split `app/page.tsx`:** Feed SSR query + metadata → `app/(hud)/page.tsx`; Map
  SSR query + metadata → `app/(hud)/map/page.tsx`. `HomeClient.tsx` is dissolved
  (its year/stats effect → `ChaosSidebar`, its layout → `HudShell`).
- **`components/Nav.tsx`:** `LINKS` → `FEED (/)`, `MAP (/map)`, `ABOUT`, routed
  through `withParams`. The `path === '/'` → `<h1>` logic updates: `/` gets a Feed
  `<h1>`, `/map` its own.
- **SEO move** (today's "incident map" copy is map-as-homepage):
  - `/map` inherits `HOME_TITLE`/`HOME_DESCRIPTION`, canonical `${SITE_URL}/map`.
  - `/` gets new archive/feed copy, canonical `${SITE_URL}/`.
  - `homeJsonLd`: rework the `WebSite` description; `Organization` stays on `/`.
  - `sitemap.ts`: add `/map` (hourly, 1.0); `/` and `/timeline` stay.
  - `robots.ts`: no change.

**Checkpoint:** both routes render with the shared sidebar; nav preserves params;
ISR intact.

> **DONE (2026-08-07).** Landed as: `lib/params.ts` (+ `params.test.ts`, 6/6
> green); `app/(hud)/{layout,page,map/page}.tsx`; `components/{ChaosSidebar,
> FeedBody,MapBody,Nav,NavLinks}.tsx`; `app/page.tsx` + `components/HomeClient.tsx`
> deleted; `sitemap.ts` gains `/map`. Build clean — `/` and `/map` both
> prerender `○ Static` at 1m revalidate, so **Decision A held** (ISR intact,
> year read client-side). Verified in-browser: deep-link `/?year=2024` updates
> sidebar + chips + feed; nav links carry `?year=2024` across all four links;
> `/map` fetches `/api/{map,chaos,incidents}?year=2024`; a real chip click
> re-filters the feed to one class. Map WebGL shows "unavailable" only under the
> non-compositing preview pane (documented `IncidentMap` headless artifact, no
> console error) — unaffected code.
>
> **Deviations from the plan above, all deliberate:**
> - **No `HudShell` yet.** The shared shell is static divs in the `(hud)` server
>   layout; only `ChaosSidebar` is a client island (Suspense-wrapped for
>   `useSearchParams`). `HudShell` was an empty wrapper until mobile needs it —
>   introduced in Phase 5 instead (YAGNI).
> - **`Nav` split into `Nav` + `NavLinks` (+ `NavLinksFallback`).** Not in the
>   plan. `useSearchParams` forces a CSR bailout that would drop the homepage
>   `<h1>` from the prerendered HTML, so param-aware links live in a
>   Suspense-wrapped `NavLinks` child while `Nav` keeps the `<h1>` on
>   `usePathname` alone (static-safe). Fallback renders plain links pre-hydration.
> - **Feed/Map bodies each fetch chip counts** (SSR-seeded, refetch on year
>   change). This is the double `/api/chaos` fetch the plan flagged for Phase 6
>   dedupe; confirmed present and harmless (same cached endpoint, deterministic
>   result).

## Phase 2 — Chaos sidebar becomes self-contained

- **New `components/ChaosSidebar.tsx`** wrapping the untouched `ChaosPanel.tsx`:
  reads `?year=`; `onYearChange` → `router.replace(withParams(...))`; owns the
  `/api/chaos?year=N` fetch + `loading`/`error`/stats (lifted from `HomeClient`);
  `availableYears` seeded via an SSR prop through the layout. `ChaosPanel` renders
  unchanged (already purely presentational).
- Removes the prop-drilling spine — Map and Feed read the param themselves.

**Checkpoint:** year change updates the URL; both surfaces react on their own; a
reload of `/?year=2024` shows 2024.

> **DONE (2026-08-07) — delivered inside Phase 1, no separate commit.** The route
> split cannot function without shared year state, and the chosen mechanism is
> the URL, so `ChaosSidebar` had to be built in Phase 1: it reads `?year=`,
> writes it via `router.replace` (dropping the param at the current year), owns
> the `/api/chaos` fetch + loading/error/stats, takes `availableYears` as an SSR
> prop from `(hud)/layout.tsx`, and leaves `ChaosPanel` untouched. Feed and Map
> read the param themselves — the prop-drilling spine is gone. Checkpoint met
> per the Phase 1 verification (deep-link updates every surface; nav carries the
> param). Nothing remained to build here.
>
> One edge case noted and deliberately NOT handled (out of scope, degrades
> gracefully): a hand-typed out-of-range `?year=1850` — valid 4-digit but absent
> from `availableYears` — leaves the `<select>` showing its first option while
> the fetches run for 1850 (empty results). Revisit only if it ever matters;
> fixing it cleanly means threading `availableYears` into the Feed/Map bodies.

## Phase 3 — Feed route: images + infinite scroll

- **New `hooks/useIncidentPages.ts`:** extract the identical
  `page`/`hasMore`/`loadMore` + request-ticket (`reqRef`/`inFlight`) logic shared
  by `IncidentFeed` and `TimelineClient` into one hook; both adopt it
  (de-duplicates rather than triplicates).
- **`app/(hud)/page.tsx` (Feed SSR):** add `pixel_art_url, image_status` to the
  select. `pixel_art_url` is already in `PUBLIC_INCIDENT_COLUMNS`; **add
  `image_status` there** (non-sensitive status vocabulary).
- **`app/api/incidents/route.ts`:** add `pixel_art_url, image_status` to the
  select. No other change.
- **`components/IncidentFeed.tsx`:** rewrite off `useIncidentPages` + the
  IntersectionObserver sentinel (rootMargin 200px) from `TimelineClient`. Delete
  `FixedSizeList`, `ITEM_HEIGHT`, `measureRef`.
- **`components/IncidentCard.tsx`:** add a `next/image` thumb (fixed box,
  `object-cover`). Three states off `image_status`/`pixel_art_url`:
  - `pixel_art_url` present → thumb.
  - `image_status === 'suppressed'` → neutral block, **no "coming soon"** (the 5
    guardrail-#5 rows — "coming soon" is wrong for a suicide/self-harm story).
  - else → existing placeholder.
  - Row `Pick<>` gains `pixel_art_url | image_status`.

**Verify:** thumbs lazy-load, infinite scroll appends, filter/year drive it,
suppressed rows show the neutral state.

> **DONE (2026-08-07).** Landed as: `hooks/useIncidentPages.ts` (shared paging,
> adopted by both `IncidentFeed` and `TimelineClient`); `IncidentFeed` rewritten
> off the hook + an IntersectionObserver sentinel (react-window gone);
> `IncidentCard` gains a 112×63 `next/image` thumbnail; `pixel_art_url` added to
> the feed SSR + `/api/incidents` selects; `next.config.js` gains the
> `assets.yishunagain.com` remote pattern; `react-window` + `@types/react-window`
> removed from `package.json` (4 packages pruned). Build clean (routes unchanged,
> `/` + `/map` still Static/ISR), lint clean, 35 web tests + 10 war-room parity
> green. Verified in-browser: feed renders 20 cards, 18 with thumbnails routed
> through `/_next/image` (optimizer returns `200 image/png` from R2), `srcset` +
> `sizes="112px"` downscale to ~128px, `loading="lazy"`; `/api/incidents` page 0
> vs page 1 return disjoint 20-row sets carrying `pixel_art_url`; `/timeline`
> adopts the hook with no errors. Not verifiable in the non-compositing preview
> pane (needs a real browser): thumbnail pixels, the observer-driven scroll
> (layout isn't computed here — but it's the pattern `TimelineClient` already
> ran in prod), and the placeholder box visual.
>
> **Deviation from the plan above — `image_status` NOT added, allowlist
> untouched.** The plan wanted `image_status` in the feed selects and in
> `PUBLIC_INCIDENT_COLUMNS` so the card could tell "suppressed" from "not
> generated". At thumbnail scale those render as the SAME neutral box (a
> classification icon on `bg-surface`, no "coming soon"), so the distinction
> buys nothing — and exposing `image_status` publicly would leak the guardrail-#5
> suicide/self-harm inference (a `suppressed` value implies the content class).
> So the card keys off `pixel_art_url` presence alone, `image_status` stays out
> of every public response, and the security-reviewed `PUBLIC_INCIDENT_COLUMNS`
> is not touched. The "coming soon" wording problem the plan cited exists only on
> the large detail-page placeholder, which is out of Phase 3 scope.

## Phase 4 — Map route: emoji markers + rich preview

- **`app/(hud)/map/page.tsx` + `app/api/map/route.ts`:** add `pixel_art_url` and a
  **server-truncated `summary` teaser (~120 chars)** to both selects and to
  `MapFeature.properties`. Truncate server-side — full summaries would bloat the
  CDN-cached GeoJSON (`/api/map` is `s-maxage=300`).
- **`components/IncidentMap.tsx`** — replace the circle layer with managed HTML
  markers:
  - On load / year change: clear the marker ref array, build one
    `maplibregl.Marker` per feature; element is a styled emoji `<span>` from
    `classIcon()`, coloured via `PIN_COLOR` (referenced, not literal).
  - Filter (`?class=`): toggle element visibility by class — replaces
    `map.setFilter`.
  - Year: rebuild from the `/api/map?year=` refetch — replaces `source.setData`.
  - **Hover (desktop):** popup with image thumb + escaped teaser + title. Every
    interpolated field stays `escapeHtml`'d — the auto-publish XSS discipline at
    `IncidentMap.tsx:140` is preserved; the teaser is the same untrusted class.
  - **Tap (mobile):** first tap → preview; tapping the preview → navigate. Splits
    today's tap-navigates-immediately behaviour.
  - **Click (desktop):** unchanged → `router.push('/incidents/'+slug)`.

**Verify:** emoji pins render (not tofu), colours correct in both themes, hover
shows thumb+teaser, class filter hides/shows pins, year refetch swaps them, mobile
tap is two-stage.

> **DONE (2026-08-07).** Landed as: `lib/teaser.ts` (+ `teaser.test.ts`, 4 tests)
> for the server-side 120-char teaser; `summary` (teased) + `pixel_art_url` added
> to `/api/map` + the map SSR select + `MapFeature.properties`; `IncidentMap`
> rewritten from the circle layer to managed HTML `maplibregl.Marker` emoji pins
> in a dark circular badge ringed with the locked `PIN_COLOR`; hover/tap preview
> popup (image thumb + escaped teaser + title); filter and year now show/hide and
> rebuild the marker array instead of `setFilter`/`setData`. Build + lint clean,
> 39 web tests + 10 war-room parity green.
>
> **Fully verified in a real browser (Playwright/Chromium, since the in-app pane
> can't composite WebGL):** 25 pins render as ❤️🤡💀 (no tofu — Decision B holds),
> ring borders are the exact locked hex (`#4ECDC4`/`#FFE66D`/`#FF6B6B`); the
> class filter hides non-matching pins (DARK EVENTS → 12 💀 shown, 13 hidden);
> hover builds the popup with the R2 image thumb + classification + severity +
> teaser, `<script>`-free (escaping intact), and mouseleave removes it; a pin
> click navigates to `/incidents/<slug>`. `/api/map` teaser is capped at 120.
> Screenshot delivered to the operator.
>
> **Marker interaction model:** hover-capable devices (`matchMedia('(hover:
> hover)')`) preview on mouseenter and navigate on click; touch devices preview
> on first tap and navigate on a second tap of the same pin, with a map-background
> tap dismissing. The touch two-stage path is coded but was NOT exercised in the
> desktop Playwright run — verify on a real touch device.

## Phase 5 — Mobile bottom sheet + first responsive layer

Largest net-new work — no breakpoint utility or `@media` query is used anywhere
today (the `tailwind.config.js` `screens` are defined but unused).

- **New `components/BottomSheet.tsx`** (no new dependency): `position: fixed`
  bottom bar above the body's `overflow: hidden`. Collapsed = score + descriptor;
  expanded = breakdown + year selector (+ class chips, Phase 6). Tap to toggle;
  drag-up via touch handlers (`translateY`), tap as baseline. Renders the same
  `ChaosPanel` internals — one source of truth.
- **`HudShell` responsive split (`md` = 768px):**
  - `≥md`: 280px right sidebar as today; no sheet.
  - `<md`: sidebar `hidden md:block`; `<BottomSheet>` mounted; main area full
    width; map fills viewport minus nav minus bar; feed scrolls with the bar
    fixed.
  - Audit Nav's fixed `height:72` and other hard values for small screens.

**Verify** (`resize_window` mobile preset): bar visible, swipe/tap expands, year
change from the sheet updates URL + both surfaces, no horizontal scroll, both
themes.

> **DONE (2026-08-07).** Landed as: `hooks/useChaosYear.ts` (the year + per-year
> stats logic, extracted from `ChaosSidebar` so the sidebar and the sheet share
> one implementation); `ChaosSidebar` reduced to a thin `ChaosPanel` render;
> **new `components/BottomSheet.tsx`** (mobile-only, `position:fixed`, tap or
> swipe to expand the same `ChaosPanel` to 70vh); `(hud)/layout.tsx` hides the
> `<aside>` (`hidden md:block`), mounts the sheet, and pads the content 54px at
> the bottom so it clears the collapsed bar; **Nav + NavLinks + FilterChips made
> responsive** (the first breakpoints used in the app) so a 375px header and chip
> row don't overflow. Build + lint clean, 39 web tests + 10 war-room parity green.
>
> **Verified in real Chromium (Playwright) at 375 AND 1280:**
> - Mobile: sidebar hidden, sheet visible with the collapsed bar
>   ("CHAOS 60 CRITICAL 2026 ▴"); tapping expands to 70vh showing the year
>   selector + breakdown (`aria-expanded` toggles); nav fits (header overflow 0);
>   four chips fit as emoji+count (row overflow 0); no horizontal page scroll;
>   feed cards render thumbnails.
> - Desktop: sidebar back at 280px, sheet `display:none`, nav links 14px, chips
>   show full labels ("❤️ GOOD VIBES (8)"). Screenshots delivered.
>
> **Deviations, all deliberate:**
> - **No `HudShell` / `useMediaQuery`.** The responsive swap is pure CSS
>   (`hidden md:block` / `md:hidden`); both `ChaosSidebar` and `BottomSheet`
>   mount and each runs `useChaosYear`. On the current year neither fetches (SSR
>   seed); only a year *change* triggers the duplicate `/api/chaos` — same
>   cached endpoint, folded into the Phase 6 dedupe note. Avoids the
>   SSR-viewport / hydration problem a `useMediaQuery` conditional render carries.
> - **Nav/chips responsive was beyond "audit Nav's fixed values"** but the
>   mobile layout is broken without it (header + chip row overflow the width).
> - **Swipe gesture** is touchstart/move/end delta (≥30px = open/close) with tap
>   as the baseline — not a finger-following drag. Playwright exercised the tap;
>   the swipe delta wants a real touch device, same caveat as Phase 4's tap.

## Phase 6 — Class filter in the URL, tests, cleanup

- **`?class=`:** `FilterChips` reads/writes the param (mirrors year). Feed →
  filters list; Map → filters markers; mobile → in the sheet's expanded view. Both
  params now persist across nav symmetrically.
- **Tests** (`node:test`, per `CLAUDE.md`): add `lib/params.test.ts` for the new
  pure helpers + the teaser truncator; extend `lib/utils.test.ts` if a display
  helper changes. The war-room **parity test** (`utils.paragraphs.test.ts`) breaks
  only if the shared `canonicalUrl`/`uniqueSources`/paragraph blocks change — this
  restructure shouldn't touch them; if it does, both copies change together.
- **Remove** `react-window` from `package.json` (Decision E); delete the dissolved
  `HomeClient.tsx`.
- **Docs, same commit** (`CLAUDE.md` hard rule): update `FRONTEND_SPEC.md` and the
  `CLAUDE.md` "Frontend Theme" section — the HUD is now two routes with a shared
  sidebar / mobile bottom sheet, the map uses HTML markers, the feed is
  image-first with infinite scroll. Delete stale claims rather than softening.

---

## Risk register

1. **Emoji glyph rendering** — resolved by Decision B (HTML markers). A symbol
   layer would need a spike first; expect tofu.
2. **Marker lifecycle** — hand-managing filter + year on HTML markers is the map's
   main new complexity; the array-rebuild approach bounds it.
3. **CDN-cached map GeoJSON size** — mitigated by server-side teaser truncation;
   watch payload on high-count years.
4. **XSS discipline in the richer popup** — must survive; every field escaped.
5. **First responsive layer on a fixed height budget** — Phase 5 is where schedule
   risk concentrates (nav 72px + map 45vh + chips 48px + feed remainder, all with
   `overflow: hidden` on `html, body`).
6. **ISR vs dynamic for `?year=`** — settled by Decision A; revisit only if
   first-paint correctness outranks ISR.
7. **Locked colours in four places** — treat `PIN_COLOR` as source; don't add a
   fifth; never edit values.

---

## Commit sequence

1. Foundation: route group, `HudShell`, params, Nav, SEO move, sitemap.
2. Self-contained `ChaosSidebar` + `?year=`.
3. `useIncidentPages` hook + Feed images + infinite scroll (drop react-window).
4. Map emoji markers + rich preview (+ map/api columns).
5. Bottom sheet + responsive shell.
6. `?class=` filter, tests, docs, cleanup.

Each is independently shippable and browser-verifiable before the next.
