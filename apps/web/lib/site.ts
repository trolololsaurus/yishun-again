// Canonical site origin — www is the canonical host (Vercel serves the site
// there; the apex redirects). Every absolute URL in metadata, JSON-LD, the
// sitemap and robots.txt must be built from this constant.
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://www.yishunagain.com'
