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
  // Incident art lives on R2 behind assets.yishunagain.com (already allowed by
  // the CSP img-src). next/image serves it through the same-origin /_next/image
  // optimizer, so the feed lazy-loads thumbnails instead of shipping full
  // 1200×630 files. This is the ONLY remote image host.
  images: {
    // The optimizer defaults to `Content-Disposition: attachment` for remote
    // images, which makes "open image in new tab" DOWNLOAD the file instead of
    // previewing it. These are public incident art, not downloads — serve inline.
    contentDispositionType: 'inline',
    remotePatterns: [
      { protocol: 'https', hostname: 'assets.yishunagain.com' },
    ],
  },
  // Turbopack (the default bundler as of Next 16) walks up looking for a
  // workspace root and picks the directory of the nearest lockfile. This app
  // is self-contained with its own package-lock.json, but the repo root has
  // one too, so inference reached outside the app and warned. Pin it.
  turbopack: { root: __dirname },
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }]
  },
  // Slugs of incidents that were merged into another row. The absorbed row is
  // unpublished, so its page 404s — but it was live, shared and indexed, and a
  // 404 loses that. Point it at the surviving incident instead.
  // Permanent: the merge is an editorial judgement that these were always one
  // event, not a temporary move.
  // Add a line here in the same change that merges the rows.
  async redirects() {
    return [
      {
        source: '/incidents/yishun-group-knife-attack-block-243-carpark-jul-2026',
        destination: '/incidents/yishun-ring-road-rioting-carpark-brawl-jul-2026',
        permanent: true,
      },
      {
        source: '/incidents/yishun-bus-staff-assault-fire-extinguisher-interchange',
        destination: '/incidents/tower-transit-staff-restrain-fire-extinguisher-man-yishun-jan-2026',
        permanent: true,
      },
      {
        source: '/incidents/yishun-triple-murder-death-penalty-appeal-dismissed-nov-2014',
        destination: '/incidents/yishun-triple-murder-wang-zhijian-block-349-2008',
        permanent: true,
      },
      {
        source: '/incidents/acsi-student-death-high-element-safra-yishun-feb-2021',
        destination: '/incidents/safra-yishun-student-death-jethro-puah-2021',
        permanent: true,
      },
    ]
  },
}

module.exports = nextConfig
