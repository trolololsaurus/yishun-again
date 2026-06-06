import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

interface DismissLinkBody {
  related_incident_id: string
}

// Marks a suggested link as dismissed by the operator. The link is stored in
// raw_content.agent_related_incidents — we update that JSONB entry in place.
export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: DismissLinkBody
  try { body = await request.json() } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const relatedId = validateUUID(body.related_incident_id)
  if (!relatedId) return NextResponse.json({ error: 'Invalid related_incident_id' }, { status: 400 })

  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,raw_content')
    .eq('id', id)
    .in('status', ['pending', 'update'])
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found' }, { status: 404 })
  }

  const rc      = (item.raw_content ?? {}) as Record<string, unknown>
  const related = (rc.agent_related_incidents as Array<Record<string, unknown>>) ?? []

  const updated = related.map(r =>
    r.incident_id === relatedId ? { ...r, dismissed: true } : r
  )

  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({ raw_content: { ...rc, agent_related_incidents: updated } })
    .eq('id', id)

  if (updateErr) {
    console.error('dismiss-link — update:', updateErr)
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
