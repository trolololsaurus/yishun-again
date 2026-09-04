import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// The operator's reversal. Removes an incident from a pattern and records it in
// excluded_incident_ids so the auto-append agent never re-adds it. Works for
// both auto-added and hand-added ids; writes a 'pattern_append_reverted'
// training signal (the auto-append decision was wrong / unwanted).

const MAX_NOTE_CHARS = 500

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const pid = validateUUID((await props.params).id)
  if (!pid) return NextResponse.json({ error: 'Invalid pattern ID' }, { status: 400 })

  let incidentId: string | null = null
  let note: string | null = null
  try {
    const body = await request.json()
    incidentId = validateUUID(typeof body?.incident_id === 'string' ? body.incident_id : undefined)
    const rawNote = typeof body?.note === 'string' ? body.note.trim() : ''
    note = rawNote ? rawNote.slice(0, MAX_NOTE_CHARS) : null
  } catch { /* handled below */ }
  if (!incidentId) return NextResponse.json({ error: 'incident_id (UUID) is required' }, { status: 400 })

  const { data: pattern, error: pErr } = await supabase
    .from('patterns')
    .select('id,slug,incident_ids,auto_added_incident_ids,excluded_incident_ids')
    .eq('id', pid).single()
  if (pErr || !pattern) return NextResponse.json({ error: 'Pattern not found' }, { status: 404 })

  const wasAuto = (pattern.auto_added_incident_ids ?? []).includes(incidentId)
  const ids      = (pattern.incident_ids ?? []).filter((x: string) => x !== incidentId)
  const auto     = (pattern.auto_added_incident_ids ?? []).filter((x: string) => x !== incidentId)
  const excluded = [...new Set([...(pattern.excluded_incident_ids ?? []), incidentId])]

  const { error } = await supabase.from('patterns').update({
    incident_ids:            ids,
    auto_added_incident_ids: auto,
    excluded_incident_ids:   excluded,
    updated_at:              new Date().toISOString(),
  }).eq('id', pid)

  if (error) {
    console.error('POST /api/patterns/[id]/detach:', error)
    return NextResponse.json({ error: 'Detach failed' }, { status: 500 })
  }

  // Reversal IS training data. decision='reject' stays within the CHECK.
  const { error: sigErr } = await supabase.from('training_signals').insert({
    incident_id:  incidentId,
    action:       'pattern_append_reverted',
    decision:     'reject',
    decided_by:   'operator',
    reject_note:  note,
    operator_note: `Removed from pattern '${pattern.slug}'${wasAuto ? ' (was auto-appended)' : ''}.`,
  })
  if (sigErr) console.error('detach — training_signal insert failed (non-fatal):', sigErr)

  return NextResponse.json({ ok: true })
}
