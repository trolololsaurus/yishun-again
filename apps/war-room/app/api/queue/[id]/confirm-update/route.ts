import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

interface ConfirmUpdateBody {
  updated_summary?: string
}

export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
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

  // Fetch the existing published incident
  const { data: existing, error: incFetchErr } = await supabase
    .from('incidents')
    .select('id,source_urls,source_timeline,update_count,incident_date,first_reported_at,is_developing')
    .eq('id', targetId)
    .single()

  if (incFetchErr || !existing) {
    return NextResponse.json({ error: 'Target incident not found' }, { status: 404 })
  }

  const rc          = (item.raw_content ?? {}) as Record<string, unknown>
  const newSourceUrl = item.source_url
  const sourceName  = (rc.source_name as string) ?? item.source_type ?? 'unknown'
  const headline    = (item.proposed_title ?? (rc.title as string) ?? '').slice(0, 200)

  // Merge source_urls — deduplicate
  const existingUrls: string[] = existing.source_urls ?? []
  const mergedUrls = existingUrls.includes(newSourceUrl)
    ? existingUrls
    : [...existingUrls, newSourceUrl]

  // Append to source_timeline
  const existingTimeline: unknown[] = Array.isArray(existing.source_timeline)
    ? existing.source_timeline
    : []

  // Date of the merged source — the candidate's REAL article date, never
  // "today". Stamping new Date() corrupted incident_date (rows floated to the
  // top of the feed dated today) and littered the timeline with merge-day
  // stamps. Prefer the queue item's own date; fall back to the incident's
  // existing date so a merge can never push the date into the future.
  const existingDate = existing.incident_date ?? null
  const newDate =
    (rc.date as string) ||
    (rc.published_at as string) ||
    existingDate ||
    existing.first_reported_at ||
    null

  const newTimelineEntry = {
    date:        newDate ?? existingDate,
    source_url:  newSourceUrl,
    source_name: sourceName,
    headline,
  }
  const mergedTimeline = [...existingTimeline, newTimelineEntry]

  // incident_date = latest known date, first_reported_at = earliest known date.
  // Both clamp to real dates only — newDate is never "today".
  const updatedDate      = existingDate && newDate && existingDate > newDate ? existingDate : (newDate ?? existingDate)
  const existingFirstDate = existing.first_reported_at ?? existingDate
  const firstReportedAt  = existingFirstDate && newDate && existingFirstDate < newDate
    ? existingFirstDate
    : (existingFirstDate ?? newDate)

  const updates: Record<string, unknown> = {
    source_urls:      mergedUrls,
    source_timeline:  mergedTimeline,
    is_developing:    true,
    update_count:     (existing.update_count ?? 0) + 1,
    incident_date:    updatedDate,
    first_reported_at: firstReportedAt,
  }

  // Use operator-edited summary if provided
  const updatedSummary = (body.updated_summary ?? '').trim()
  if (updatedSummary) updates.summary = updatedSummary

  const { error: updateErr } = await supabase
    .from('incidents')
    .update(updates)
    .eq('id', targetId)

  if (updateErr) {
    console.error('confirm-update — incident update:', updateErr)
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
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
    action,
    decision:                action === 'edit_approve' ? 'approve_with_edits' : 'approve',
    original_draft:          item.proposed_summary,
    edited_draft:            updatedSummary || null,
    agent_confidence_was:    item.agent_confidence,
    agent_role_proposed:     agentRoleProposed,
    operator_role_confirmed: 'update',   // operator confirmed the update classification
  })

  return NextResponse.json({ ok: true, incident_id: targetId })
}
