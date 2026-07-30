// Simple in-memory rate limiter — spec §10b.3
// 60 req/min per IP for Phase 1.
// Note: per-instance state — each serverless instance has its own window, so
// the effective global limit is N-instances × limit. Resets on cold start.
// Switch to Upstash (or another shared store) for a real multi-instance limit.

const map = new Map<string, number[]>()

// Bound the map so a client rotating spoofed IP headers can't grow memory
// forever on a long-lived instance. When the cap is hit, stale entries are
// swept; if every entry is live the map is cleared (brief window reset is
// preferable to unbounded growth).
const MAX_TRACKED_IPS = 10_000

export function rateLimit(ip: string, limit = 60, windowMs = 60_000): { success: boolean } {
  const now     = Date.now()
  const cutoff  = now - windowMs
  const history = (map.get(ip) ?? []).filter(t => t > cutoff)

  if (history.length >= limit) return { success: false }

  if (!map.has(ip) && map.size >= MAX_TRACKED_IPS) {
    for (const [key, times] of map) {
      if (!times.some(t => t > cutoff)) map.delete(key)
    }
    if (map.size >= MAX_TRACKED_IPS) map.clear()
  }

  history.push(now)
  map.set(ip, history)
  return { success: true }
}

export function getIp(req: Request): string {
  // Trust order matters: x-real-ip is set by Vercel itself and cannot be
  // spoofed by the client, so it comes first. cf-connecting-ip is only
  // trustworthy when Cloudflare actually fronts the deployment — a client
  // hitting the origin directly can send it freely, which previously allowed
  // a fresh rate-limit bucket per request.
  return (
    req.headers.get('x-real-ip') ??
    req.headers.get('cf-connecting-ip') ??
    req.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ??
    'unknown'
  )
}
