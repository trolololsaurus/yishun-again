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
