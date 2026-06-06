import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

interface ConfirmLinkBody {
  related_incident_id: string
  link_type:           string
  confidence:          number
  agent_reason:        string
}

const VALID_LINK_TYPES = ['related', 'follow_up', 'same_location'] as const

export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: ConfirmLinkBody
  try { body = await request.json() } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const relatedId = validateUUID(body.related_incident_id)
  if (!relatedId) return NextResponse.json({ error: 'Invalid related_incident_id' }, { status: 400 })

  const linkType = VALID_LINK_TYPES.includes(body.link_type as typeof VALID_LINK_TYPES[number])
    ? body.link_type
    : 'related'

  // Fetch queue item to get update_target_incident_id (the "A" side of the link)
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,update_target_incident_id')
    .eq('id', id)
    .in('status', ['pending', 'update'])
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found' }, { status: 404 })
  }

  const incidentAId = item.update_target_incident_id
  if (!incidentAId) {
    return NextResponse.json(
      { error: 'Cannot confirm link: queue item has no matched incident (only available for update items)' },
      { status: 400 }
    )
  }

  // Create the incident_links row; ignore unique-constraint errors
  const { error: insertErr } = await supabase.from('incident_links').insert({
    incident_a:           incidentAId,
    incident_b:           relatedId,
    link_type:            linkType,
    confidence:           Math.max(0, Math.min(1, Number(body.confidence) || 0.5)),
    agent_reason:         (body.agent_reason ?? '').slice(0, 500),
    confirmed_by_operator: true,
  })

  if (insertErr && insertErr.code !== '23505') {
    console.error('confirm-link — insert:', insertErr)
    return NextResponse.json({ error: insertErr.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
