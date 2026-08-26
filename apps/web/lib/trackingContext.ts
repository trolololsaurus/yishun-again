// Shared by /api/utm/log and /api/track/pageview — both need the same
// privacy-scrubbed referrer + Cloudflare geo + hashed user-agent, and used to
// duplicate this line for line.
export async function extractTrackingContext(req: Request, rawReferrer: unknown) {
  // Privacy: keep only origin + path of the referrer. Query strings from
  // referring sites can carry per-user identifiers (session ids, click ids)
  // which must never land in our DB.
  let referrer: string | null = null
  if (typeof rawReferrer === 'string') {
    try {
      const u = new URL(rawReferrer)
      referrer = `${u.origin}${u.pathname}`.slice(0, 200)
    } catch { referrer = null }
  }

  // Cloudflare geo — no IP stored per spec §8.1. Length-capped: these are
  // client-spoofable when the origin isn't fronted by Cloudflare.
  const geo_country = req.headers.get('cf-ipcountry')?.slice(0, 8) ?? null
  const geo_city     = req.headers.get('cf-ipcity')?.slice(0, 64)  ?? null

  // Hashed user-agent SHA256[:16] — no PII
  const ua = req.headers.get('user-agent') ?? ''
  const user_agent_hash = ua
    ? Buffer.from(
        await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ua))
      ).toString('hex').slice(0, 16)
    : null

  return { referrer, geo_country, geo_city, user_agent_hash }
}
