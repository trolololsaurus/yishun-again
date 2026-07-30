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
    .select('id,source_url,source_type,raw_content,proposed_summary,proposed_classification,proposed_severity,agent_confidence')
    .eq('id', id)
    .eq('status', 'update')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an update item' }, { status: 404 })
  }

  const { data: claimed, error: updateErr } = await supabase
    .from('war_room_queue')
    .update({
      status:                    'pending',
      update_target_incident_id: null,
    })
    .eq('id', id)
    .eq('status', 'update')
    .select('id')

  if (updateErr) {
    console.error('split — queue update:', updateErr)
    return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
  }
  if (!claimed?.length) {
    return NextResponse.json(
      { error: 'Queue item was already processed by another request.' },
      { status: 409 },
    )
  }

  // Log training signal: agent said 'update', operator said it's actually a
  // new incident. queue_id/source_* keep the row visible to the learning loop
  // (rebuild_source_reputation tallies per source_url domain).
  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:             null,
    queue_id:                id,
    action:                  'reject',
    decision:                'reject',
    reject_reason:           'duplicate',
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    original_draft:          item.proposed_summary,
    original_classification: item.proposed_classification,
    original_severity:       item.proposed_severity,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     (rc.agent_role_proposed as string) ?? 'update',
    operator_role_confirmed: 'initial',   // operator says this is a new separate incident
    operator_changes:        { split_from_update: true },
  })
  if (signalErr) console.error('split — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ ok: true })
}
