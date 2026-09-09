import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { revalidateIncident } from '@/lib/revalidate'
import { RETRYABLE_STATUSES } from '@/lib/types'
import type { ImageVersion } from '@/lib/types'

// Image revert (Track B, B4b — migration 025).
//
// Operator rectification keeps each superseded image alive at its own R2 key and
// lists it in incidents.image_history. Reverting picks one of those versions and
// makes it live again — a pure pointer SWAP, no re-render, no byte copy: the
// image being restored leaves the history and the one being displaced joins it
// (with a fresh created_at, so its 8h TTL clock starts now). The operator can
// therefore flip between versions freely until ops/image_cleanup.py expires the
// older ones.
//
// Body: `{url}` — which version to restore. Omitted → the most recent.
//
// This never generates or deletes an image, so it does not touch the safety
// path. Guardrail #5 is still refused defensively: a suppressed row has no
// history, but the check must not rest on that staying true.

/** R2 key behind a public art URL (mirrors the rectify route). */
function keyFromArtUrl(url: string): string {
  try {
    return new URL(url).pathname.replace(/^\/+/, '')
  } catch {
    return url.split('?')[0].replace(/^\/+/, '')
  }
}

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  let body: { url?: string } = {}
  try {
    body = await request.json()
  } catch {
    body = {}
  }

  const { data: incident, error: fetchErr } = await supabase
    .from('incidents')
    .select('id, slug, image_status, pixel_art_url, image_prompt, image_history')
    .eq('id', id)
    .single()

  if (fetchErr || !incident) {
    return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
  }

  if (incident.image_status === 'suppressed') {
    return NextResponse.json(
      { error: 'Suppressed under guardrail #5 — not rectifiable.' },
      { status: 422 },
    )
  }

  const history: ImageVersion[] = Array.isArray(incident.image_history) ? incident.image_history : []
  if (history.length === 0) {
    return NextResponse.json({ error: 'No previous image to revert to.' }, { status: 422 })
  }

  // The requested version, or the most recent if none named. `at(-1)` because
  // history is stored newest-last.
  const target = body.url
    ? history.find(v => v.url === body.url)
    : history.at(-1)
  if (!target) {
    return NextResponse.json({ error: 'That image is no longer available to revert to.' }, { status: 422 })
  }

  if (!/^[a-z0-9-]+$/.test(incident.slug ?? '')) {
    return NextResponse.json({ error: 'Incident slug is not URL-safe' }, { status: 422 })
  }

  // Drop the restored version from history; add the currently-live one (its TTL
  // starts now). The live image is never left in history, so the cleanup sweep
  // can never delete the picture a page is serving.
  const nextHistory: ImageVersion[] = history.filter(v => v.url !== target.url)
  if (incident.pixel_art_url) {
    nextHistory.push({
      key:        keyFromArtUrl(incident.pixel_art_url),
      url:        incident.pixel_art_url,
      prompt:     incident.image_prompt ?? '',
      created_at: new Date().toISOString(),
    })
  }

  // CAS on the retryable set — the same guard the rectify route uses, so a
  // suppression or concurrent edit landing in between cannot be overwritten.
  const { data: updated, error: updErr } = await supabase
    .from('incidents')
    .update({
      pixel_art_url: target.url,
      image_prompt:  target.prompt,
      image_status:  'ok',
      image_history: nextHistory,
    })
    .eq('id', id)
    .in('image_status', RETRYABLE_STATUSES)
    .select('id')

  if (updErr) {
    console.error('revert-image — update failed:', updErr)
    return NextResponse.json({ error: updErr.message }, { status: 500 })
  }
  if (!updated?.length) {
    return NextResponse.json(
      { error: 'Incident state changed — reload and retry.' },
      { status: 409 },
    )
  }

  const rv = await revalidateIncident(incident.slug)
  if (!rv.ok) console.error('revert-image — revalidation failed:', rv.reason)

  return NextResponse.json({
    ok: true,
    status: 'ok',
    url: target.url,
    image_prompt: target.prompt ?? '',
    image_history: nextHistory,
    revalidated: rv.ok,
    revalidate_reason: rv.reason,
  })
}
