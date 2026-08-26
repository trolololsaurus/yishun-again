import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID, revertUpdate, type UpdateSnapshot } from '@/lib/utils'

// Undo an already-APPLIED update (merge). confirm-update (and PR #2's auto-merge)
// mutate a live incident and stash the pre-merge state in
// raw_content._undo_snapshot; this route restores it. Reversal is a plain
// snapshot RESTORE, not a surgical un-append — see applyUpdate/revertUpdate in
// lib/utils.ts for why.

const MAX_NOTE_CHARS = 500

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let note: string | null = null
  try {
    const body = await request.json()
    const rawNote = typeof body?.note === 'string' ? body.note.trim() : ''
    note = rawNote ? rawNote.slice(0, MAX_NOTE_CHARS) : null
  } catch { /* no body is fine */ }

  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,incident_id,update_target_incident_id,source_url,source_type,agent_confidence,proposed_classification,proposed_severity,proposed_summary,raw_content')
    .eq('id', id)
    .eq('status', 'update_approved')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an applied update' }, { status: 404 })
  }

  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  const snapshot = rc._undo_snapshot as UpdateSnapshot | undefined
  if (!snapshot || !Array.isArray(snapshot.source_urls)) {
    // A merge applied before this feature shipped has no snapshot — there is
    // nothing to restore from, and guessing would corrupt the incident. The
    // operator must fix it by hand (e.g. via /rectify).
    return NextResponse.json(
      { error: 'This update has no undo snapshot (merged before undo existed) — reconcile it by hand.' },
      { status: 422 },
    )
  }

  const targetId = item.incident_id ?? item.update_target_incident_id
  if (!targetId) {
    return NextResponse.json({ error: 'Queue item has no incident to revert' }, { status: 400 })
  }

  // Claim the row BEFORE mutating the incident (same ordering as confirm-update):
  // a failed status write must never leave the incident restored but the row
  // still re-revertable. CAS on status='update_approved'.
  const { data: claimed, error: claimErr } = await supabase.from('war_room_queue').update({
    status:       'update_reverted',
    processed_at: new Date().toISOString(),
  }).eq('id', id).eq('status', 'update_approved').select('id')

  if (claimErr) {
    console.error('revert-update — queue claim failed:', claimErr)
    return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
  }
  if (!claimed?.length) {
    return NextResponse.json(
      { error: 'Queue item was already processed by another request.' },
      { status: 409 },
    )
  }

  const { error: updateErr } = await supabase
    .from('incidents')
    .update(revertUpdate(snapshot))
    .eq('id', targetId)

  if (updateErr) {
    console.error('revert-update — incident restore:', updateErr)
    // Give the claim back so the operator can retry once the cause is fixed.
    const { error: unclaimErr } = await supabase.from('war_room_queue')
      .update({ status: 'update_approved', processed_at: null })
      .eq('id', id).eq('status', 'update_reverted')
    if (unclaimErr) console.error('revert-update — failed to release claim after restore error:', unclaimErr)
    return NextResponse.json({ error: 'Incident restore failed' }, { status: 500 })
  }

  // The reversal IS training data: the merge passed the gate and was wrong.
  // action='update_reverted' mirrors 'auto_publish_reverted'; decision='reject'
  // stays within the existing CHECK (migration 018).
  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:             targetId,
    queue_id:                id,
    action:                  'update_reverted',
    decision:                'reject',
    reject_note:             note,
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    original_draft:          item.proposed_summary,
    original_classification: item.proposed_classification,
    original_severity:       item.proposed_severity,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     (rc.agent_role_proposed as string) ?? 'update',
    operator_role_confirmed: null,
  })
  if (signalErr) console.error('revert-update — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ ok: true, incident_id: targetId })
}
