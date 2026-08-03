# Frontend Spec — Yishun Again (`apps/web`)

**Status:** CANONICAL · Locked to the live, deployed site (the working source of truth)
**Supersedes:** TechSpec v1_9 §6.0–6.0e and §6.1 for all frontend design + behavior.
Where this document and any TechSpec section disagree, **this document wins.**
**Stack (locked in `apps/web/package-lock.json`):** Next.js **16.2.12** App Router
(Turbopack is the default bundler; `next.config.js` pins `turbopack.root` because
the repo root also carries a lockfile) · React **19.2.8** · Tailwind CSS 3.4 ·
MapLibre GL JS 3.6.2 · react-window 1.8.11. Anything describing this app as
Next.js 14 / React 18 is stale.

> **Why this exists:** the frontend design was never captured in a spec — it lived
> only in `apps/web/globals.css`, `tailwind.config.js`, and the components. The
> TechSpec carried three *conflicting and stale* palettes (§6.0c `#183828` teal,
> §6.1 `#0D0D0D`, plus a copy-error where GOOD VIBES shared DARK EVENTS' hex).
> This file replaces all of that with the values that actually render live.

---

## 0. Aesthetic (named)

**Panzer Dragoon Saga — Midnight Command Centre.** A SEGA 32-bit JRPG game-HUD
aesthetic: a fixed, non-scrolling command console framing a live incident feed.
Pixel-display typography (Press Start 2P) for chrome/HUD elements; monospace
(Courier Prime) for readable content. Deep midnight-navy field, amber console
accents, three signal colors for incident classification.

The literal anchor, already in `globals.css`:
`/* Panzer Dragoon Saga — Midnight Command Centre */`

---

## 1. Color palette — LOCKED

Source of truth: `globals.css` CSS custom properties (mirrored in `tailwind.config.js`).

| Token | Hex | Purpose |
|---|---|---|
| `--color-bg` | `#0A0E1A` | Page background (midnight navy) |
| `--color-surface` | `#0F1526` | Cards, dropdowns |
| `surface2` | `#0F1A2E` | Hover surface |
| `--color-border` | `#1E2D4A` | All borders |
| `--color-amber` | `#C07830` | Primary accent — logo, scores, links |
| `--color-amber-dim` | `#805828` | `/100`, metadata, dim amber |
| `--color-sienna` | `#803018` | Active states |
| `--color-map-bg` | `#070B14` | Map container background + pin stroke |
| `--color-text-primary` | `#E8E8F0` | Primary text |
| `--color-text-secondary` | `#7A8BAA` | Secondary / metadata text |
| `--color-text-dim` | `#3D4F6A` | Dimmed text, separators |

### Classification colors — **THE SOUL OF THE PROJECT. NEVER MODIFY.**

| Classification | Display name | Token | Hex |
|---|---|---|---|
| `heart` | GOOD VIBES ❤️ | `--color-good-vibes` | `#4ECDC4` (teal) |
| `clown` | ABSURDITIES 🤡 | `--color-absurdities` | `#FFE66D` (yellow) |
| `dagger` | DARK EVENTS 💀 | `--color-dark-events` | `#FF6B6B` (coral) |
| `custom` + `custom_label='CULTURE'` | YISHUN ON THE MAP 🌐 | `culture` | `#A78BFA` (violet) |

These four colors and their display names are immutable. Any change is a
regression. (This corrects the TechSpec §6.0c copy-error where GOOD VIBES wrongly
shared `#E87070` with DARK EVENTS — live has them correctly distinct.)

Two details the table can't carry:
- `culture` is a **Tailwind-only** token (`tailwind.config.js`), not a CSS custom
  property — there is no `--color-culture` in `globals.css`. The other three are
  both.
- `custom` rows carry a second recognised label, `UNSOLVED CRIME` (❓ "Cold case
  — perpetrator never identified or convicted"). It has **no colour of its own**:
  `classColor` returns `text-text-secondary` and `pinColor` returns `#7A8BAA`,
  which is also the fallback for any unrecognised `custom_label`. Display names
  and icons live in `CUSTOM_LABEL` / `CUSTOM_ICON` (`lib/utils.ts`).

---

## 2. Typography — LOCKED

**Families:** Press Start 2P (display/HUD), Courier Prime → Courier New fallback
(body/content). Google Fonts. **Floor is 9px, with one deliberate exception:** the
story-timeline node role labels (`REPORTED` / `VERDICT` / …) are 8px, because the
label must fit a ~80px node without wrapping. Nothing else may go below 9px.

**Press Start 2P (HUD/chrome):**
| Element | Size |
|---|---|
| Logo (YISHUN / AGAIN) | 26px |
| Nav links | 14px |
| Chaos score number | 48px |
| `/100` | 20px |
| Chaos descriptor (QUIET…) | 13px |
| Breakdown stat counts | 20px |
| Section headers (CHAOS INDEX, INCIDENT BREAKDOWN) | 11px |
| `MAP UNAVAILABLE` error heading | 11px |
| Filter chip labels | 10px |
| Year selector label (YEAR) | 10px |
| `STORY TIMELINE` disclosure heading | 10px |
| Badges (MILESTONE) | 9px |  <!-- DEVELOPING badge removed June-2026; see §5 -->
| Story-timeline node role label | 8px (the exception above) |

**Courier Prime (content):**
| Element | Size / weight |
|---|---|
| Feed-card incident titles | 16px bold |
| Detail-page title (`<h1>`) | 20px bold |
| Body / summaries | 16px |
| Feed header | 14px |
| Map popup title | 16px / 700 |
| Map popup metadata | 14px |
| Feed-card metadata (date, area) | 13px |
| Legal disclaimer | 10px |

---

## 3. Layout — LOCKED (one-page, no page scroll)

| Element | Value |
|---|---|
| `html, body` | `height: 100%; overflow: hidden` — **no page-level scroll, ever** |
| Header (`Nav`) | 72px fixed top, `flex-none` |
| `<main>` | `flex-1 min-h-0 flex flex-col overflow-y-auto` — the scroll region for the non-homepage routes (detail, `/timeline`, `/about`) |
| Right sidebar (Chaos Panel) | 280px fixed, `flex-none` — **never collapses**; `overflow-y-auto` so a short viewport can still reach the disclaimer |
| Map | 45vh |
| Filter chips bar | 48px fixed, `flex-none` |
| Incident feed | wrapper `flex-1 min-h-0 overflow-hidden`; the scroll region is react-window's `FixedSizeList`, measured by a callback ref |
| Main left column | `flex-1 min-w-0 flex flex-col overflow-hidden` |
| Scrollbar | 6px width, `height: 0` (never a horizontal bar), no border-radius |

The page itself never scrolls. On the homepage the only internal scroll regions
are the feed and the sidebar; the map and chip bar are fixed. This is the
command-console frame.

> The feed's list height is measured by a **callback ref**, not a
> `useRef` + effect, so the first commit already has the real height instead of
> the 600px SSR fallback. The earlier ResizeObserver-only version dropped its
> first callback whenever `contentRect.height` was still 0 and never got a
> second one — the list stayed 600px tall inside a ~235px box and clipped its own
> scroll region. Don't revert it.

---

## 4. Map — LOCKED

**Tile provider: OpenFreeMap "Liberty"** — keyless, no token, no domain
registration, served via Cloudflare CDN (including a Singapore edge). This is
the single supported map system. Stadia and CartoDB are **not** used; do not
reintroduce them.

| Property | Value |
|---|---|
| Style URL | `https://tiles.openfreemap.org/styles/liberty` |
| Configured via | `process.env.NEXT_PUBLIC_MAPLIBRE_STYLE`, hardcoded fallback to the same Liberty URL in `IncidentMap.tsx` |
| Container bg | `#070B14` |
| Max bounds | `[[103.80, 1.40], [103.87, 1.46]]` |
| Center | `[103.8350, 1.4290]` |
| Default zoom | 13.5 |
| Pin radius | interpolate 7px (zoom 12) → 11px (zoom 15) |
| Pin opacity | 0.92 |
| Pin stroke | 1.5px, `#070B14` |
| Pin colors | heart `#4ECDC4` · clown `#FFE66D` · dagger `#FF6B6B` · culture `#A78BFA` |
| Popup max-width | 260px |

**Behavior:** hover → teaser popup; click → navigate to incident page;
mouse-leave → dismiss popup.

**Resilience (implemented in `IncidentMap.tsx`):**
- Hardcoded fallback (`FALLBACK_MAP_STYLE`) equals the Liberty URL, so the map
  loads even if `NEXT_PUBLIC_MAPLIBRE_STYLE` is unset or blank. Uses `||` so an
  empty-string env var also falls back. **The env var must never be set to a
  different/broken value** — a set-but-wrong value overrides the fallback. The
  safest production posture is to leave the Vercel env var unset and rely on the
  fallback.
- `map.on('error')` surfaces a visible "MAP UNAVAILABLE" state instead of
  hanging on "Loading map…".
- A 12s load-timeout safety net catches style-fetch failures that MapLibre
  swallows without emitting `error`.
- The "Loading map…" placeholder clears on both `load` and `error`.

> **Note on theme:** Liberty is a *light* basemap, which contrasts with the
> dark Midnight Command Centre UI. This is an accepted, deliberate choice. If a
> dark basemap is ever desired, swap the style URL only — all other map values
> above stay fixed.

---

## 5. Feed behavior — LOCKED

**Sort order** (`/api/incidents` and the SSR first page in `page.tsx` — kept identical):
```
ORDER BY incident_date DESC NULLS LAST,
         id            DESC
```
Latest incident always on top (newest event date first); `id` is the
deterministic tiebreaker (prevents duplicate-slug pagination). `is_developing`
no longer floats stories to the top — stale flags were burying newer incidents.

**Pagination:** `PAGE_SIZE = 20`, client virtualization `ITEM_HEIGHT = 152px`.
Infinite scroll: `onItemsRendered` fires `loadMore()` once the last visible row
is within 3 of the end. Every request takes a ticket (`reqRef`) and only the
newest may write state — otherwise a fast filter switch could land the previous
filter's rows on top of the current one's.

**Filters** (`FilterState`): `all | heart | clown | dagger` — **single-select**,
rendered as four chips (`ALL` is its own chip). Clicking a chip sets that filter;
clicking the active chip again is a **no-op**, not a toggle back to `all` — use
the `ALL` chip. CULTURE appears in the feed but is **not** a filter chip (known
gap — candidate for a future CULTURE chip).

**Display date:** cards, detail page and JSON-LD (`datePublished`) all use
`incident_date` (the real article/event date), NOT `published_at` (operator
approve-time). `fmtDate(null)` renders `—`, never epoch/1970. Two card
variations: a developing row shows `N reports · First reported <date>` instead of
the date/area line, and a concluded row hides `incident_date` in favour of the
first-reported → verdict line. `sitemap.ts` **orders** by `incident_date` but
sets `lastModified` to `published_at ?? incident_date` — that field is a crawler
freshness hint, not a display date, so approve-time is the right value there.

### Feed / incident-card deltas (June-2026 pass)
- **DEVELOPING badge + banner removed** (confused readers). No `DEVELOPING`
  string exists anywhere in `apps/web`. `is_developing` now drives exactly two
  things: the "N reports · First reported …" line and a 2px amber left border on
  the card — **not** feed sort (the feed is newest-first, above).
- **Lightning (⚡) = corroboration**, derived live from `corroboration_count`:
  `bolts = max(0, corroboration_count − 1)` (2 sources → ⚡, 3 → ⚡⚡, …). The legacy
  `hype_meter` column is no longer read — it is excluded from
  `PUBLIC_INCIDENT_COLUMNS`, so the public site never even fetches it. Tooltip
  (`HYPE_TOOLTIP`) updated accordingly. Same rule in feed card, map popup, and
  detail page.
- **Story timeline collapses same-date entries** to one node (most-significant
  role wins the label, via `ROLE_PRIORITY`); renders only with 2+ distinct dates.
- **"Time to verdict"** is computed from the last verdict/sentencing/appeal entry
  in `source_timeline` (helpers `lastVerdictEntry` / `verdictNoun`), never from
  `incident_date` (which is the event date, and equals `first_reported_at` on
  most rows — the old "1 day to verdict" bug). Label adapts: "to verdict /
  sentencing / appeal".
- Helpers: `apps/web/lib/utils.ts` → `hypeFromSources`, `lastVerdictEntry`,
  `verdictNoun`, `collapseTimelineByDate`.

---

## 6. Year selector + Chaos Panel — LOCKED

**Year selector** (in the sidebar Chaos Panel): defaults to the year the SSR
homepage computed with `new Date().getFullYear()` — that is the **server's**
year (UTC on Vercel), not SGT, so for the last 8 hours of 31 Dec SGT it lags the
local year by one. Options come from distinct `incident_date` years, parsed off
the `YYYY-MM-DD` string rather than via `new Date()` (QA L5: `new Date()` parses
as UTC, which rolls a 1 Jan SGT date back into the previous year).

Selecting a year sends `year` to `/api/incidents`, `/api/chaos`, and `/api/map`
simultaneously — all three update together. Feed header shows
"Showing [year] · [N] loaded". The dropdown always has a year selected (there is
no "all years" state), so there is **no reset/clear control** — it would be a
no-op.

**Year validation — single shared sanitiser.** All three routes use
`sanitiseYear` (`lib/utils.ts`), which accepts any 4-digit year (no hardcoded
floor — historical backfills predate 1990). Per-route handling of a missing /
invalid value:
- `/api/map`: absent **or** invalid → current year.
- `/api/incidents`: absent **or** invalid → unfiltered (no year predicate).
- `/api/chaos`: absent param → current year; **present-but-invalid → HTTP 400**.
  The client surfaces this as a visible **"CHAOS DATA ERROR"** state in the Chaos
  Panel (no silent fallback to stale/other-year numbers). The same state covers
  any non-2xx: an upstream Supabase error is a **500**, deliberately, because a
  200 with an all-zero payload would render *and CDN-cache* "Quiet" and the
  client's error path would never fire.

> **Regression guard:** never reintroduce a per-route year regex or a numeric
> floor in `sanitiseYear`. A floor (the old `>= 1990`) silently dropped valid
> pre-1990 years on the feed and chaos counts while the map — which used its own
> regex — kept working, producing a confusing "only the map filters" bug.

**Chaos Index formula** (`computeChaosScore` in `lib/utils.ts` — the only place
the aggregate is computed; `/api/chaos` and the SSR homepage both call it):
```
raw   = Σ (severity × weight), floored at 0
  dagger ×3.0 · clown ×1.5 · heart ×−1.0 · custom/culture ×0
score = round(100 × (1 − e^(−raw / CHAOS_SCALE)))        CHAOS_SCALE = 300
```
> Weights corrected July 2026 — this block previously said `dagger ×2.0 · clown
> ×1.0`, which never matched the code (`utils.ts` and `stage2_writer.py` have
> always used 3.0 / 1.5).
>
> Scoring rebalanced the same day. It was `clamp(round(raw / 300 × 100), 0, 100)`
> — linear with a hard cliff, so raw 300 (about 20 severity-5 daggers) pegged a
> year at 100 for good; 2026 read 87 by July. The curve now has diminishing
> returns and approaches 100 without reaching it: raw 300 → 63, Apocalyptic
> (≥80) needs raw ≈ 483. 2026 reads 58.

Descriptors: `<20` Quiet · `<40` Simmering · `<60` Elevated · `<80` Critical ·
`≥80` Apocalyptic.

> **The score is computed on read and never stored.** Both callers sum it live
> over that year's published `incidents` rows (`severity ?? 0`, so a null
> severity contributes 0 rather than `NaN` — QA M3). The `chaos_index_snapshots`
> table exists in migration 001 but **nothing writes to it**: its only reference
> in the whole repo is a *read* in
> `orchestrator/herald_agent.py::_check_chaos_record`, which needs ≥ 2 rows and
> therefore can never fire. Anything claiming the index is "computed on every
> publish and stored" describes a path that does not exist — do not build a
> frontend read against that table.
>
> Per-incident `chaos_contribution` is a different number, written by Stage 2
> (`_compute_chaos_contribution`, same 3.0 / 1.5 / −1.0 multipliers). It is
> deliberately **not** in `PUBLIC_INCIDENT_COLUMNS` and is not what the panel
> shows.

---

## 7. Caching / ISR — LOCKED

| Route | Cache header |
|---|---|
| `/api/incidents` | `s-maxage=60, stale-while-revalidate=120` |
| `/api/chaos` | `s-maxage=60, stale-while-revalidate=30` |
| `/api/map` | `s-maxage=300, stale-while-revalidate=60` |
| Incident pages (ISR) | `revalidate = 3600` |
| Homepage (ISR) | `revalidate = 60` |

---

## 8. Behavior gaps (specify before building features that touch them)

Not yet defined anywhere — decide when first relevant:
- **Filter empty-state** — a filter returning zero incidents currently renders
  the header ("Showing [year] · 0 loaded") above an empty list body, because
  `itemCount` is 0 once `hasMore` is false. No "nothing here" copy, no
  illustration. That is the behavior, not a decision.
- **CULTURE filterability** — currently unfilterable; add a chip or leave as-is.
- **Map empty-state** — a year with no plottable incidents renders a working map
  with zero pins and no message.

Two entries previously listed here have since been built and are specified above,
so they are no longer gaps: **load-more / end-of-feed** (§5 — auto-fetch within 3
rows of the end; the trailing row reads "Loading…" then "No more incidents.") and
**map error state** (§4 — "MAP UNAVAILABLE" plus the error text, from
`map.on('error')` or the 12s timeout).

---

## 9. Immutable rules

1. **Classification colors + names never change** — the soul of the project.
2. **One-page, no page-level scroll** — `html, body { overflow: hidden }`. On the
   homepage only the feed and the sidebar scroll, internally.
3. **Sidebar never collapses.**
4. **Display date is `incident_date`, never `published_at`.**
5. **This file is the source of truth** — fix the frontend *to this*, and update
   *this* when the design intentionally changes (then note it superseded the
   prior value). Don't let code and spec drift apart again.
