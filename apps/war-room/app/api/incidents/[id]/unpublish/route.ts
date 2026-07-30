import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

export async function POST(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // Fetch agent_confidence before unpublishing for the training signal
  const { data: incident } = await supabase
    .from('incidents')
    .select('agent_confidence,is_published')
    .eq('id', id)
    .single()

  // QA M10: no-op if already a draft — a double-submit must not log a second
  // 'unpublish' training signal for an already-unpublished incident.
  if (!incident) {
    return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
  }
  if (!incident.is_published) {
    return NextResponse.json({ ok: true, noop: true })
  }

  const { error } = await supabase
    .from('incidents')
    .update({ is_published: false, published_at: null })
    .eq('id', id)
    .eq('is_published', true)

  if (error) {
    console.error('Unpublish incident:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  // Training signal is telemetry — the unpublish above has already committed,
  // so a signal failure must not fail the request. supabase-js returns errors
  // rather than throwing, so capture and log it instead of silently dropping it
  // (this insert was previously swallowed when 'unpublish' wasn't an allowed
  // action — see migration 009).
  const { error: signalError } = await supabase.from('training_signals').insert({
    incident_id:          id,
    action:               'unpublish',
    decision:             'reject',
    agent_confidence_was: incident?.agent_confidence ?? null,
  })
  if (signalError) {
    console.error('Unpublish training_signal insert failed (non-fatal):', signalError)
  }

  return NextResponse.json({ ok: true })
}
