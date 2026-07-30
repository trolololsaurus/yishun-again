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

// Scraped/pipeline URLs are rendered into operator-clicked <a href>. React
// escapes text but not URL schemes — a javascript: URL from a hostile RSS
// entry would execute in the War Room origin on click (and the CSP does not
// block javascript: navigation). Only http(s) survives; anything else
// renders as a dead link.
export function safeHref(url: string | null | undefined): string {
  if (!url) return '#'
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return url
  } catch { /* not a parseable absolute URL */ }
  return '#'
}
