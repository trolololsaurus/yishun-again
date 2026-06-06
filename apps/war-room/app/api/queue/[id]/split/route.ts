import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Converts an 'update' queue item into a standard 'pending' item so it goes
// through the normal approve flow as a new incident instead of an update.
export async function POST(
  _request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id')
    .eq('id', id)
    .eq('status', 'update')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an update item' }, { status: 404 })
  }

  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({
      status:                    'pending',
      update_target_incident_id: null,
    })
    .eq('id', id)

  if (updateErr) {
    console.error('split — queue update:', updateErr)
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
  }

  // Log training signal: agent said 'update', operator said it's actually a new incident
  const { data: freshItem } = await supabase
    .from('war_room_queue')
    .select('raw_content,proposed_summary,proposed_classification,proposed_severity,agent_confidence')
    .eq('id', id)
    .single()

  if (freshItem) {
    const rc = (freshItem.raw_content ?? {}) as Record<string, unknown>
    await supabase.from('training_signals').insert({
      incident_id:             null,
      action:                  'reject',
      reject_reason:           'duplicate',
      original_draft:          freshItem.proposed_summary,
      original_classification: freshItem.proposed_classification,
      original_severity:       freshItem.proposed_severity,
      agent_confidence_was:    freshItem.agent_confidence,
      agent_role_proposed:     (rc.agent_role_proposed as string) ?? 'update',
      operator_role_confirmed: 'initial',   // operator says this is a new separate incident
      operator_changes:        { split_from_update: true },
    })
  }

  return NextResponse.json({ ok: true })
}
