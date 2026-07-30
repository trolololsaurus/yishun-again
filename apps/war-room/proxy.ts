import { NextRequest, NextResponse } from 'next/server'

/**
 * Cloudflare Access gate for the War Room.
 *
 * In production:  every request must carry the CF Access header
 *                 `cf-access-authenticated-user-email`.  If OPERATOR_EMAIL
 *                 is set, the value must also match exactly.
 *
 * In development: check is skipped entirely — all requests pass through.
 *
 * Exempt route:   /api/health (health-check probe, no auth needed)
 *
 * Next.js 16 renamed the `middleware` convention to `proxy`, which always runs
 * on the Node.js runtime — the edge runtime is unsupported here and cannot be
 * configured.  That suits this gate (it only reads a request header), and it is
 * the better host for JWT signature verification than edge was, since Node has
 * both WebCrypto and the full crypto module.
 */

const CF_ACCESS_HEADER = 'cf-access-authenticated-user-email'

export function proxy(req: NextRequest): NextResponse {
  // Always allow the health-check probe through.
  if (req.nextUrl.pathname === '/api/health') {
    return NextResponse.next()
  }

  // Development: skip the check entirely.
  if (process.env.NODE_ENV !== 'production') {
    return NextResponse.next()
  }

  const userEmail = req.headers.get(CF_ACCESS_HEADER)

  // Header must be present — injected by Cloudflare Access on every
  // authenticated request.  Absence means the request bypassed the tunnel.
  if (!userEmail) {
    return new NextResponse('Access denied', {
      status: 403,
      headers: { 'Content-Type': 'text/plain' },
    })
  }

  // Optional single-operator allowlist.  When OPERATOR_EMAIL is set the
  // header value must match it exactly (case-sensitive, Cloudflare
  // normalises email to lowercase before injecting it).
  const allowed = process.env.OPERATOR_EMAIL
  if (allowed && userEmail !== allowed.toLowerCase()) {
    return new NextResponse('Access denied', {
      status: 403,
      headers: { 'Content-Type': 'text/plain' },
    })
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
   * /api/health is handled explicitly above so that its fast-path
   * return runs before any header parsing.
   */
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
