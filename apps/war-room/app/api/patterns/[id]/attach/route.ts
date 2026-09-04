import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Manually attach an incident to a pattern. Accepts a UUID or a slug. Adds to
// incident_ids and CLEARS any prior exclusion (an operator re-adding something
// the agent had been told to skip overrides that block). Not an auto-append, so
// it does not touch auto_added_incident_ids.

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const pid = validateUUID((await props.params).id)
  if (!pid) return NextResponse.json({ error: 'Invalid pattern ID' }, { status: 400 })

  let ref = ''
  try {
    const body = await request.json()
    ref = typeof body?.incident === 'string' ? body.incident.trim() : ''
  } catch { /* handled below */ }
  if (!ref) return NextResponse.json({ error: 'incident (id or slug) is required' }, { status: 400 })

  // Resolve to an incident id: a UUID is used directly; anything else is treated
  // as a slug and looked up.
  let incidentId = validateUUID(ref)
  let resolved: { id: string; slug: string; title: string; incident_date: string | null } | null = null
  {
    const q = incidentId
      ? supabase.from('incidents').select('id,slug,title,incident_date').eq('id', incidentId)
      : supabase.from('incidents').select('id,slug,title,incident_date').eq('slug', ref.replace(/[^a-z0-9-]/g, ''))
    const { data } = await q.single()
    if (!data) return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
    resolved = data
    incidentId = data.id
  }

  const { data: pattern, error: pErr } = await supabase
    .from('patterns')
    .select('id,incident_ids,excluded_incident_ids')
    .eq('id', pid).single()
  if (pErr || !pattern) return NextResponse.json({ error: 'Pattern not found' }, { status: 404 })

  const ids = [...new Set([...(pattern.incident_ids ?? []), incidentId as string])]
  const excluded = (pattern.excluded_incident_ids ?? []).filter((x: string) => x !== incidentId)

  const { error } = await supabase.from('patterns').update({
    incident_ids:          ids,
    excluded_incident_ids: excluded,
    updated_at:            new Date().toISOString(),
  }).eq('id', pid)

  if (error) {
    console.error('POST /api/patterns/[id]/attach:', error)
    return NextResponse.json({ error: 'Attach failed' }, { status: 500 })
  }
  return NextResponse.json({ ok: true, incident: resolved })
}
