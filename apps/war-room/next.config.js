/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,

  // Turbopack (the default bundler as of Next 16) walks up looking for a
  // workspace root and picks the directory of the nearest lockfile. This app
  // is self-contained with its own package-lock.json, but the repo root has
  // one too, so inference reached outside the app and warned. Pin it.
  turbopack: { root: __dirname },

  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options',           value: 'DENY' },
          { key: 'X-Content-Type-Options',     value: 'nosniff' },
          { key: 'Referrer-Policy',            value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy',         value: 'camera=(), microphone=(), geolocation=()' },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          {
            key: 'Content-Security-Policy',
            // War Room: no external scripts, no MapLibre, no pixel art rendering.
            // fonts.googleapis.com needed for Press Start 2P + Courier Prime.
            value: [
              "default-src 'self'",
              // Next.js inline scripts; dev needs 'unsafe-eval' for webpack HMR/eval source maps
              process.env.NODE_ENV === 'development'
                ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
                : "script-src 'self' 'unsafe-inline'",
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
              "font-src 'self' https://fonts.gstatic.com",
              "img-src 'self' data: https:",
              "connect-src 'self'",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ]
  },
}

module.exports = nextConfig
