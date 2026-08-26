import { NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseUUID } from '@/lib/utils'
import { extractTrackingContext } from '@/lib/trackingContext'

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

  const { referrer, geo_country, geo_city, user_agent_hash } = await extractTrackingContext(req, body.referrer)

  const { error } = await supabase.from('utm_events').insert({
    incident_id,
    utm_source, utm_medium, utm_campaign,
    referrer, geo_country, geo_city,
    user_agent_hash,
  })

  if (error) {
    // Log the detail server-side; PostgREST messages leak schema internals
    // (e.g. an FK violation echoes table/constraint names to a probed caller).
    console.error('utm_events insert error:', error.message, error.code)
    return NextResponse.json({ error: 'Insert failed' }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
