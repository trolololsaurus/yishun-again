import { NextRequest, NextResponse } from 'next/server'
import { createRemoteJWKSet, jwtVerify } from 'jose'

/**
 * Cloudflare Access middleware for the War Room.
 *
 * In production every request must carry a VALID `Cf-Access-Jwt-Assertion`
 * JWT (falling back to the `CF_Authorization` cookie), verified against the
 * team's public signing keys with the Access application's AUD tag. The old
 * check — presence of the `cf-access-authenticated-user-email` header — was
 * spoofable by anyone who could reach the origin directly (e.g. a leaked
 * *.vercel.app URL): plain request headers prove nothing.
 *
 * Required env (production):
 *   CF_ACCESS_TEAM_DOMAIN  e.g. "myteam.cloudflareaccess.com"
 *   CF_ACCESS_AUD          the Access application's Application Audience tag
 *
 * Fails CLOSED: if these are unset in production, every request is rejected
 * with a 503 naming the missing config, rather than silently degrading to a
 * spoofable check.
 *
 * Identity logins carry an `email` claim (checked against OPERATOR_EMAIL when
 * set). Service tokens (agents backend → War Room) carry `common_name`
 * instead and are allowed through — they already proved possession of the
 * client secret to Cloudflare.
 *
 * In development the check is skipped entirely.
 *
 * Next.js 16 renamed the `middleware` convention to `proxy`, which always runs
 * on the Node.js runtime — the edge runtime is unsupported here and cannot be
 * configured. That suits this gate better than edge did: JWT signature
 * verification wants Node's full crypto, not just WebCrypto.
 */

// JWKS is fetched from the team domain and cached by jose between requests
// on a warm instance.
let jwks: ReturnType<typeof createRemoteJWKSet> | null = null
let jwksDomain = ''

function getJwks(teamDomain: string) {
  if (!jwks || jwksDomain !== teamDomain) {
    jwks = createRemoteJWKSet(new URL(`https://${teamDomain}/cdn-cgi/access/certs`))
    jwksDomain = teamDomain
  }
  return jwks
}

// Minimal fixed-window rate limit for /api/* (spec: 60 req/min per IP on all
// API routes). Per-instance, like the public site's limiter — a shared-store
// limiter is the eventual upgrade for both.
const rlMap = new Map<string, number[]>()
const RL_LIMIT = 60
const RL_WINDOW_MS = 60_000
const RL_MAX_KEYS = 5_000

function rateLimited(ip: string): boolean {
  const now = Date.now()
  const cutoff = now - RL_WINDOW_MS
  const history = (rlMap.get(ip) ?? []).filter(t => t > cutoff)
  if (history.length >= RL_LIMIT) return true
  if (!rlMap.has(ip) && rlMap.size >= RL_MAX_KEYS) {
    for (const [k, times] of rlMap) {
      if (!times.some(t => t > cutoff)) rlMap.delete(k)
    }
    if (rlMap.size >= RL_MAX_KEYS) rlMap.clear()
  }
  history.push(now)
  rlMap.set(ip, history)
  return false
}

function deny(status: number, message: string): NextResponse {
  return new NextResponse(message, {
    status,
    headers: { 'Content-Type': 'text/plain' },
  })
}

export async function proxy(req: NextRequest): Promise<NextResponse> {
  // Development: skip auth entirely (local-only convenience).
  if (process.env.NODE_ENV !== 'production') {
    return NextResponse.next()
  }

  // CSRF defence-in-depth for state-changing API calls: a browser request
  // from a foreign origin carries that origin here. Server-to-server calls
  // (no Origin header) pass.
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    const origin = req.headers.get('origin')
    if (origin && origin !== req.nextUrl.origin) {
      return deny(403, 'Cross-origin request rejected')
    }
  }

  if (req.nextUrl.pathname.startsWith('/api/')) {
    const ip = req.headers.get('x-real-ip')
      ?? req.headers.get('cf-connecting-ip')
      ?? 'unknown'
    if (rateLimited(ip)) return deny(429, 'Too many requests')
  }

  const teamDomain = (process.env.CF_ACCESS_TEAM_DOMAIN ?? '')
    .replace(/^https?:\/\//, '').replace(/\/+$/, '')
  const aud = process.env.CF_ACCESS_AUD ?? ''

  if (!teamDomain || !aud) {
    // Fail closed — a misconfigured deploy must not become an open CMS.
    return deny(503, 'War Room auth not configured: set CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD')
  }

  const token = req.headers.get('cf-access-jwt-assertion')
    ?? req.cookies.get('CF_Authorization')?.value

  if (!token) {
    return deny(403, 'Access denied')
  }

  let payload: Record<string, unknown>
  try {
    const verified = await jwtVerify(token, getJwks(teamDomain), {
      issuer:   `https://${teamDomain}`,
      audience: aud,
    })
    payload = verified.payload as Record<string, unknown>
  } catch {
    return deny(403, 'Access denied')
  }

  const email = typeof payload.email === 'string' ? payload.email.toLowerCase() : null
  const isServiceToken = !email && typeof payload.common_name === 'string'

  if (!email && !isServiceToken) {
    return deny(403, 'Access denied')
  }

  // Optional single-operator allowlist — applies to identity logins only.
  const allowed = process.env.OPERATOR_EMAIL
  if (email && allowed && email !== allowed.toLowerCase()) {
    return deny(403, 'Access denied')
  }

  return NextResponse.next()
}

export const config = {
  /*
   * Match every route except:
   *   - _next/static  — static asset chunks
   *   - _next/image   — image optimisation API
   *   - favicon.ico   — browser favicon request
   *
   * /api/health is deliberately NOT exempt any more: it returns real
   * operational data (scraper fleet health, queue counts), and the spec says
   * the War Room has no bypass route. Browser calls from the health page
   * carry the CF_Authorization cookie, so they pass the JWT check.
   */
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
