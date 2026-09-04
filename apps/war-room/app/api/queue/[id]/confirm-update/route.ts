import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID, applyUpdate, applySignalCorroboration } from '@/lib/utils'

interface ConfirmUpdateBody {
  updated_summary?: string
}

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: ConfirmUpdateBody = {}
  try { body = await request.json() } catch { /* no body is fine */ }

  // Fetch the queue item
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('*')
    .eq('id', id)
    .eq('status', 'update')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or not an update item' }, { status: 404 })
  }

  const targetId = item.update_target_incident_id
  if (!targetId) {
    return NextResponse.json({ error: 'Queue item has no update_target_incident_id' }, { status: 400 })
  }

  // Guardrail #2: a signal source's URL may never become a quoted citation.
  // 'edmw' is the tolerated legacy alias for the canonical 'signal' — see
  // classifiers/source_allowlist.py's SIGNAL_TYPES. That does NOT mean the
  // update is worthless — a signal corroborating an already-published incident
  // is exactly the "Forum buzz" case a signal-only match on a BRAND NEW incident
  // already gets (edmw_signal_count, bumped at initial publish). So a signal
  // source still confirms as an update — it just takes the corroboration-only
  // path (applySignalCorroboration) instead of the source_urls/timeline merge.
  const isSignal = item.source_type === 'signal' || item.source_type === 'edmw'

  // Fetch the existing published incident. `summary` is selected so the undo
  // snapshot can restore it (an operator edit below replaces it).
  const { data: existing, error: incFetchErr } = await supabase
    .from('incidents')
    .select('id,source_urls,source_timeline,update_count,incident_date,first_reported_at,is_developing,summary,edmw_signal_count')
    .eq('id', targetId)
    .single()

  if (incFetchErr || !existing) {
    return NextResponse.json({ error: 'Target incident not found' }, { status: 404 })
  }

  const rc          = (item.raw_content ?? {}) as Record<string, unknown>
  const newSourceUrl = item.source_url
  const sourceName  = (rc.source_name as string) ?? item.source_type ?? 'unknown'
  const headline    = (item.proposed_title ?? (rc.title as string) ?? '').slice(0, 200)

  // applyUpdate owns the citation merge (source_urls, timeline, dates,
  // update_count); applySignalCorroboration owns the signal-only path
  // (edmw_signal_count, never source_urls). Both return the pre-merge snapshot
  // the undo route restores from. See lib/utils.ts — the autonomous auto-merge
  // (PR #2) mirrors applyUpdate in Python; it does not (yet) mirror the signal
  // path, so signal corroboration stays an operator-only action for now.
  const { updates, snapshot } = isSignal
    ? applySignalCorroboration(existing, { updatedSummary: body.updated_summary })
    : applyUpdate(existing, {
        newSourceUrl,
        sourceName,
        headline,
        newDate: (rc.date as string) || (rc.published_at as string) || null,
        updatedSummary: body.updated_summary,
      })
  const updatedSummary = (body.updated_summary ?? '').trim()

  // Claim the queue item BEFORE mutating the incident. The old order updated
  // the incident first and never checked the queue update's error — a failed
  // (or raced) status write left the item re-confirmable, appending duplicate
  // timeline entries and double-counting update_count. Losing the claim here
  // costs nothing; losing it after the mutation corrupts the incident.
  // The snapshot rides in raw_content so the undo route can restore the exact
  // pre-merge state.
  const { data: claimed, error: claimErr } = await supabase.from('war_room_queue').update({
    status:       'update_approved',
    incident_id:  targetId,
    processed_at: new Date().toISOString(),
    raw_content:  { ...rc, _undo_snapshot: snapshot },
  }).eq('id', id).eq('status', 'update').select('id')

  if (claimErr) {
    console.error('confirm-update — queue claim failed:', claimErr)
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
    .update(updates)
    .eq('id', targetId)

  if (updateErr) {
    console.error('confirm-update — incident update:', updateErr)
    // Give the claim back so the operator can retry once the cause is fixed.
    const { error: unclaimErr } = await supabase.from('war_room_queue')
      .update({ status: 'update', incident_id: null, processed_at: null })
      .eq('id', id).eq('status', 'update_approved')
    if (unclaimErr) console.error('confirm-update — failed to release claim after incident update error:', unclaimErr)
    return NextResponse.json({ error: 'Incident update failed' }, { status: 500 })
  }

  // Create incident_links for any confirmed related incidents
  const agentRelated = (rc.agent_related_incidents as Array<{
    incident_id: string; confidence: number; reason: string; link_type: string; dismissed?: boolean
  }>) ?? []

  for (const link of agentRelated) {
    if (link.dismissed) continue
    // QA M9: the operator just confirmed this update, so mark the link
    // operator-confirmed (it was looking agent-suggested); capture the error.
    const { error: linkErr } = await supabase.from('incident_links').insert({
      incident_a:           targetId,
      incident_b:           link.incident_id,
      link_type:            link.link_type ?? 'related',
      confidence:           link.confidence,
      agent_reason:         link.reason ?? '',
      confirmed_by_operator: true,
    })
    if (linkErr && linkErr.code !== '23505') console.error('confirm-update — incident_link insert:', linkErr)
  }

  // Update queue status
  await supabase.from('war_room_queue').update({
    status:       'update_approved',
    incident_id:  targetId,
    processed_at: new Date().toISOString(),
  }).eq('id', id)

  const agentRoleProposed = (rc.agent_role_proposed as string) ?? 'update'
  const action = updatedSummary ? 'edit_approve' : 'approve'

  await supabase.from('training_signals').insert({
    incident_id:             targetId,
    queue_id:                id,
    action,
    decision:                action === 'edit_approve' ? 'approve_with_edits' : 'approve',
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    proposed_classification: item.proposed_classification,
    proposed_severity:       item.proposed_severity,
    original_draft:          item.proposed_summary,
    edited_draft:            updatedSummary || null,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     agentRoleProposed,
    operator_role_confirmed: 'update',   // operator confirmed the update classification
  })

  return NextResponse.json({ ok: true, incident_id: targetId })
}
