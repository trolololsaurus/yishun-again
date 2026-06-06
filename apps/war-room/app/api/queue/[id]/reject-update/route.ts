import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

export async function POST(
  _request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,agent_confidence,proposed_summary,proposed_classification,proposed_severity')
    .eq('id', id)
    .eq('status', 'update')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an update item' }, { status: 404 })
  }

  await supabase.from('war_room_queue').update({
    status:       'update_rejected',
    processed_at: new Date().toISOString(),
  }).eq('id', id)

  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  await supabase.from('training_signals').insert({
    incident_id:             null,
    action:                  'reject',
    reject_reason:           'duplicate',
    original_draft:          item.proposed_summary,
    original_classification: item.proposed_classification,
    original_severity:       item.proposed_severity,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     (rc.agent_role_proposed as string) ?? 'update',
    operator_role_confirmed: null,   // operator rejected outright — no confirmed role
  })

  return NextResponse.json({ ok: true })
}
