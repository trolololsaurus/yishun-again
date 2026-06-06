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
  const match = id.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  return match ? id : null
}

export function today(): string {
  return new Date().toISOString().split('T')[0]
}
