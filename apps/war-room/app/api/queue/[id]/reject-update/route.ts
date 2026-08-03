import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import type { RejectReason } from '@/lib/types'

// Same set the plain reject route accepts — see migration 017.
const VALID_REASONS: RejectReason[] = [
  'noise', 'duplicate', 'unverified', 'too_thin', 'legal_risk', 'not_yishun',
]

// An update rejection whose reason the operator did not specify. Kept as the
// fallback for older clients only; the UI always sends one now.
const DEFAULT_REASON: RejectReason = 'duplicate'

const MAX_NOTE_CHARS = 500

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // This route used to hardcode reject_reason: 'duplicate' on EVERY update
  // rejection. Updates are the majority of the queue (10 of 14 rows on the
  // 2026-08-03 pass), so a wrongly-attached update and a genuine duplicate were
  // indistinguishable in the training data — and it inflated the 'duplicate'
  // bucket, which `ingestion/learning.py` round-robins on when picking Stage 2
  // prompt examples. The operator's actual reason is now recorded.
  let reason: RejectReason = DEFAULT_REASON
  let note: string | null = null
  try {
    const body = await request.json()
    if (typeof body?.reason === 'string' && VALID_REASONS.includes(body.reason as RejectReason)) {
      reason = body.reason as RejectReason
    }
    const rawNote = typeof body?.note === 'string' ? body.note.trim() : ''
    note = rawNote ? rawNote.slice(0, MAX_NOTE_CHARS) : null
  } catch {
    // No body / unparseable — fall back to DEFAULT_REASON rather than 400ing,
    // so a rejection is never lost over a malformed payload.
  }

  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,source_url,source_type,agent_confidence,proposed_summary,proposed_classification,proposed_severity,raw_content')
    .eq('id', id)
    .eq('status', 'update')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an update item' }, { status: 404 })
  }

  // CAS on status='update' so a racing confirm-update isn't clobbered.
  const { data: claimed, error: updateErr } = await supabase.from('war_room_queue').update({
    status:       'update_rejected',
    processed_at: new Date().toISOString(),
  }).eq('id', id).eq('status', 'update').select('id')

  if (updateErr) {
    console.error('reject-update — queue update failed:', updateErr)
    return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
  }
  if (!claimed?.length) {
    return NextResponse.json(
      { error: 'Queue item was already processed by another request.' },
      { status: 409 },
    )
  }

  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  // queue_id/source_* make the signal visible to the learning loop —
  // rebuild_source_reputation() tallies per source_url domain and skipped
  // these rows entirely when they were omitted.
  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:             null,
    queue_id:                id,
    action:                  'reject',
    decision:                'reject',
    reject_reason:           reason,
    reject_note:             note,
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    original_draft:          item.proposed_summary,
    original_classification: item.proposed_classification,
    original_severity:       item.proposed_severity,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     (rc.agent_role_proposed as string) ?? 'update',
    operator_role_confirmed: null,   // operator rejected outright — no confirmed role
  })
  if (signalErr) console.error('reject-update — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ ok: true })
}
