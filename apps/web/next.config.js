// apps/web/next.config.js — spec §10b.2

const isDev = process.env.NODE_ENV === 'development'

const securityHeaders = [
  { key: 'X-DNS-Prefetch-Control',  value: 'on' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
  { key: 'X-Frame-Options',         value: 'DENY' },
  { key: 'X-Content-Type-Options',  value: 'nosniff' },
  { key: 'Referrer-Policy',         value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy',      value: 'camera=(), microphone=(), geolocation=()' },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      // 'unsafe-inline' is required in BOTH dev and prod: Next.js App Router emits
      // inline <script> tags (the self.__next_f.push RSC hydration payload). Without
      // it, real browsers block those inline scripts, React never hydrates, and all
      // client components — including the dynamically-imported map — silently fail to
      // mount (symptom: map stuck on "Loading map…"). The secure alternative is a
      // per-request CSP nonce via middleware, but that forces dynamic rendering and
      // would break this project's SSG + ISR (revalidate) model. 'unsafe-eval' is for
      // MapLibre GL.
      "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  // MapLibre + Google Fonts
      "font-src 'self' https://fonts.gstatic.com",
      "img-src 'self' data: blob: https://tiles.openfreemap.org https://assets.yishunagain.com",
      // No supabase.co allowance: the browser never talks to Supabase directly
      // (all reads proxied via /api/*), so listing it only widened the
      // exfiltration surface for any injected script.
      isDev
        ? "connect-src 'self' https://tiles.openfreemap.org https://assets.yishunagain.com ws://localhost:3000 wss://localhost:3000"
        : "connect-src 'self' https://tiles.openfreemap.org https://assets.yishunagain.com",
      "worker-src blob:",
      "media-src 'self' https://videodelivery.net",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join('; '),
  },
]

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  transpilePackages: ['maplibre-gl'],  // ESM package — must transpile for Next.js
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }]
  },
}

module.exports = nextConfig
