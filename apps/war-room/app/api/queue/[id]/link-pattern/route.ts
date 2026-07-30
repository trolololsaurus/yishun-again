import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Creates confirmed incident_links between every pair of incidents in the pattern.
export async function POST(
  _request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

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

  const incidentIds = (rc.incident_ids as string[]) ?? []
  const patternAlertId = validateUUID(rc.pattern_alert_id as string)
  const patternValue   = (rc.pattern_value as string) ?? ''
  const patternType    = (rc.pattern_type  as string) ?? 'related'

  // Map pattern type to link_type
  const linkType = patternType === 'location' ? 'same_location'
                 : patternType === 'entity'   ? 'related'
                 : 'related'

  // Create links between every pair of incidents
  const pairs: [string, string][] = []
  for (let i = 0; i < incidentIds.length; i++) {
    for (let j = i + 1; j < incidentIds.length; j++) {
      pairs.push([incidentIds[i], incidentIds[j]])
    }
  }

  let linksCreated = 0
  for (const [a, b] of pairs) {
    const { error } = await supabase.from('incident_links').insert({
      incident_a:            a,
      incident_b:            b,
      link_type:             linkType,
      confidence:            0.9,
      agent_reason:          `Pattern detected: ${patternType} / ${patternValue}`,
      confirmed_by_operator: true,
    })
    if (!error || error.code === '23505') linksCreated++
  }

  // Confirm the pattern alert
  if (patternAlertId) {
    const { error: alertErr } = await supabase
      .from('pattern_alerts')
      .update({ status: 'confirmed', operator_action: 'link_incidents', resolved_at: new Date().toISOString() })
      .eq('id', patternAlertId)
    if (alertErr) console.error('link-pattern — pattern_alerts update failed:', alertErr)
  }

  // Dismiss the notification
  const { error: queueErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)
  if (queueErr) {
    console.error('link-pattern — queue update failed:', queueErr)
    return NextResponse.json({ error: 'Links created but the notification could not be dismissed — it may reappear.', links_created: linksCreated }, { status: 500 })
  }

  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:      null,
    action:           'pattern_confirmed',
    decision:         'approve',
    operator_changes: {
      pattern_type:    rc.pattern_type,
      pattern_value:   rc.pattern_value,
      incident_count:  incidentIds.length,
      operator_action: 'link_incidents',
    },
  })
  if (signalErr) console.error('link-pattern — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ ok: true, links_created: linksCreated })
}
