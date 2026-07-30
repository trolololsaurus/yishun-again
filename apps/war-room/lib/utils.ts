import type { Classification } from './types'

export const CLASS_ICON: Record<string, string> = {
  dagger: '🗡️',
  clown:  '🤡',
  heart:  '❤️',
  custom: '📌',
}

export const CLASS_COLOR: Record<string, string> = {
  dagger: 'text-purple',
  clown:  'text-yellow',
  heart:  'text-red',
  custom: 'text-text-secondary',
}

export const CLASS_LABEL: Record<string, string> = {
  dagger: 'DAGGER',
  clown:  'CLOWN',
  heart:  'HEART',
  custom: 'CUSTOM',
}

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

export function confidenceColor(conf: number | null): string {
  if (conf === null) return 'bg-border text-text-secondary'
  if (conf >= 0.85)  return 'bg-green text-bg'
  if (conf >= 0.5)   return 'bg-yellow text-bg'
  return 'bg-red text-bg'
}

export function confidenceLabel(conf: number | null): string {
  if (conf === null) return '?'
  return (conf * 100).toFixed(0) + '%'
}

export function severityDiamonds(sev: number | null): string {
  if (!sev) return ''
  return '◆'.repeat(sev) + '◇'.repeat(Math.max(0, 5 - sev))
}

export function hypeMeter(hype: number): string {
  if (!hype) return ''
  return '⚡'.repeat(hype)
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 70)
}

export function validateUUID(id: string | undefined): string | null {
  if (!id) return null
  // QA L1: case-insensitive — uppercase/mixed-case UUIDs are valid per RFC 4122.
  const match = id.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
  return match ? id : null
}

export function today(): string {
  return new Date().toISOString().split('T')[0]
}

/**
 * ISO timestamp for `days` days ago — the lower bound of a "last N days" window.
 *
 * Callers are async Server Components with `revalidate = 0`: they re-render per
 * request, and reading the clock is the whole point. `react-hooks/purity` flags
 * a bare `Date.now()` in a component body because a clock read makes a *client*
 * render impure (two renders of identical props disagree) — it has no way to
 * tell a Server Component from a client one, so it fired on all three call
 * sites. The rule only analyses component and hook bodies, so hoisting the
 * clock read into this plain function is both what quiets it and what removes
 * three copies of the same day-to-milliseconds arithmetic.
 *
 * If you ever need `Date.now()` directly inside a Server Component body, that
 * is legitimate — suppress the rule at the call site rather than making the
 * value stale.
 */
export function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
}
