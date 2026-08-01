import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'

// Operator-only: what was actually sent to the image model for this incident,
// and what happened.
//
// Rewritten for the Gemini pipeline (Track B, B4). It previously rebuilt an
// SDXL prompt live from `lib/artPrompt.ts` and returned a `negative_prompt`
// alongside it. Both are gone:
//
//  - The SDXL builder derived its prompt from classification + area_name only,
//    ignoring the incident entirely, so every dagger on the same street produced
//    a byte-identical string (ART_PIPELINE.md §7.3). It described a pipeline
//    that no longer exists.
//  - Gemini has NO negative prompt parameter. That field had no destination —
//    exclusions are stated inline in the prose (§3.1).
//
// What replaces them is the real thing: `incidents.image_prompt` and
// `image_attempts`, written at publish time by whichever writer created the row.
// This is history rather than a live rebuild, which is correct — it is the
// prompt that produced the picture on screen, not one that might have.
export async function GET(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  const { data: incident, error } = await supabase
    .from('incidents')
    .select('id, title, slug, classification, custom_label, area_name, block_number, pixel_art_url, image_status, image_prompt, image_attempts')
    .eq('id', id)
    .single()

  if (error || !incident) {
    return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
  }

  return NextResponse.json({
    incident,
    prompt:   incident.image_prompt ?? null,
    status:   incident.image_status ?? 'pending',
    attempts: incident.image_attempts ?? [],
  })
}
