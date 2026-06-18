import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { DISMISS_CATEGORIES, type DismissCategory } from '@/lib/types'

const VALID_CATEGORIES = Object.keys(DISMISS_CATEGORIES) as DismissCategory[]

export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: Record<string, unknown> = {}
  try { body = await request.json() } catch { /* no body — pattern alert dismiss */ }

  // ── Incident link dismissal (reason_category present) ────────────────────
  if (body.reason_category !== undefined) {
    const category = body.reason_category as DismissCategory
    if (!VALID_CATEGORIES.includes(category)) {
      return NextResponse.json({ error: 'Invalid reason_category' }, { status: 400 })
    }

    const relatedId = validateUUID(body.related_incident_id as string)
    if (!relatedId) {
      return NextResponse.json({ error: 'Invalid or missing related_incident_id' }, { status: 400 })
    }

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

    const matchedLink    = related.find(r => r.incident_id === relatedId)
    const linkConfidence = matchedLink?.confidence ?? null
    const agentReason    = matchedLink?.agent_reason ?? null

    const updated = related.map(r =>
      r.incident_id === relatedId ? { ...r, dismissed: true } : r
    )

    const { error: updateErr } = await supabase
      .from('war_room_queue')
      .update({ raw_content: { ...rc, agent_related_incidents: updated } })
      .eq('id', id)

    if (updateErr) {
      console.error('dismiss-alert — link update:', updateErr)
      return NextResponse.json({ error: updateErr.message }, { status: 500 })
    }

    await supabase.from('training_signals').insert({
      incident_id:      null,
      action:           'pattern_dismissed',
      decision:         'reject',
      operator_changes: {
        dismiss_reason_category: category,
        dismiss_reason_detail:   (body.reason_detail as string) || null,
        autonomy_signal:         DISMISS_CATEGORIES[category].autonomy_signal,
        link_confidence:         linkConfidence,
        agent_reason:            agentReason,
      },
    })

    return NextResponse.json({ ok: true })
  }

  // ── Pattern alert dismissal (no reason_category — existing behaviour) ────
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,raw_content')
    .eq('id', id)
    .eq('status', 'pending')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found' }, { status: 404 })
  }

  const rc = (item.raw_content ?? {}) as Record<string, unknown>
  if (rc.notification_type !== 'pattern_alert') {
    return NextResponse.json({ error: 'Not a pattern alert notification' }, { status: 400 })
  }

  const patternAlertId = validateUUID(rc.pattern_alert_id as string)
  if (patternAlertId) {
    await supabase
      .from('pattern_alerts')
      .update({
        status:          'dismissed',
        operator_action: 'dismissed',
        resolved_at:     new Date().toISOString(),
      })
      .eq('id', patternAlertId)
  }

  await supabase
    .from('war_room_queue')
    .update({ status: 'rejected', processed_at: new Date().toISOString() })
    .eq('id', id)

  await supabase.from('training_signals').insert({
    incident_id:      null,
    action:           'pattern_dismissed',
    decision:         'reject',
    operator_changes: {
      pattern_type:    rc.pattern_type,
      pattern_value:   rc.pattern_value,
      incident_count:  ((rc.incident_ids as string[]) ?? []).length,
      operator_action: 'dismissed',
    },
  })

  return NextResponse.json({ ok: true })
}
