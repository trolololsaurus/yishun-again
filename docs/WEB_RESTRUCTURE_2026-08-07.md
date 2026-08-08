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
