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
      isDev
        ? "script-src 'self' 'unsafe-eval' 'unsafe-inline'"             // Next.js HMR inline scripts
        : "script-src 'self' 'unsafe-eval'",                            // MapLibre WebGL
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  // MapLibre + Google Fonts
      "font-src 'self' https://fonts.gstatic.com",
      "img-src 'self' data: blob: https://tiles.openfreemap.org https://tiles.stadiamaps.com https://*.stadiamaps.com https://basemaps.cartocdn.com https://*.basemaps.cartocdn.com https://assets.yishunagain.com",
      isDev
        ? "connect-src 'self' https://*.supabase.co https://tiles.openfreemap.org https://tiles.stadiamaps.com https://*.stadiamaps.com https://basemaps.cartocdn.com https://*.basemaps.cartocdn.com https://assets.yishunagain.com ws://localhost:3000 wss://localhost:3000"
        : "connect-src 'self' https://*.supabase.co https://tiles.openfreemap.org https://tiles.stadiamaps.com https://*.stadiamaps.com https://basemaps.cartocdn.com https://*.basemaps.cartocdn.com https://assets.yishunagain.com",
      "worker-src blob:",
      "media-src 'self' https://videodelivery.net",
      "frame-ancestors 'none'",
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
