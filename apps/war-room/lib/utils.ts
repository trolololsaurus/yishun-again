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

// ── Source counting ──────────────────────────────────────────────────────────
//
// PORTED from apps/web/lib/utils.ts, same reason as the paragraph block below:
// the War Room must show the operator the number readers will see. The public
// incident page derives its ⚡ meter and its "Corroborated by N sources" line
// from `uniqueSources(source_urls).length`, NOT from `corroboration_count` —
// so a War Room reading the stored column shows a different bolt count on any
// row where the two have drifted. Guard: lib/utils.paragraphs.test.ts.

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

/** Bolts shown for a story with `sourceCount` citations: 2 sources → ⚡. */
export function hypeFromSources(sourceCount: number | null | undefined): number {
  return Math.max(0, (sourceCount ?? 1) - 1)
}

// ── Finding one incident from something the operator pasted ──────────────────

/** Only a real absolute URL, or a bare host with a dot-TLD. Without the second
 *  test `new URL('https://' + input)` happily parses a bare slug as a HOSTNAME,
 *  and "yishun-cat-abuse-feb-2017" would be treated as a website. */
const _LOOKS_LIKE_URL = /^(https?:\/\/|[\w-]+(\.[\w-]+)+(\/|$))/i

/** Slug charset used everywhere a slug reaches Supabase. */
export function sanitiseSlug(raw: string): string {
  return raw.trim().toLowerCase().replace(/[^a-z0-9-]/g, '')
}

export interface IncidentRef {
  /** An incidents.slug, from `/incidents/<slug>` or a bare slug. */
  slug:      string | null
  /** Some other URL — treated as one of the incident's own citations. */
  sourceUrl: string | null
}

/**
 * Resolve whatever the operator pasted into something we can look up.
 *
 * Three things get pasted at a rectify queue, and guessing wrong sends the
 * operator hunting for a row that is right there:
 *   • the public incident page   https://www.yishunagain.com/incidents/<slug>
 *   • the War Room preview       https://warroom.yishunagain.com/incidents/<slug>
 *   • the SOURCE article         https://www.straitstimes.com/singapore/…
 * The first two are the same lookup; the third matches against `source_urls`.
 * A bare slug is accepted too.
 */
export function incidentRefFromInput(raw: string | null | undefined): IncidentRef {
  const text = (raw ?? '').trim()
  if (!text) return { slug: null, sourceUrl: null }

  if (!_LOOKS_LIKE_URL.test(text)) {
    const slug = sanitiseSlug(text)
    return { slug: slug || null, sourceUrl: null }
  }

  let parsed: URL
  try {
    parsed = new URL(/^https?:\/\//i.test(text) ? text : `https://${text}`)
  } catch {
    const slug = sanitiseSlug(text)
    return { slug: slug || null, sourceUrl: null }
  }

  // `/incidents/<slug>` on ANY host — the public site, the War Room and
  // localhost all serve it at that path, and which one the operator copied
  // from is not information worth being strict about.
  const m = parsed.pathname.match(/\/incidents\/([^/]+)\/?$/)
  if (m) {
    const slug = sanitiseSlug(decodeURIComponent(m[1]))
    if (slug) return { slug, sourceUrl: null }
  }

  return { slug: null, sourceUrl: parsed.toString() }
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

// ── Applying and reverting an update (merge) ─────────────────────────────────
//
// A confirmed update mutates an ALREADY-PUBLISHED incident: it appends the new
// source URL + a timeline entry, flips is_developing, bumps update_count and
// recomputes the dates. `applyUpdate` is the single source of that math — the
// confirm-update route calls it, and (PR #2) the autonomous auto-merge mirrors
// it in Python. `revertUpdate` is its inverse, and it is deliberately a plain
// RESTORE of the pre-merge snapshot rather than a surgical un-append: the route
// does NOT re-add a source_url it already held, so reconstructing the reversal
// by removing the URL would delete a citation the incident had before the merge.
// Snapshotting the prior arrays wholesale is both simpler and correct.
//
// Guard: apps/war-room/lib/utils.updateMerge.test.ts (revert(apply(x)) == x).

export interface IncidentMergeState {
  source_urls:       string[] | null
  source_timeline:   unknown[] | null
  update_count:      number | null
  incident_date:     string | null
  first_reported_at: string | null
  is_developing:     boolean | null
  summary:           string | null
}

export interface MergeInput {
  newSourceUrl: string
  sourceName:   string
  headline:     string
  /** The candidate's REAL article date — never "today". null if unknown. */
  newDate:      string | null
  /** Operator-edited summary. Empty/undefined keeps the existing summary. */
  updatedSummary?: string
}

/** Everything needed to restore the incident to its exact pre-merge state. */
export interface UpdateSnapshot {
  source_urls:       string[]
  source_timeline:   unknown[]
  update_count:      number
  incident_date:     string | null
  first_reported_at: string | null
  is_developing:     boolean
  summary:           string | null
}

export function applyUpdate(
  existing: IncidentMergeState,
  input: MergeInput,
): { updates: Record<string, unknown>; snapshot: UpdateSnapshot } {
  const existingUrls: string[] = existing.source_urls ?? []
  const existingTimeline: unknown[] = Array.isArray(existing.source_timeline)
    ? existing.source_timeline
    : []
  const existingDate = existing.incident_date ?? null
  const existingCount = existing.update_count ?? 0
  const existingDeveloping = existing.is_developing ?? false

  // Snapshot BEFORE mutating — the arrays are captured by reference to the old
  // values, so the patch below (which builds new arrays) never mutates them.
  const snapshot: UpdateSnapshot = {
    source_urls:       existingUrls,
    source_timeline:   existingTimeline,
    update_count:      existingCount,
    incident_date:     existingDate,
    first_reported_at: existing.first_reported_at ?? null,
    is_developing:     existingDeveloping,
    summary:           existing.summary ?? null,
  }

  // Merge source_urls — never store the same citation twice.
  const mergedUrls = existingUrls.includes(input.newSourceUrl)
    ? existingUrls
    : [...existingUrls, input.newSourceUrl]

  // Date of the merged source — never "today". Fall back to the incident's own
  // date so a merge can never push incident_date into the future.
  const newDate = input.newDate || existingDate || existing.first_reported_at || null

  const mergedTimeline = [
    ...existingTimeline,
    { date: newDate ?? existingDate, source_url: input.newSourceUrl, source_name: input.sourceName, headline: input.headline },
  ]

  // incident_date = latest known date, first_reported_at = earliest known date.
  const updatedDate = existingDate && newDate && existingDate > newDate ? existingDate : (newDate ?? existingDate)
  const existingFirstDate = existing.first_reported_at ?? existingDate
  const firstReportedAt = existingFirstDate && newDate && existingFirstDate < newDate
    ? existingFirstDate
    : (existingFirstDate ?? newDate)

  const updates: Record<string, unknown> = {
    source_urls:       mergedUrls,
    source_timeline:   mergedTimeline,
    is_developing:     true,
    update_count:      existingCount + 1,
    incident_date:     updatedDate,
    first_reported_at: firstReportedAt,
  }
  const updatedSummary = (input.updatedSummary ?? '').trim()
  if (updatedSummary) updates.summary = updatedSummary

  return { updates, snapshot }
}

/** The patch that restores an incident to its pre-merge snapshot. */
export function revertUpdate(snap: UpdateSnapshot): Record<string, unknown> {
  return {
    source_urls:       snap.source_urls,
    source_timeline:   snap.source_timeline,
    update_count:      snap.update_count,
    incident_date:     snap.incident_date,
    first_reported_at: snap.first_reported_at,
    is_developing:     snap.is_developing,
    summary:           snap.summary,
  }
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
