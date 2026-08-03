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

// ── Paragraph rendering ──────────────────────────────────────────────────────
//
// PORTED VERBATIM from apps/web/lib/utils.ts. Keep the two byte-identical.
//
// Why the duplication: there is no packages/shared wired into either app, and
// the War Room MUST paragraph a summary exactly the way the public site will.
// Before this, the War Room rendered incidents.summary raw with
// `whitespace-pre-wrap` while the public page ran it through toParagraphs() —
// so an operator editing a summary reviewed a wall of text and shipped
// something that looked different in production. Review surfaces that disagree
// with the thing they gate are worse than no preview.
//
// Guard: apps/war-room/lib/utils.paragraphs.test.ts asserts this
// implementation stays identical to the web copy. If you change one, change
// both — the test fails otherwise.

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
