import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Operator confirms the auto-conclusion is correct. Notification is dismissed;
// the incident remains concluded (is_developing=FALSE, conclusion_type='timeout').
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

  const { error: updateErr } = await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)

  if (updateErr) {
    return NextResponse.json({ error: updateErr.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
