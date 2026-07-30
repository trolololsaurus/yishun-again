import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Operator confirms the auto-conclusion is correct. Notification is dismissed;
// the incident remains concluded (is_developing=FALSE, conclusion_type='timeout').
export async function POST(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
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

  // QA H1: actually PERSIST the conclusion on the incident — the route's contract
  // says the incident remains concluded, but this previously only dismissed the
  // notification, leaving is_developing=TRUE with no conclusion_type/concluded_at.
  // validateUUID: raw_content is pipeline-written JSONB — a malformed value
  // otherwise reaches PostgREST and 500s with a raw DB error (reopen validates;
  // this route didn't).
  const incidentId = validateUUID(rc.incident_id as string)
  if (incidentId) {
    const { error: incErr } = await supabase
      .from('incidents')
      .update({
        is_developing:      false,
        conclusion_type:    'timeout',
        concluded_at:       new Date().toISOString(),
        latest_source_role: 'timeout',
      })
      .eq('id', incidentId)
    if (incErr) {
      console.error('confirm-close — incident conclusion update failed:', incErr)
      return NextResponse.json({ error: 'Incident update failed' }, { status: 500 })
    }
  }

  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)

  if (updateErr) {
    console.error('confirm-close — queue update failed:', updateErr)
    return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
