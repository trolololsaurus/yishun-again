import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

export async function POST(
  _request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // Fetch agent_confidence before unpublishing for the training signal
  const { data: incident } = await supabase
    .from('incidents')
    .select('agent_confidence')
    .eq('id', id)
    .single()

  const { error } = await supabase
    .from('incidents')
    .update({ is_published: false, published_at: null })
    .eq('id', id)

  if (error) {
    console.error('Unpublish incident:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  await supabase.from('training_signals').insert({
    incident_id:          id,
    action:               'unpublish',
    decision:             'reject',
    agent_confidence_was: incident?.agent_confidence ?? null,
  })

  return NextResponse.json({ ok: true })
}
