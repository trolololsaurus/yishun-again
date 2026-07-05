// Canonical site origin -- www is the canonical host (Vercel serves the site
// there; the apex redirects). Every absolute URL in metadata, JSON-LD, the
// sitemap and robots.txt must be built from this constant.

const CANONICAL = 'https://www.yishunagain.com'

// Env vars pasted into the Vercel dashboard can carry invisible characters --
// a leading BOM (U+FEFF) here once broke every build at `new URL(SITE_URL)`
// in layout.tsx. Strip BOM/zero-width chars + whitespace, then validate; a
// malformed value falls back to the canonical host rather than killing the
// build.
const raw = (process.env.NEXT_PUBLIC_SITE_URL ?? CANONICAL)
  .replace(/[\s\uFEFF\u200B-\u200D]+/g, '')

function safeOrigin(candidate: string): string {
  try {
    return new URL(candidate).toString().replace(/\/$/, '')
  } catch {
    return CANONICAL
  }
}

export const SITE_URL = safeOrigin(raw)
