/**
 * A short, single-line teaser for the map hover/tap preview.
 *
 * Truncated SERVER-SIDE so the CDN-cached /api/map GeoJSON stays small — a full
 * summary per pinned incident would bloat it. Whitespace is collapsed (the
 * writer stores summaries with `\n\n` paragraph breaks) and the cut backs off to
 * a word boundary so it never ends mid-word. Dependency-free so lib/teaser.test.ts
 * runs under raw Node.
 */
const MAX = 120

export function mapTeaser(summary: string | null | undefined): string {
  if (!summary) return ''
  const clean = summary.replace(/\s+/g, ' ').trim()
  if (clean.length <= MAX) return clean
  const cut       = clean.slice(0, MAX)
  const lastSpace = cut.lastIndexOf(' ')
  // Back off to the last space, unless that would throw away most of the text
  // (a very long unbroken token) — then hard-cut.
  return (lastSpace > 60 ? cut.slice(0, lastSpace) : cut).trimEnd() + '…'
}
