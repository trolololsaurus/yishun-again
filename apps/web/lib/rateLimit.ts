// Simple in-memory rate limiter — spec §10b.3
// 60 req/min per IP for Phase 1.
// Note: resets on process restart; switch to Upstash for multi-instance deploys.

const map = new Map<string, number[]>()

export function rateLimit(ip: string, limit = 60, windowMs = 60_000): { success: boolean } {
  const now     = Date.now()
  const cutoff  = now - windowMs
  const history = (map.get(ip) ?? []).filter(t => t > cutoff)

  if (history.length >= limit) return { success: false }

  history.push(now)
  map.set(ip, history)
  return { success: true }
}

export function getIp(req: Request): string {
  return (
    req.headers.get('cf-connecting-ip') ??
    req.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ??
    'unknown'
  )
}
