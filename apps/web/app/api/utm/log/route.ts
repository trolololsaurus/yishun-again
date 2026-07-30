import { NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseUUID } from '@/lib/utils'

const VALID_SOURCES   = new Set(['telegram','reddit','hwz','whatsapp','organic','direct','share'])
const VALID_MEDIUMS   = new Set(['share_card','link','organic'])
const VALID_CAMPAIGNS = new Set(['heart','clown','dagger','milestone','unknown'])

export async function POST(req: Request) {
  const { success } = rateLimit(getIp(req), 30)  // tighter limit for event logging
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  let body: Record<string, unknown>
  try { body = await req.json() } catch { return NextResponse.json({ error: 'Bad JSON' }, { status: 400 }) }

  const incident_id  = sanitiseUUID(body.incident_id as string)
  const utm_source   = VALID_SOURCES.has(body.utm_source as string)   ? (body.utm_source as string)   : 'unknown'
  const utm_medium   = VALID_MEDIUMS.has(body.utm_medium as string)   ? (body.utm_medium as string)   : 'link'
  const utm_campaign = VALID_CAMPAIGNS.has(body.utm_campaign as string) ? (body.utm_campaign as string) : 'unknown'
  // Privacy: keep only origin + path of the referrer. Query strings from
  // referring sites can carry per-user identifiers (session ids, click ids)
  // which utm_events must never store.
  let referrer: string | null = null
  if (typeof body.referrer === 'string') {
    try {
      const u = new URL(body.referrer)
      referrer = `${u.origin}${u.pathname}`.slice(0, 200)
    } catch { referrer = null }
  }

  // Cloudflare geo — no IP stored per spec §8.1. Length-capped: these are
  // client-spoofable when the origin isn't fronted by Cloudflare.
  const geo_country  = req.headers.get('cf-ipcountry')?.slice(0, 8)  ?? null
  const geo_city     = req.headers.get('cf-ipcity')?.slice(0, 64)    ?? null

  // Hashed user-agent SHA256[:16] — no PII
  const ua = req.headers.get('user-agent') ?? ''
  const uaHash = ua
    ? Buffer.from(
        await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ua))
      ).toString('hex').slice(0, 16)
    : null

  const { error } = await supabase.from('utm_events').insert({
    incident_id,
    utm_source, utm_medium, utm_campaign,
    referrer, geo_country, geo_city,
    user_agent_hash: uaHash,
  })

  if (error) {
    // Log the detail server-side; PostgREST messages leak schema internals
    // (e.g. an FK violation echoes table/constraint names to a probed caller).
    console.error('utm_events insert error:', error.message, error.code)
    return NextResponse.json({ error: 'Insert failed' }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
