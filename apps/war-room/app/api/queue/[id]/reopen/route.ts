import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Operator reopens an auto-concluded story — restores is_developing and clears conclusion fields.
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
  if (rc.notification_type !== 'lifecycle_concluded') {
    return NextResponse.json({ error: 'Not a lifecycle notification' }, { status: 400 })
  }

  const incidentId = validateUUID(rc.incident_id as string)
  if (!incidentId) {
    return NextResponse.json({ error: 'Notification missing incident_id' }, { status: 400 })
  }

  // Restore the incident to developing status
  const { error: incErr } = await supabase
    .from('incidents')
    .update({
      is_developing:       true,
      latest_source_role:  'follow_up',
      concluded_at:        null,
      conclusion_type:     null,
    })
    .eq('id', incidentId)

  if (incErr) {
    console.error('reopen — incident update:', incErr)
    return NextResponse.json({ error: 'Incident update failed' }, { status: 500 })
  }

  // Dismiss the notification — surface a failure instead of leaving the
  // notification re-actionable while claiming success.
  const { error: dismissErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)
  if (dismissErr) {
    console.error('reopen — notification dismiss failed:', dismissErr)
    return NextResponse.json(
      { error: 'Incident reopened but the notification could not be dismissed — it may reappear.', incident_id: incidentId },
      { status: 500 },
    )
  }

  return NextResponse.json({ ok: true, incident_id: incidentId })
}
