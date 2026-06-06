import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID, slugify } from '@/lib/utils'

// Creates a people_profiles stub (is_published=FALSE) for operator to complete later.
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
  if (rc.notification_type !== 'pattern_alert') {
    return NextResponse.json({ error: 'Not a pattern alert notification' }, { status: 400 })
  }

  const patternValue   = (rc.pattern_value as string) ?? 'unknown'
  const patternType    = (rc.pattern_type  as string) ?? ''
  const incidentIds    = (rc.incident_ids  as string[]) ?? []
  const patternAlertId = validateUUID(rc.pattern_alert_id as string)

  // Only entity patterns create people profiles; location/crime_type patterns
  // create a stub with a note for operator context.
  const name = patternType === 'entity' ? patternValue : `[${patternType}] ${patternValue}`
  const baseSlug = slugify(name)

  // Try inserting; if slug conflicts, append timestamp
  let profileId: string | null = null
  for (const suffix of ['', `-${Date.now()}`]) {
    const slug = (baseSlug + suffix).slice(0, 70)
    const { data: profile, error: profErr } = await supabase
      .from('people_profiles')
      .insert({
        slug,
        name:              name.slice(0, 200),
        incident_ids:      incidentIds,
        is_published:      false,
        legal_sensitivity: 'medium',
        notes: `Auto-created from pattern alert: ${patternType} / ${patternValue}. Operator review required before publishing.`,
      })
      .select('id')
      .single()

    if (!profErr && profile) {
      profileId = profile.id
      break
    }
    if (profErr && profErr.code !== '23505') {
      console.error('note-profile — insert:', profErr)
      return NextResponse.json({ error: profErr.message }, { status: 500 })
    }
  }

  // Confirm the pattern alert
  if (patternAlertId) {
    await supabase
      .from('pattern_alerts')
      .update({ status: 'confirmed', operator_action: 'note_for_profile', resolved_at: new Date().toISOString() })
      .eq('id', patternAlertId)
  }

  // Dismiss the notification
  await supabase
    .from('war_room_queue')
    .update({ status: 'approved', processed_at: new Date().toISOString() })
    .eq('id', id)

  await supabase.from('training_signals').insert({
    incident_id:      null,
    action:           'pattern_confirmed',
    operator_changes: {
      pattern_type:    patternType,
      pattern_value:   patternValue,
      incident_count:  incidentIds.length,
      operator_action: 'note_for_profile',
    },
  })

  return NextResponse.json({ ok: true, profile_id: profileId })
}
