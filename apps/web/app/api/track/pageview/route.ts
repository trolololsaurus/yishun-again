import { NextResponse } from 'next/server'
import { supabase }      from '@/lib/supabase'
import { supabaseAdmin } from '@/lib/supabaseAdmin'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseUUID } from '@/lib/utils'
import { extractTrackingContext } from '@/lib/trackingContext'

// One endpoint, two shapes — dictated by navigator.sendBeacon, which is
// POST-only (no PATCH), so the dwell-fill beacon on unload has to be a POST
// too. Discriminated on `dwell_ms`: absent = new pageview, present = dwell
// fill for an existing row.
//
// The client generates its OWN row id (crypto.randomUUID()) and sends it on
// create, rather than reading one back from an insert response — Postgres
// RLS applies INSERT's WITH CHECK to a RETURNING clause, not a SELECT policy,
// so reading the id back would likely work, but "likely" isn't good enough
// for something every dwell-beacon depends on, and generating it client-side
// sidesteps the question entirely.

// The 1-hour RLS window already bounds how late a dwell fill can land; this
// is just a sanity clamp so one stuck tab can't write a huge dwell value.
const MAX_DWELL_MS = 4 * 60 * 60 * 1000 // 4 hours

export async function POST(req: Request) {
  const { success } = rateLimit(getIp(req), 120) // fires on every pageview + every unload
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  let body: Record<string, unknown>
  try { body = await req.json() } catch { return NextResponse.json({ error: 'Bad JSON' }, { status: 400 }) }

  // ── Dwell fill: { id, dwell_ms } ──────────────────────────────────────────
  // Uses the secret-key client, not the publishable one used everywhere else
  // in this route — see lib/supabaseAdmin.ts for why. This route is the trust
  // boundary (id must be a well-formed UUID, dwell_ms is clamped below).
  if (typeof body.dwell_ms === 'number') {
    if (!supabaseAdmin) return NextResponse.json({ ok: false }, { status: 200 })

    const id = sanitiseUUID(body.id as string)
    if (!id) return NextResponse.json({ error: 'id is required' }, { status: 400 })
    const dwell_ms = Math.max(0, Math.min(MAX_DWELL_MS, Math.round(body.dwell_ms)))

    const { error } = await supabaseAdmin.from('page_events').update({ dwell_ms }).eq('id', id)
    if (error) {
      console.error('page_events dwell update error:', error.message, error.code)
      return NextResponse.json({ ok: false }, { status: 200 })
    }
    return NextResponse.json({ ok: true })
  }

  // ── New pageview or share: { id, session_id, incident_id?, path, referrer?, event_type? } ──
  const id         = sanitiseUUID(body.id as string)
  const session_id = typeof body.session_id === 'string' ? body.session_id : null
  const path       = typeof body.path === 'string' ? body.path.slice(0, 500) : null
  if (!id || !session_id || !path) {
    return NextResponse.json({ error: 'id, session_id and path are required' }, { status: 400 })
  }

  // 'share' never gets a dwell fill and must be excluded from bounce/view
  // math (migration 020) — anything else falls back to 'pageview'.
  const event_type = body.event_type === 'share' ? 'share' : 'pageview'

  const incident_id = sanitiseUUID(body.incident_id as string)

  const { referrer, geo_country, geo_city, user_agent_hash } = await extractTrackingContext(req, body.referrer)

  const { error } = await supabase
    .from('page_events')
    .insert({
      id, session_id, incident_id, path, referrer, event_type,
      geo_country, geo_city, user_agent_hash,
    })

  if (error) {
    console.error('page_events insert error:', error.message, error.code)
    return NextResponse.json({ error: 'Insert failed' }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
