import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import type { RejectBody, RejectReason } from '@/lib/types'

const VALID_REASONS: RejectReason[] = [
  'noise', 'duplicate', 'unverified', 'too_thin', 'legal_risk',
]

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: RejectBody
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const reason = body.reason as RejectReason
  if (!VALID_REASONS.includes(reason)) {
    return NextResponse.json({ error: 'Invalid reject reason' }, { status: 400 })
  }

  // Verify item exists and is pending
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id, source_url, source_type, raw_content, proposed_summary, proposed_classification, proposed_severity, agent_confidence')
    .eq('id', id)
    .eq('status', 'pending')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or already processed' }, { status: 404 })
  }

  // Update queue status
  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'rejected', processed_at: new Date().toISOString() })
    .eq('id', id)

  if (updateErr) {
    console.error('Reject — update queue:', updateErr)
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
  }

  // Log training signal — no incident_id on reject
  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  await supabase.from('training_signals').insert({
    incident_id:             null,
    queue_id:                id,
    action:                  'reject',
    decision:                'reject',
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    proposed_classification: item.proposed_classification,
    proposed_severity:       item.proposed_severity,
    reject_reason:           reason,
    original_draft:          item.proposed_summary,
    original_classification: item.proposed_classification,
    original_severity:       item.proposed_severity,
    agent_confidence_was:    item.agent_confidence,
  })

  return NextResponse.json({ ok: true })
}
