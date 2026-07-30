/**
 * POST /api/backfill-bulk
 *
 * Bulk approve or reject backfill queue items by ID array.
 * Used by the BackfillBanner component for the War Room one-tap bulk actions.
 *
 * Body: { action: 'approve' | 'reject', ids: string[] }
 *
 * approve — publishes each item using its proposed values (equivalent to an
 *            approve-as-is; operator has already reviewed the confidence tier
 *            logic that created these items). Logs training_signal action='approve'.
 *
 * reject  — marks each item rejected with reason='noise'.
 *            Logs training_signal action='reject'.
 *
 * Returns: { updated: N, errors: string[] }
 */

import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { slugify } from '@/lib/utils'
import { geocodeIncident } from '@/lib/geocode'

const MAX_BULK = 200   // safety cap per call

function isValidUUID(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)
}

export async function POST(request: Request) {
  let body: { action: string; ids: string[] }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const { action, ids } = body

  if (!['approve', 'reject'].includes(action)) {
    return NextResponse.json({ error: "action must be 'approve' or 'reject'" }, { status: 400 })
  }

  if (!Array.isArray(ids) || ids.length === 0) {
    return NextResponse.json({ error: 'ids must be a non-empty array' }, { status: 400 })
  }

  // Sanitise and cap IDs
  const validIds = ids.filter(isValidUUID).slice(0, MAX_BULK)
  if (!validIds.length) {
    return NextResponse.json({ error: 'No valid UUIDs in ids' }, { status: 400 })
  }

  // Fetch all targeted queue items in one query
  const { data: items, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('*')
    .in('id', validIds)
    .eq('status', 'pending')

  if (fetchErr) {
    return NextResponse.json({ error: fetchErr.message }, { status: 500 })
  }

  const queue = items ?? []
  const now   = new Date().toISOString()

  let updated = 0
  const errors: string[] = []

  if (action === 'reject') {
    // ── BULK REJECT ─────────────────────────────────────────────────────────
    const { data: rejected, error: updateErr } = await supabase
      .from('war_room_queue')
      .update({ status: 'rejected', processed_at: now })
      .in('id', validIds)
      .eq('status', 'pending')
      .select('id')

    if (updateErr) {
      console.error('backfill-bulk — reject update failed:', updateErr)
      return NextResponse.json({ error: 'Queue update failed' }, { status: 500 })
    }

    // Log training signals. Awaited: in a serverless function an unawaited
    // insert can be frozen mid-flight when the response returns, silently
    // starving the learning loop. Signal errors still don't fail the bulk op.
    const rejectedIds = new Set((rejected ?? []).map(r => r.id))
    for (const item of queue) {
      if (!rejectedIds.has(item.id)) continue  // lost a race — no signal for a no-op
      const rc = (item.raw_content ?? {}) as Record<string, unknown>
      const { error: signalErr } = await supabase.from('training_signals').insert({
        incident_id:             null,
        queue_id:                item.id,
        action:                  'reject',
        decision:                'reject',
        source_url:              item.source_url,
        source_name:             rc.source_name as string | undefined,
        source_type:             item.source_type,
        proposed_classification: item.proposed_classification,
        proposed_severity:       item.proposed_severity,
        reject_reason:           'noise',
        original_draft:          item.proposed_summary,
        original_classification: item.proposed_classification,
        original_severity:       item.proposed_severity,
        // QA A11 — mark bulk decisions. One click over N cards is not N verdicts,
        // and ops/learning_monitor.py excludes these from the agreement rate.
        // Without the marker a bulk-heavy window always outscores a window of
        // genuine review, and the "is the model learning?" metric measures
        // workflow rather than model quality.
        operator_changes:        { bulk: true },
        agent_confidence_was:    item.agent_confidence,
      })
      if (signalErr) console.error('backfill-bulk — training_signal insert failed (non-fatal):', signalErr)
    }

    // Rows the CAS actually rejected — not the fetched count, which over-reports
    // when a concurrent request processed some of these items first.
    return NextResponse.json({ updated: rejectedIds.size, errors: [] })
  }

  // ── BULK APPROVE ───────────────────────────────────────────────────────────
  // Process each item individually so slug-conflict errors are isolated.
  for (const item of queue) {
    const rc    = (item.raw_content ?? {}) as Record<string, unknown>
    const title = (item.proposed_title ?? (rc.title as string) ?? '').slice(0, 120)
    const summary = item.proposed_summary ?? (rc.summary as string) ?? ''

    if (!title || !summary) {
      errors.push(`${item.id}: missing title or summary — skip`)
      continue
    }

    // 'custom' included — the single-approve route accepts it; silently
    // downgrading custom items to 'dagger' misfiled them in bulk.
    const classification = (['heart', 'clown', 'dagger', 'custom'].includes(item.proposed_classification ?? '')
      ? item.proposed_classification
      : 'dagger') as string
    const severity     = Math.max(1, Math.min(5, Number(item.proposed_severity) || 3))
    const sourceUrls   = (rc.source_urls as string[]) ?? [item.source_url]
    const slug         = item.proposed_slug ?? (rc.slug as string) ?? slugify(title)

    // QA H3 parity with the single-approve route: never silently stamp
    // incident_date with today. A dateless item must be approved one-by-one
    // with an operator-supplied date, not bulk-published with a wrong one.
    const rawDate      = (rc.date as string | undefined)?.substring(0, 10)
    const incidentDate = rawDate && /^\d{4}-\d{2}-\d{2}$/.test(rawDate) && !isNaN(Date.parse(rawDate))
      ? rawDate
      : null
    if (!incidentDate) {
      errors.push(`${item.id}: no valid article date — approve individually with a date (QA H3)`)
      continue
    }

    // Publish-time pin creation (block → POI → street); never trust LLM
    // coords from raw_content. Geocode failure publishes without a pin.
    const blockNumber = (rc.block_number as string | null) ?? null
    const areaName    = (rc.area_name    as string | null) ?? null
    let latitude: number | null = null
    let longitude: number | null = null
    try {
      const coords = await geocodeIncident(blockNumber, areaName, title)
      if (coords) [latitude, longitude] = coords
    } catch { /* publish without pin */ }

    const incident = {
      title,
      summary,
      classification,
      severity,
      block_number:        blockNumber,
      area_name:           areaName,
      latitude,
      longitude,
      source_urls:         sourceUrls,
      corroboration_count: item.corroboration_count ?? 1,
      edmw_signal_count:   item.edmw_signal_count   ?? 0,
      hype_meter:          (rc.hype_meter    as number) ?? 0,
      slug,
      seo_title:           (rc.seo_title           as string | null) ?? null,
      seo_description:     (rc.seo_description     as string | null) ?? null,
      tags:                (rc.tags                as string[])      ?? [],
      agent_confidence:    item.agent_confidence ?? null,
      chaos_contribution:  (rc.chaos_contribution  as number | null) ?? null,
      deaths:              (rc.deaths   as number | null) ?? null,
      injuries:            (rc.injuries as number | null) ?? null,
      is_milestone:        false,
      incident_date:       incidentDate,
      first_reported_at:   incidentDate,
      latest_source_role:  'initial',
      is_developing:       false,
      update_count:        0,
      source_timeline:     [],
      is_published:        true,
      published_at:        now,
    }

    const { data: newIncident, error: incErr } = await supabase
      .from('incidents')
      .insert(incident)
      .select('id')
      .single()

    if (incErr) {
      const msg = incErr.code === '23505'
        ? `${item.id}: slug conflict — skip`
        : `${item.id}: ${incErr.message}`
      errors.push(msg)
      continue
    }

    // Mark queue item approved. CAS on status='pending': if a concurrent
    // request already processed it, delete the duplicate incident we created.
    const { data: claimed, error: queueErr } = await supabase.from('war_room_queue').update({
      status:       'approved',
      incident_id:  newIncident.id,
      processed_at: now,
    }).eq('id', item.id).eq('status', 'pending').select('id')

    if (queueErr) {
      errors.push(`${item.id}: published but queue update failed — reconcile manually`)
      console.error('backfill-bulk — queue update failed:', queueErr)
      continue
    }
    if (!claimed?.length) {
      const { error: undoErr } = await supabase.from('incidents').delete().eq('id', newIncident.id)
      if (undoErr) console.error('backfill-bulk — failed to remove duplicate incident after lost race:', undoErr)
      errors.push(`${item.id}: already processed by another request — skip`)
      continue
    }

    // Log training signal — awaited (see reject path), errors non-fatal.
    const { error: signalErr } = await supabase.from('training_signals').insert({
      incident_id:             newIncident.id,
      queue_id:                item.id,
      action:                  'approve',
      decision:                'approve',
      source_url:              item.source_url,
      source_name:             rc.source_name as string | undefined,
      source_type:             item.source_type,
      proposed_classification: item.proposed_classification,
      proposed_severity:       item.proposed_severity,
      original_draft:          item.proposed_summary,
      original_classification: item.proposed_classification,
      original_severity:       item.proposed_severity,
      operator_changes:        { bulk: true },   // QA A11 — see the reject path above
      agent_confidence_was:    item.agent_confidence,
    })
    if (signalErr) console.error('backfill-bulk — training_signal insert failed (non-fatal):', signalErr)

    updated++
  }

  return NextResponse.json({ updated, errors })
}
