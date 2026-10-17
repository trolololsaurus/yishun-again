import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // ── Pattern alert dismissal ──────────────────────────────────────────────
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
    const { error: alertErr } = await supabase
      .from('pattern_alerts')
      .update({
        status:          'dismissed',
        operator_action: 'dismissed',
        resolved_at:     new Date().toISOString(),
      })
      .eq('id', patternAlertId)
    if (alertErr) console.error('dismiss-alert — pattern_alerts update failed:', alertErr)
  }

  const { error: queueErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'rejected', processed_at: new Date().toISOString() })
    .eq('id', id)
  if (queueErr) {
    console.error('dismiss-alert — queue update failed:', queueErr)
    return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
  }

  const { error: patternSignalErr } = await supabase.from('training_signals').insert({
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
  if (patternSignalErr) console.error('dismiss-alert — training_signal insert failed (non-fatal):', patternSignalErr)

  return NextResponse.json({ ok: true })
}
