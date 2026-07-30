import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID, slugify } from '@/lib/utils'
import { geocodeIncident } from '@/lib/geocode'
import type { ApproveBody, Classification } from '@/lib/types'

export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: ApproveBody
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  // Sanitise inputs
  const title           = (body.title          ?? '').slice(0, 120).trim()
  const summary         = (body.summary         ?? '').trim().slice(0, 5000)
  const classification  = (['heart', 'clown', 'dagger', 'custom'].includes(body.classification)
    ? body.classification : 'dagger') as Classification
  const severity        = Math.max(1, Math.min(5, Number(body.severity) || 3))
  const pixel_art_prompt = (body.pixel_art_prompt ?? '').trim()

  if (!title || !summary) {
    return NextResponse.json({ error: 'title and summary are required' }, { status: 400 })
  }

  // Fetch the queue item
  const { data: item, error: fetchErr } = await supabase
    .from('war_room_queue')
    .select('*')
    .eq('id', id)
    .eq('status', 'pending')
    .single()

  if (fetchErr || !item) {
    return NextResponse.json({ error: 'Queue item not found or already processed' }, { status: 404 })
  }

  const rc          = (item.raw_content ?? {}) as Record<string, unknown>
  const sourceUrls  = ((rc.source_urls as string[]) ?? [item.source_url]).filter(Boolean)
  const slug        = (item.proposed_slug ?? (rc.slug as string) ?? slugify(title))

  // QA C4/guardrail #1: never publish without a verifiable source.
  if (sourceUrls.length < 1) {
    return NextResponse.json(
      { error: 'Cannot approve: incident has no source URL (legal guardrail #1).' },
      { status: 422 },
    )
  }

  // QA H3: never silently stamp incident_date with today. Prefer an
  // operator-supplied date, then the real source date from raw_content. If a
  // _date_fallback row reaches here with no real date, BLOCK the approval and
  // ask the operator to set it — rather than defaulting to today.
  const bodyDate    = (body as { incident_date?: string }).incident_date
  const sourceDate  = (bodyDate || rc.date || rc.incident_date) as string | undefined
  // Full-match + parseability: the old prefix regex let "2026-99-99…" through
  // to Postgres, which bounced it as a raw DB error.
  const candidateDate = sourceDate?.substring(0, 10)
  const incidentDate = candidateDate
    && /^\d{4}-\d{2}-\d{2}$/.test(candidateDate)
    && !isNaN(Date.parse(candidateDate))
    ? candidateDate
    : null
  if (!incidentDate) {
    return NextResponse.json(
      { error: 'No valid article date — set incident_date (YYYY-MM-DD) before approving (would otherwise default to today).' },
      { status: 422 },
    )
  }

  // Pin creation happens at publish: geocode via OneMap using priority order
  // block → POI → street. LLM-estimated coords in raw_content are never
  // trusted (they used to stack every pin at the Yishun centre point).
  // Geocode failure must not block approval — publish with no pin instead.
  const blockNumber = (rc.block_number as string | null) ?? null
  const areaName    = (rc.area_name    as string | null) ?? null
  let latitude: number | null = null
  let longitude: number | null = null
  try {
    const coords = await geocodeIncident(blockNumber, areaName, title)
    if (coords) [latitude, longitude] = coords
  } catch (e) {
    console.error('approve — geocoding failed (publishing without pin):', e)
  }

  // Build incident row
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
    pixel_art_url:       null,
    slug,
    seo_title:           (rc.seo_title       as string | null) ?? null,
    seo_description:     (rc.seo_description as string | null) ?? null,
    tags:                (rc.tags            as string[])      ?? [],
    agent_confidence:    item.agent_confidence ?? null,
    chaos_contribution:  (rc.chaos_contribution as number | null) ?? null,
    deaths:              (rc.deaths   as number | null) ?? null,
    injuries:            (rc.injuries as number | null) ?? null,
    // Preserve a multi-source story that was already assembled on the queue row
    // (e.g. an enriched draft moved back to the queue for review). Without these,
    // approve would publish a timeline-less incident.
    source_timeline:     (rc.source_timeline as unknown[])       ?? [],
    update_count:        (rc.update_count as number | null)      ?? 0,
    latest_source_role:  (rc.latest_source_role as string | null) ?? null,
    conclusion_type:     (rc.conclusion_type as string | null)    ?? null,
    is_milestone:        (rc.is_milestone as boolean)        ?? false,
    milestone_type:      (rc.milestone_type  as string | null) ?? null,
    milestone_value:     (rc.milestone_value as number | null) ?? null,
    incident_date:       incidentDate,
    is_published:        true,
    published_at:        new Date().toISOString(),
  }

  // Insert incident
  const { data: newIncident, error: incErr } = await supabase
    .from('incidents')
    .insert(incident)
    .select('id')
    .single()

  if (incErr) {
    console.error('Approve — insert incident:', incErr)
    const msg = incErr.code === '23505'
      ? 'Slug already exists — edit the title slightly and retry'
      : incErr.message
    return NextResponse.json({ error: msg }, { status: 409 })
  }

  // Detect edits → determine action type for training signal
  const changes: Record<string, unknown> = {}
  if (title       !== item.proposed_title)           changes.title          = { from: item.proposed_title,          to: title }
  if (summary     !== item.proposed_summary)         changes.summary        = { from: item.proposed_summary,        to: summary }
  if (classification !== item.proposed_classification) changes.classification = { from: item.proposed_classification, to: classification }
  if (severity    !== item.proposed_severity)        changes.severity       = { from: item.proposed_severity,       to: severity }
  if (pixel_art_prompt !== (rc.pixel_art_prompt as string ?? '')) {
    changes.pixel_art_prompt = { from: rc.pixel_art_prompt, to: pixel_art_prompt }
  }
  const action = Object.keys(changes).length > 0 ? 'edit_approve' : 'approve'

  // Create incident_links for any undismissed related incidents now that we
  // have a published incident ID for the "A" side of the link.
  const agentRelated = (rc.agent_related_incidents as Array<{
    incident_id: string; confidence: number; reason: string; link_type: string; dismissed?: boolean
  }>) ?? []
  for (const link of agentRelated) {
    if (link.dismissed) continue
    const { error: linkErr } = await supabase.from('incident_links').insert({
      incident_a:   newIncident.id,
      incident_b:   link.incident_id,
      link_type:    link.link_type ?? 'related',
      confidence:   link.confidence,
      agent_reason: link.reason ?? '',
    })
    // Unique-constraint (23505) is expected if already confirmed; log anything else.
    if (linkErr && linkErr.code !== '23505') console.error('approve — incident_link insert:', linkErr)
  }

  // QA H2: the queue-status update governs idempotency — if it fails, the
  // incident is published but the item stays 'pending' and can be re-approved
  // (slug conflict). Treat its failure as a hard error.
  // The .eq('status','pending') CAS closes the two-operator race: if a
  // concurrent approve/reject already processed this item, 0 rows match and
  // we compensate by deleting the incident we just inserted.
  const { data: claimed, error: queueErr } = await supabase.from('war_room_queue').update({
    status:       'approved',
    incident_id:  newIncident.id,
    processed_at: new Date().toISOString(),
  }).eq('id', id).eq('status', 'pending').select('id')
  if (queueErr) {
    console.error('approve — queue status update failed (incident published but queue still pending):', queueErr)
    return NextResponse.json(
      { error: 'Incident published but queue update failed — do not re-approve; reconcile manually.', incident_id: newIncident.id },
      { status: 500 },
    )
  }
  if (!claimed?.length) {
    // Lost the race — another request processed this item between our fetch
    // and this update. Remove the duplicate incident we created.
    const { error: undoErr } = await supabase.from('incidents').delete().eq('id', newIncident.id)
    if (undoErr) console.error('approve — failed to remove duplicate incident after lost race:', undoErr)
    return NextResponse.json(
      { error: 'Queue item was already processed by another request.' },
      { status: 409 },
    )
  }

  // Log training signal (telemetry — must not fail the request).
  // The queue_id/source_* fields are what make the row usable by the Learning
  // Loop: ops/learning_monitor.py::rebuild_source_reputation() tallies operator
  // approvals and rejections per source_url domain. A signal written without
  // source_url is invisible to it, so source_reputation stays empty forever and
  // ingestion/learning.py has no per-domain trust to nudge with.
  const { error: signalErr } = await supabase.from('training_signals').insert({
    incident_id:             newIncident.id,
    queue_id:                id,
    action,
    decision:                action === 'edit_approve' ? 'approve_with_edits' : 'approve',
    source_url:              item.source_url,
    source_name:             rc.source_name as string | undefined,
    source_type:             item.source_type,
    proposed_classification: item.proposed_classification,
    proposed_severity:       item.proposed_severity,
    original_draft:          item.proposed_summary,
    edited_draft:            action === 'edit_approve' ? summary : null,
    original_classification: item.proposed_classification,
    edited_classification:   action === 'edit_approve' ? classification : null,
    original_severity:       item.proposed_severity,
    edited_severity:         action === 'edit_approve' ? severity : null,
    operator_changes:        Object.keys(changes).length > 0 ? changes : null,
    agent_confidence_was:    item.agent_confidence,
  })
  if (signalErr) console.error('approve — training_signal insert failed (non-fatal):', signalErr)

  return NextResponse.json({ incident_id: newIncident.id, action })
}
