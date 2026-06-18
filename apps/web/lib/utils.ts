import type { Classification } from './types'

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
export function classIcon(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return '🌐'
  return CLASS_ICON[cls] ?? ''
}

export function classLabel(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return 'YISHUN ON THE MAP'
  return CLASS_LABEL[cls] ?? cls.toUpperCase()
}

export function classColor(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return 'text-culture'
  return CLASS_COLOR[cls] ?? ''
}

export function classTooltip(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return CULTURE_TOOLTIP
  return CLASS_TOOLTIP[cls] ?? ''
}

export function pinColor(cls: string, customLabel?: string | null): string {
  if (cls === 'custom' && customLabel === 'CULTURE') return PIN_COLOR.culture
  return PIN_COLOR[cls] ?? '#7A8BAA'
}

export const HYPE_TOOLTIP = 'Hype meter — number of mainstream media sources reporting this'

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

export function chaosDescriptor(score: number): string {
  if (score < 20) return 'Quiet'
  if (score < 40) return 'Simmering'
  if (score < 60) return 'Elevated'
  if (score < 80) return 'Critical'
  return 'Apocalyptic'
}

export function computeChaosScore(incidents: Array<{ classification: string; severity: number }>): number {
  const raw = incidents.reduce((sum, inc) => {
    const weight = inc.classification === 'dagger' ? 3.0
                 : inc.classification === 'clown'  ? 1.5
                 : inc.classification === 'heart'  ? -1.0 : 0
    return sum + inc.severity * weight
  }, 0)
  return Math.min(100, Math.max(0, Math.round((raw / 300) * 100)))
}

export function sanitiseSlug(raw: string | null | undefined): string {
  return (raw ?? '').replace(/[^a-z0-9-]/g, '').slice(0, 70)
}

export function sanitiseUUID(raw: string | null | undefined): string | null {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(raw ?? '')
    ? raw! : null
}

export function sanitiseYear(raw: string | null | undefined): number | null {
  const n = parseInt(raw ?? '', 10)
  return n >= 1990 && n <= 2100 ? n : null
}

export function sanitisePage(raw: string | null | undefined): number {
  const n = parseInt(raw ?? '0', 10)
  return isNaN(n) || n < 0 ? 0 : Math.min(n, 1000)
}

export function sanitiseClassification(raw: string | null | undefined): 'heart' | 'clown' | 'dagger' | null {
  if (raw === 'heart' || raw === 'clown' || raw === 'dagger') return raw
  return null
}
