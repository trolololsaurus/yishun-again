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
  const incidentId = rc.incident_id as string | undefined
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
      return NextResponse.json({ error: incErr.message }, { status: 500 })
    }
  }

  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)

  if (updateErr) {
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
