# Frontend Spec — Yishun Again (`apps/web`)

**Status:** CANONICAL · Locked to the live, deployed site (the working source of truth)
**Supersedes:** TechSpec v1_9 §6.0–6.0e and §6.1 for all frontend design + behavior.
Where this document and any TechSpec section disagree, **this document wins.**

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
| `custom` + `custom_label='CULTURE'` | CULTURE 🌐 | `culture` | `#A78BFA` (violet) |

These four colors and their display names are immutable. Any change is a
regression. (This corrects the TechSpec §6.0c copy-error where GOOD VIBES wrongly
shared `#E87070` with DARK EVENTS — live has them correctly distinct.)

---

## 2. Typography — LOCKED

**Families:** Press Start 2P (display/HUD), Courier Prime → Courier New fallback
(body/content). Google Fonts. **No font below 9px.**

**Press Start 2P (HUD/chrome):**
| Element | Size |
|---|---|
| Logo (YISHUN / AGAIN) | 26px |
| Nav links | 14px |
| Chaos score number | 48px |
| `/100` | 20px |
| Chaos descriptor (QUIET…) | 13px |
| Breakdown stat counts | 20px |
| Chaos descriptor / section headers | 11px |
| Filter chip labels | 10px |
| Year selector label | 10px |
| Badges (MILESTONE) | 9px |  <!-- DEVELOPING badge removed June-2026; see Feed deltas below -->

<!--
### Feed / incident-card deltas (June-2026 pass)
- **DEVELOPING badge + banner removed** (confused readers). `is_developing` drives
  only the "N reports · First reported …" line — **not** feed sort (see §5; the
  feed is newest-first).
- **Lightning (⚡) = corroboration**, derived live from `corroboration_count`:
  `bolts = max(0, corroboration_count − 1)` (2 sources → ⚡, 3 → ⚡⚡, …). The legacy
  `hype_meter` column is no longer read. Tooltip updated accordingly. Same rule in
  feed card, map popup, and detail page.
- **Story timeline collapses same-date entries** to one node (most-significant role
  wins the label); renders only with 2+ distinct dates.
- **"Time to verdict"** is computed from the last verdict/sentencing/appeal entry in
  `source_timeline` (helpers `lastVerdictEntry` / `verdictNoun`), never from
  `incident_date` (which is the event date). Label adapts: "to verdict / sentencing / appeal".
- Helpers: `apps/web/lib/utils.ts` → `hypeFromSources`, `lastVerdictEntry`,
  `verdictNoun`, `collapseTimelineByDate`.
-->


**Courier Prime (content):**
| Element | Size / weight |
|---|---|
| Incident titles | 16px bold |
| Body / summaries | 16px |
| Feed header | 14px |
| Map popup title | 16px / 700 |
| Map popup metadata | 14px |
| Metadata (date, area) | 13px |
| Legal disclaimer | 10px |

---

## 3. Layout — LOCKED (one-page, no page scroll)

| Element | Value |
|---|---|
| `html, body` | `height: 100%; overflow: hidden` — **no page-level scroll, ever** |
| Header | 72px fixed top |
| Right sidebar (Chaos Panel) | 280px fixed, `flex-none` — **never collapses** |
| Map | 45vh |
| Filter chips bar | 48px fixed, `flex-none` |
| Incident feed | `flex-1 min-h-0 overflow-y-auto` — fills remaining height, scrolls internally |
| Main left column | `flex-1 min-w-0 flex flex-col overflow-hidden` |
| Scrollbar | 6px width, no border-radius |

Only the incident feed scrolls. Everything else is fixed. This is the
command-console frame.

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

**Filters** (`FilterState`): `all | heart | clown | dagger` — **single-select**.
Selecting a chip filters to that classification; selecting it again returns to
`all`. CULTURE appears in the feed but is **not** a filter chip (known gap —
candidate for a future CULTURE chip).

**Display date:** cards, detail page, JSON-LD, and sitemap all use
`incident_date` (the real article/event date), NOT `published_at` (operator
approve-time). `fmtDate(null)` renders `—`, never epoch/1970.

---

## 6. Year selector + Chaos Panel — LOCKED

**Year selector** (in the sidebar Chaos Panel): defaults to current SGT year.
Selecting a year sends `year` to `/api/incidents`, `/api/chaos`, and `/api/map`
simultaneously — all three update together. Feed header shows
"Showing [year] · [N] loaded". The dropdown always has a year selected (there is
no "all years" state), so there is **no reset/clear control** — it would be a
no-op.

**Year validation — single shared sanitiser.** All three routes use
`sanitiseYear` (`lib/utils.ts`), which accepts any 4-digit year (no hardcoded
floor — historical backfills predate 1990). Per-route handling of a missing /
invalid value:
- `/api/map` and `/api/incidents` (absent param) → default to current year /
  unfiltered respectively.
- `/api/chaos`: absent param → current year; **present-but-invalid → HTTP 400**.
  The client surfaces this as a visible **"CHAOS DATA ERROR"** state in the Chaos
  Panel (no silent fallback to stale/other-year numbers).

> **Regression guard:** never reintroduce a per-route year regex or a numeric
> floor in `sanitiseYear`. A floor (the old `>= 1990`) silently dropped valid
> pre-1990 years on the feed and chaos counts while the map — which used its own
> regex — kept working, producing a confusing "only the map filters" bug.

**Chaos Index formula** (`lib/utils.ts`):
```
raw = Σ (severity × weight)
  dagger ×2.0 · clown ×1.0 · heart ×−1.0 · custom/culture ×0
score = clamp(round(raw / 300 × 100), 0, 100)
```
Descriptors: `<20` Quiet · `<40` Simmering · `<60` Elevated · `<80` Critical ·
`≥80` Apocalyptic.

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
- **Filter empty-state** — what renders when a filter returns zero incidents.
- **CULTURE filterability** — currently unfilterable; add a chip or leave as-is.
- **Load-more / end-of-feed** behavior at the bottom of pagination.
- **Map error/empty state** (tied to §4 known issue).

---

## 9. Immutable rules

1. **Classification colors + names never change** — the soul of the project.
2. **One-page, no page-level scroll** — only the feed scrolls.
3. **Sidebar never collapses.**
4. **Display date is `incident_date`, never `published_at`.**
5. **This file is the source of truth** — fix the frontend *to this*, and update
   *this* when the design intentionally changes (then note it superseded the
   prior value). Don't let code and spec drift apart again.
