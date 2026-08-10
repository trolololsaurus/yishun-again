import type { Classification, SourceTimelineEntry } from './types'

export const CLASS_ICON: Record<string, string> = {
  heart:  '❤️',
  clown:  '🤡',
  dagger: '💀',
  custom: '📌',
}

// User-facing display labels (data values stay 'heart' | 'clown' | 'dagger')
export const CLASS_LABEL: Record<string, string> = {
  heart:  'GOOD VIBES',
  clown:  'ABSURDITIES',
  dagger: 'DARK EVENTS',
  custom: 'CUSTOM',
}

// Icon + label, e.g. "❤️ GOOD VIBES"
export function classDisplay(cls: string, customLabel?: string | null): string {
  const icon = classIcon(cls, customLabel)
  const label = classLabel(cls, customLabel)
  return icon ? `${icon} ${label}` : label
}

// Hover tooltips — shared across feed cards, detail page, timeline, map popup
export const CLASS_TOOLTIP: Record<string, string> = {
  heart:  'Good Vibes — community wins and feel-good moments',
  clown:  'Absurdities — baffling or inexplicably stupid behaviour',
  dagger: 'Dark Events — crime, violence, serious incidents',
}

const CULTURE_TOOLTIP = 'Yishun on the Map — pop-culture and media mentions'

// classification + custom_label aware variants — special-case
// classification='custom' + custom_label='CULTURE' ("Yishun on the Map"),
// otherwise fall back to the generic maps above.
const CUSTOM_ICON: Record<string, string> = {
  CULTURE:       '🌐',
  'UNSOLVED CRIME': '❓',
}
const CUSTOM_LABEL: Record<string, string> = {
  CULTURE:       'YISHUN ON THE MAP',
  'UNSOLVED CRIME': 'UNSOLVED CRIME',
}
const CUSTOM_TOOLTIP: Record<string, string> = {
  CULTURE:       'Yishun on the Map — pop-culture and media mentions',
  'UNSOLVED CRIME': 'Cold case — perpetrator never identified or convicted',
}

export function classIcon(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel) return CUSTOM_ICON[customLabel] ?? '📌'
  return CLASS_ICON[cls] ?? ''
}

export function classLabel(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel) return CUSTOM_LABEL[customLabel] ?? customLabel
  return CLASS_LABEL[cls] ?? cls.toUpperCase()
}

export function classColor(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return 'text-culture'
  if (cls === 'custom') return 'text-text-secondary'
  return CLASS_COLOR[cls] ?? ''
}

export function classTooltip(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel) return CUSTOM_TOOLTIP[customLabel] ?? ''
  return CLASS_TOOLTIP[cls] ?? ''
}

export function pinColor(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return PIN_COLOR.culture
  if (cls === 'custom') return '#7A8BAA'
  return PIN_COLOR[cls] ?? '#7A8BAA'
}

export const HYPE_TOOLTIP = 'Corroboration — one bolt per extra source confirming this story (2 sources = ⚡, 3 = ⚡⚡, …)'

// Lightning count from the number of sources: 1 source → none, 2 → 1 bolt,
// 3 → 2 bolts, etc. Grows as more sources merge into one incident.
export function hypeFromSources(sourceCount: number | null | undefined): number {
  return Math.max(0, (sourceCount ?? 1) - 1)
}

export function severityTooltip(sev: number | null): string {
  return `Severity ${sev ?? 0}/5`
}

// Map pin colours — Panzer Dragoon classification palette
// heart=teal-cyan (GOOD VIBES), clown=bright yellow (ABSURDITIES), dagger=coral red (DARK EVENTS)
export const PIN_COLOR: Record<string, string> = {
  heart:   '#4ECDC4',
  clown:   '#FFE66D',
  dagger:  '#FF6B6B',
  culture: '#A78BFA',
}

// Badge colors for classification chips / badges
export const CLASS_COLOR: Record<string, string> = {
  heart:  'text-good-vibes',
  clown:  'text-absurdities',
  dagger: 'text-dark-events',
  custom: 'text-muted',
}

export function severityDiamonds(sev: number | null): string {
  if (!sev) return ''
  return '◆'.repeat(sev) + '◇'.repeat(Math.max(0, 5 - sev))
}

export function hypeMeter(hype: number): string {
  if (!hype) return ''
  return '⚡'.repeat(hype)
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-SG', {
    timeZone: 'Asia/Singapore',
    day:  '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

// Compact duration for inter-node gap labels: "55 days", "3 months", "2 yrs 3 mo"
export function formatDurationGap(start: Date, end: Date): string {
  const totalMonths = (end.getFullYear() - start.getFullYear()) * 12
    + (end.getMonth() - start.getMonth())
  if (totalMonths < 1) {
    const days = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86_400_000))
    return `${days} day${days !== 1 ? 's' : ''}`
  }
  const years  = Math.floor(totalMonths / 12)
  const months = totalMonths % 12
  if (years === 0) return `${months} month${months !== 1 ? 's' : ''}`
  if (months === 0) return `${years} yr${years !== 1 ? 's' : ''}`
  return `${years} yr${years !== 1 ? 's' : ''} ${months} mo`
}

// Duration between two dates expressed as human-readable string.
// Uses month-based precision; < 1 month falls back to days.
export function formatDuration(start: Date, end: Date): string {
  const totalMonths = (end.getFullYear() - start.getFullYear()) * 12
    + (end.getMonth() - start.getMonth())
  if (totalMonths < 1) {
    const days = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86_400_000))
    return `${days} day${days !== 1 ? 's' : ''}`
  }
  const years  = Math.floor(totalMonths / 12)
  const months = totalMonths % 12
  if (years === 0) return `${months} month${months !== 1 ? 's' : ''}`
  if (months === 0) return `${years} year${years !== 1 ? 's' : ''}`
  return `${years} year${years !== 1 ? 's' : ''} ${months} month${months !== 1 ? 's' : ''}`
}

// Timeline roles that conclude a legal story (verdict / sentencing / appeal).
// Shared by the detail-page timeline and the feed card so the "time to verdict"
// duration is computed identically in both places.
export const VERDICT_ROLES = new Set(['verdict', 'sentencing', 'appeal', 'appeal_dismissed'])

// The last conclusion entry in a source_timeline (verdict/sentencing/appeal),
// or null if the story has not concluded. This is the REAL verdict date —
// incident_date is the event date and is wrong to use here.
export function lastVerdictEntry(
  timeline: SourceTimelineEntry[] | null | undefined
): SourceTimelineEntry | null {
  if (!Array.isArray(timeline)) return null
  for (let i = timeline.length - 1; i >= 0; i--) {
    if (VERDICT_ROLES.has(timeline[i]?.role ?? '')) return timeline[i]
  }
  return null
}

// Noun for the conclusion, e.g. "to sentencing" / "to appeal" / "to verdict".
export function verdictNoun(role: string | null | undefined): string {
  if (role === 'sentencing') return 'sentencing'
  if (role === 'appeal' || role === 'appeal_dismissed') return 'appeal'
  return 'verdict'
}

// Significance order — used to pick the representative role when several
// timeline entries share one date (a conclusion outranks a routine update).
const ROLE_PRIORITY: Record<string, number> = {
  appeal_dismissed: 6, appeal: 6, sentencing: 5, verdict: 4,
  correction: 3, follow_up: 2, update: 1, initial: 0,
}

// Collapse a source_timeline so each DATE appears once (item: don't stamp
// multiple REPORTED/VERDICT nodes on the same day). When several entries share
// a date, the most significant role wins the node's label. Sorted by date.
export function collapseTimelineByDate(
  timeline: SourceTimelineEntry[] | null | undefined
): SourceTimelineEntry[] {
  if (!Array.isArray(timeline)) return []
  const byDate = new Map<string, SourceTimelineEntry>()
  for (const e of timeline) {
    if (!e?.date) continue
    const cur = byDate.get(e.date)
    if (!cur) { byDate.set(e.date, e); continue }
    const pe = ROLE_PRIORITY[e.role ?? 'initial'] ?? 0
    const pc = ROLE_PRIORITY[cur.role ?? 'initial'] ?? 0
    if (pe > pc) byDate.set(e.date, e)
  }
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
}

// ── Shared location for a `same_location` incident link ─────────────────────
//
// "Same location" on its own tells the reader nothing — the whole archive is
// one town. The location is DISCOVERED by intersecting the two incidents' own
// location fields, never hardcoded: every confirmed same_location link in the
// DB agrees on `area_name` (all 137 of them), and some also agree on the block.
// Returns null when the only thing the two share is "Yishun", which is not a
// location worth printing.

const GENERIC_AREAS = new Set(['yishun', 'yishun town', 'yishun estate'])

function normLoc(raw: string | null | undefined): string {
  return (raw ?? '').trim().toLowerCase().replace(/\s+/g, ' ')
}

interface LocatedIncident {
  area_name:    string | null
  block_number: string | null
}

export function sharedLocationLabel(
  a: LocatedIncident,
  b: LocatedIncident
): string | null {
  const areaA = normLoc(a.area_name)
  const sameArea = areaA !== '' && areaA === normLoc(b.area_name)
  const area = (a.area_name ?? '').trim()

  // Most specific: both rows name the same block on the same street. Worth
  // printing even when the area is the generic "Yishun" — the block carries it.
  const blkA = normLoc(a.block_number)
  if (blkA !== '' && blkA === normLoc(b.block_number) && sameArea) {
    const blk = (a.block_number ?? '').trim()
    return `${/^\d/.test(blk) ? `Block ${blk}` : blk}, ${area}`
  }

  // Street/subzone level, e.g. "Yishun Ring Road".
  if (sameArea && !GENERIC_AREAS.has(areaA)) return area

  return null
}

// ── Article date recovered from a source URL ────────────────────────────────
//
// A display-side FALLBACK for source links that carry no `source_timeline`
// entry. Many SG publishers stamp the publication date into the path
// (malaymail /2018/07/13/, zaobao storyYYYYMMDD-), and reading it back is free
// and exact. Publishers that don't (ST, CNA, Yahoo) return null and the link
// renders undated rather than with a guessed date — `tools/backfill_source_dates.py`
// is what actually resolves those, by fetching the article.

const _URL_YMD_PATH = /\/(\d{4})\/(\d{1,2})\/(\d{1,2})(?:\/|$)/
const _URL_YMD_DASH = /\/(\d{4})-(\d{2})-(\d{2})(?:\/|-|$)/
const _URL_STORY    = /\bstory(\d{4})(\d{2})(\d{2})\b/

function _isoDate(y: string, m: string, d: string): string | null {
  const yi = +y, mi = +m, di = +d
  if (yi < 1990 || yi > 2100 || mi < 1 || mi > 12 || di < 1 || di > 31) return null
  const iso = `${yi}-${String(mi).padStart(2, '0')}-${String(di).padStart(2, '0')}`
  // Reject calendar-invalid dates (2026-02-31) — Date rolls them over silently.
  const dt = new Date(`${iso}T00:00:00Z`)
  return dt.getUTCFullYear() === yi && dt.getUTCMonth() + 1 === mi && dt.getUTCDate() === di
    ? iso : null
}

export function dateFromUrl(url: string | null | undefined): string | null {
  if (!url) return null
  let path: string
  try {
    path = new URL(url).pathname
  } catch {
    return null   // not a parseable URL — never guess from a raw string
  }
  for (const re of [_URL_YMD_PATH, _URL_YMD_DASH, _URL_STORY]) {
    const m = re.exec(path)
    if (m) {
      const iso = _isoDate(m[1], m[2], m[3])
      if (iso) return iso
    }
  }
  return null
}

// ── Summary paragraphs ──────────────────────────────────────────────────────
//
// Every published summary in the DB (163/163) is a single unbroken block, and
// 35 of them run past 900 characters — a wall of text nobody reads. Stage 2 now
// emits blank-line paragraph breaks, so `\n\n` is honoured first and this
// function is a no-op on new drafts. Existing rows have no breaks at all, so
// they are grouped on sentence boundaries instead. This only ever inserts
// paragraph breaks — no word is added, removed or reordered.

// Trailing abbreviations that end in '.' without ending a sentence.
const _ABBREV = /(?:^|[\s(])(?:mr|mrs|ms|dr|prof|st|jr|sr|sgt|insp|supt|capt|lt|col|no|vs|approx|etc|e\.g|i\.e|a\.m|p\.m)\.$/i

export function splitSentences(text: string): string[] {
  const out: string[] = []
  const re = /[.!?]["'’)\]]?\s+/g
  let start = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const end = m.index + m[0].length
    // "Mr. Tan" / "e.g. the lift" — the period belongs to an abbreviation.
    if (_ABBREV.test(text.slice(start, m.index + 1))) continue
    // A real sentence starts with a capital, a digit or an opening quote.
    if (!/^["'“(\[]?[A-Z0-9]/.test(text.slice(end))) continue
    out.push(text.slice(start, end).trim())
    start = end
  }
  const tail = text.slice(start).trim()
  if (tail) out.push(tail)
  return out.filter(Boolean)
}

/** Soft target paragraph length in characters. */
export const PARAGRAPH_TARGET = 320

export function toParagraphs(
  raw: string | null | undefined,
  target: number = PARAGRAPH_TARGET
): string[] {
  const text = (raw ?? '').replace(/\r\n?/g, '\n').trim()
  if (!text) return []

  // 1. Author-supplied breaks always win — blank lines first, then single ones.
  if (/\n[ \t]*\n/.test(text)) {
    const paras = text.split(/\n[ \t]*\n+/).map(p => p.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean)
    if (paras.length) return paras
  }
  if (text.includes('\n')) {
    const paras = text.split(/\n+/).map(p => p.trim()).filter(Boolean)
    if (paras.length > 1) return paras
  }

  // 2. No breaks at all — group sentences.
  const sentences = splitSentences(text)
  if (sentences.length <= 2) return [text]

  const paras: string[] = []
  let buf: string[] = []
  let len = 0
  for (const s of sentences) {
    buf.push(s)
    len += s.length + 1
    if (len >= target && buf.length >= 2) {
      paras.push(buf.join(' '))
      buf = []
      len = 0
    }
  }
  if (buf.length) {
    const tail = buf.join(' ')
    // Don't strand a short final sentence as its own paragraph.
    if (paras.length > 0 && tail.length < 120) paras[paras.length - 1] += ' ' + tail
    else paras.push(tail)
  }
  return paras.length ? paras : [text]
}

export function chaosDescriptor(score: number): string {
  if (score < 20) return 'Quiet'
  if (score < 40) return 'Simmering'
  if (score < 60) return 'Elevated'
  if (score < 80) return 'Critical'
  return 'Apocalyptic'
}

/**
 * Raw points at which the curve reaches ~63. Formerly this was the hard 100
 * cap, which is what made the index saturate: a severity-5 dagger scores 15, so
 * just 20 of them pegged a year at Apocalyptic permanently. 2026 hit 87 by July.
 */
export const CHAOS_SCALE = 300

/**
 * Chaos Index, 0-100.
 *
 * Per-incident points are unchanged (dagger x3.0, clown x1.5, heart x-1.0,
 * multiplied by severity) — the same values Stage 2 stores as
 * chaos_contribution. What changed is the curve.
 *
 * Old: `min(100, raw / 300 * 100)` — linear with a hard cliff. Because raw is a
 * cumulative sum over the year, the score only ever climbed and stuck at 100
 * once passed, so it measured "how much have we catalogued" more than "how
 * chaotic was it".
 *
 * New: `100 * (1 - e^(-raw / CHAOS_SCALE))` — diminishing returns that approach
 * 100 asymptotically and never actually reach it. Volume still counts, but each
 * additional incident adds less, so a busy year reads Elevated/Critical instead
 * of pegging. Apocalyptic (>=80) now needs raw ~483, roughly 32 severity-5
 * daggers in one year, rather than 20.
 *
 * Note this index still reflects archive coverage as much as reality: thin
 * historical years read Quiet because few incidents are catalogued, not because
 * Yishun was calm.
 */
export function computeChaosScore(incidents: Array<{ classification: string; severity: number | null }>): number {
  const raw = incidents.reduce((sum, inc) => {
    const weight = inc.classification === 'dagger' ? 3.0
                 : inc.classification === 'clown'  ? 1.5
                 : inc.classification === 'heart'  ? -1.0 : 0
    return sum + (inc.severity ?? 0) * weight   // QA M3: null/undefined severity → 0, not NaN
  }, 0)
  // Hearts can drive raw negative; floor at 0 before the curve.
  const positive = Math.max(0, raw)
  return Math.max(0, Math.min(100, Math.round(100 * (1 - Math.exp(-positive / CHAOS_SCALE)))))
}

// For the rare places that must build raw HTML strings (MapLibre Popup.setHTML).
// DB fields like title/custom_label are LLM-written from scraped text and can
// reach the site without human review via auto-publish — never interpolate them
// into HTML unescaped.
export function escapeHtml(raw: string | null | undefined): string {
  return String(raw ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function sanitiseSlug(raw: string | null | undefined): string {
  return (raw ?? '').replace(/[^a-z0-9-]/g, '').slice(0, 70)
}

export function sanitiseUUID(raw: string | null | undefined): string | null {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(raw ?? '')
    ? raw! : null
}

// Accept any 4-digit year. No hardcoded floor: the year dropdown is populated
// from real incident_date values (incl. manual historical backfills predating
// 1990), so any lower bound here silently drops valid years. A malformed value
// returns null and each caller decides how to handle it (map → default to
// current year; chaos → surface an error; incidents → unfiltered).
export function sanitiseYear(raw: string | null | undefined): number | null {
  return /^\d{4}$/.test(raw ?? '') ? parseInt(raw as string, 10) : null
}

export function sanitisePage(raw: string | null | undefined): number {
  const n = parseInt(raw ?? '0', 10)
  return isNaN(n) || n < 0 ? 0 : Math.min(n, 1000)
}

export function sanitiseClassification(raw: string | null | undefined): 'heart' | 'clown' | 'dagger' | null {
  if (raw === 'heart' || raw === 'clown' || raw === 'dagger') return raw
  return null
}

// ── Canonical source URL ─────────────────────────────────────────────────────
//
// Mirrors classifiers/source_allowlist.py::canonical_url. Two URLs that differ
// only by a tracking parameter are the same article, and the source COUNT is a
// factual claim to the reader — "Corroborated by N sources" plus the lightning
// meter. `yishun-python-escapes-drain-worksite-aug-2026` published as
// "⚡2 sources" holding one Stomp report twice, once with
// `?ref=home-editors-picks` and once without.
//
// The pipeline now collapses these before writing, but rows written earlier
// still carry both spellings, so the count is made robust at render time too.
//
// DENYLIST, not an allowlist: a query string can genuinely identify an article
// (?id=, ?storyid=), and merging two distinct pages is far worse than showing
// one duplicate.
const TRACKING_PARAMS = new Set([
  'ref', 'ref_src', 'ref_url', 'referrer',
  'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
  'fbclid', 'gclid', 'dclid', 'msclkid', 'igshid', 'twclid',
  'mc_cid', 'mc_eid', '_ga', 'cmpid', 'cmp', 'spm',
  'at_medium', 'at_campaign', 'oc',
])

export function canonicalUrl(url: string): string {
  if (!url) return ''
  try {
    const u = new URL(url.trim())
    u.hash = ''
    u.hostname = u.hostname.toLowerCase().replace(/^www\./, '')
    u.protocol = u.protocol.toLowerCase()
    for (const key of [...u.searchParams.keys()]) {
      if (TRACKING_PARAMS.has(key.toLowerCase())) u.searchParams.delete(key)
    }
    u.pathname = u.pathname.replace(/\/+$/, '') || '/'
    return u.toString()
  } catch {
    // Unparseable — treat as distinct rather than risk merging two real sources.
    return url.trim()
  }
}

/** Unique source URLs, first spelling of each article kept, order preserved. */
export function uniqueSources(urls: readonly (string | null | undefined)[] | null | undefined): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const u of urls ?? []) {
    if (!u) continue
    const key = canonicalUrl(u)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(u)
  }
  return out
}

// ── Foreign-linked source note ───────────────────────────────────────────────
//
// Yishun Again is a Singapore archive. Some incidents are corroborated by
// Malaysian outlets — Malay Mail and friends cover SG crime and court news, and
// they are legitimate citations, but a reader should see at a glance that the
// source sits outside the local press. The label the incident page shows is the
// bare domain, so this returns a short parenthetical to render beside it.
//
// Keyed on the registrable domain, suffix-aware, so a subdomain (e.g.
// www.malaymail.com, malaysia.news.yahoo.com) is covered without listing each.
// Operator direction 2026-08 named Malay Mail specifically; the other
// clearly-Malaysian outlets already in the sources table are included so the
// annotation is consistent rather than singling one out.
const FOREIGN_LINKED_DOMAINS = [
  'malaymail.com',
  'thestar.com.my',
  'malaysia.news.yahoo.com',
  'nst.com.my',            // New Straits Times (MY)
  'thesundaily.my',
]

export function foreignSourceNote(url: string): string | null {
  let host = ''
  try { host = new URL(url).hostname.toLowerCase().replace(/^www\./, '') } catch { return null }
  const isForeign = FOREIGN_LINKED_DOMAINS.some(d => host === d || host.endsWith('.' + d))
  return isForeign ? '(foreign-linked news source)' : null
}
