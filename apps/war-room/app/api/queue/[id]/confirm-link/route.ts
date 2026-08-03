import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

interface ConfirmLinkBody {
  related_incident_id: string
  link_type:           string
  confidence:          number
  agent_reason:        string
}

const VALID_LINK_TYPES = ['related', 'follow_up', 'same_location'] as const

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: ConfirmLinkBody
  try { body = await request.json() } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const relatedId = validateUUID(body.related_incident_id)
  if (!relatedId) return NextResponse.json({ error: 'Invalid related_incident_id' }, { status: 400 })

  const linkType = VALID_LINK_TYPES.includes(body.link_type as typeof VALID_LINK_TYPES[number])
    ? body.link_type
    : 'related'

  // Fetch queue item to get update_target_incident_id (the "A" side of the link)
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('id,update_target_incident_id')
    .eq('id', id)
    .in('status', ['pending', 'update'])
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found' }, { status: 404 })
  }

  const incidentAId = item.update_target_incident_id
  if (!incidentAId) {
    return NextResponse.json(
      { error: 'Cannot confirm link: queue item has no matched incident (only available for update items)' },
      { status: 400 }
    )
  }

  // Create the incident_links row; ignore unique-constraint errors
  const { error: insertErr } = await supabase.from('incident_links').insert({
    incident_a:           incidentAId,
    incident_b:           relatedId,
    link_type:            linkType,
    confidence:           Math.max(0, Math.min(1, Number(body.confidence) || 0.5)),
    agent_reason:         (body.agent_reason ?? '').slice(0, 500),
    confirmed_by_operator: true,
  })

  if (insertErr && insertErr.code !== '23505') {
    console.error('confirm-link — insert:', insertErr)
    return NextResponse.json({ error: insertErr.message }, { status: 500 })
  }

  // ── Training signal: the agent proposed this link and the operator AGREED ──
  //
  // This route recorded nothing before, and that silently broke graduated
  // autonomy. `classifiers/autonomy_tracker.get_autonomy_status()` computes
  //
  //     error_rate = corrections / total
  //
  // where a row counts toward `total` if it carries an `autonomy_signal`, and
  // toward `corrections` if it also carries a `dismiss_reason_category`. Both
  // fields were only ever written by the DISMISS path, so every counted
  // decision was a failure and error_rate was pinned at exactly 1.00 — measured
  // live on 2026-08-03 across entity_extraction, confidence_threshold and
  // temporal_dedup. Every threshold in GRADUATION_THRESHOLDS demands an
  // error_rate between 0.03 and 0.10, so nothing could ever graduate. The
  // system could only ever count its own mistakes.
  //
  // Recording the agreement here supplies the missing denominator.
  //
  // The signal is `confidence_threshold`, NOT one of the dedup categories: the
  // dismissal taxonomy (entity_dedup, location_dedup, …) diagnoses WHY a link
  // was wrong, and those states are unobservable on a success. What a
  // confirmation actually validates is the decision to surface a link at this
  // confidence — which is exactly what `confidence_threshold` measures. The
  // dismiss path writes the same signal for symmetry and keeps its category as
  // a separate diagnostic breakdown.
  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:      null,
    queue_id:         id,
    action:           'pattern_confirmed',
    decision:         'approve',
    operator_changes: {
      autonomy_signal:  'confidence_threshold',
      link_confirmed:   true,
      link_type:        linkType,
      link_confidence:  Math.max(0, Math.min(1, Number(body.confidence) || 0.5)),
      agent_reason:     (body.agent_reason ?? '').slice(0, 500),
      // dismiss_reason_category is deliberately ABSENT — its presence is what
      // autonomy_tracker reads as "the operator overturned the agent".
    },
  })
  if (signalErr) console.error('confirm-link — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ ok: true })
}
