import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { buildArtPrompt, NEGATIVE_PROMPT } from '@/lib/artPrompt'

// Operator-only: shows what the art agent would send to SDXL for a published
// incident, plus whatever prompt the pipeline proposed at draft time.
//
// Two different things, deliberately returned separately:
//  - `prompt` / `negative_prompt` — computed live from the incident's current
//    classification + area_name. This is what Modal would actually use, and it
//    tracks operator edits (re-classify an incident and the prompt changes).
//  - `proposed` — the `pixel_art_prompt` the agent wrote onto the originating
//    queue row. Stage 2 stopped generating this after the Haiku switch, so it
//    is null for anything drafted since; older rows still carry it. It is
//    history, not what would be generated.
//
// `incidents` has no pixel_art_prompt column, which is why the second one has
// to be read back off `war_room_queue`.
export async function GET(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  const { data: incident, error } = await supabase
    .from('incidents')
    .select('id, title, slug, classification, custom_label, area_name, block_number, pixel_art_url')
    .eq('id', id)
    .single()

  if (error || !incident) {
    return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
  }

  // Supplementary — a queue lookup failure must not cost the operator the
  // effective prompt, which is the part they came for.
  let proposed: { prompt: string; queue_id: string; created_at: string } | null = null
  const { data: queueRows, error: queueErr } = await supabase
    .from('war_room_queue')
    .select('id, created_at, proposed_pixel_prompt, raw_content')
    .eq('incident_id', id)
    .order('created_at', { ascending: false })
    .limit(1)

  if (queueErr) {
    console.error('GET art-prompt — queue lookup failed (non-fatal):', queueErr)
  } else if (queueRows?.length) {
    const row = queueRows[0]
    const rc  = (row.raw_content ?? {}) as Record<string, unknown>
    // raw_content is the richer copy; proposed_pixel_prompt is the projection
    // consolidation/queue_row.py writes alongside it. Prefer whichever is set.
    const text = ((rc.pixel_art_prompt as string) || row.proposed_pixel_prompt || '').trim()
    if (text) proposed = { prompt: text, queue_id: row.id, created_at: row.created_at }
  }

  return NextResponse.json({
    incident,
    prompt:          buildArtPrompt(incident.classification, incident.area_name),
    negative_prompt: NEGATIVE_PROMPT,
    proposed,
  })
}
