import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID, slugify } from '@/lib/utils'
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
  const title           = (body.title          ?? '').slice(0, 90).trim()
  const summary         = (body.summary         ?? '').trim()
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
  const sourceUrls  = (rc.source_urls as string[]) ?? [item.source_url]
  const slug        = (item.proposed_slug ?? (rc.slug as string) ?? slugify(title))

  // Read incident_date from raw_content — set by scraper/backfill agent
  // Falls back to today only if raw_content has no usable date
  const sourceDate  = (rc.date || rc.incident_date) as string | undefined
  const incidentDate = sourceDate && /^\d{4}-\d{2}-\d{2}/.test(sourceDate)
    ? sourceDate.substring(0, 10)
    : new Date().toISOString().substring(0, 10)

  // Build incident row
  const incident = {
    title,
    summary,
    classification,
    severity,
    block_number:        (rc.block_number  as string  | null) ?? null,
    area_name:           (rc.area_name     as string  | null) ?? null,
    latitude:            (rc.latitude      as number  | null) ?? null,
    longitude:           (rc.longitude     as number  | null) ?? null,
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
    await supabase.from('incident_links').insert({
      incident_a:   newIncident.id,
      incident_b:   link.incident_id,
      link_type:    link.link_type ?? 'related',
      confidence:   link.confidence,
      agent_reason: link.reason ?? '',
    })
    // Unique-constraint errors are ignored — fine if this link was already confirmed
  }

  // Update queue status
  await supabase.from('war_room_queue').update({
    status:       'approved',
    incident_id:  newIncident.id,
    processed_at: new Date().toISOString(),
  }).eq('id', id)

  // Log training signal
  await supabase.from('training_signals').insert({
    incident_id:             newIncident.id,
    action,
    original_draft:          item.proposed_summary,
    edited_draft:            action === 'edit_approve' ? summary : null,
    original_classification: item.proposed_classification,
    edited_classification:   action === 'edit_approve' ? classification : null,
    original_severity:       item.proposed_severity,
    edited_severity:         action === 'edit_approve' ? severity : null,
    operator_changes:        Object.keys(changes).length > 0 ? changes : null,
    agent_confidence_was:    item.agent_confidence,
  })

  return NextResponse.json({ incident_id: newIncident.id, action })
}
