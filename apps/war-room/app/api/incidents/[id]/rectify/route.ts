import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { rectifyIncidentArt } from '@/lib/artGenerate'
import { revalidateIncident } from '@/lib/revalidate'
import { RECTIFIABLE_STATUSES } from '@/lib/types'

// Operator rectification (Track B, B4b) — actions 1 and 2.
//
// "Retry with edits" and "retry as-is" are the same operation with a different
// prompt source, so they are one endpoint: a body with `prompt` uses it, a body
// without falls back to the stored `image_prompt`.
//
// Single attempt, no softening ladder. The ladder exists because the safety
// filter is deterministic, so an unattended retry has to change the prompt to
// be worth anything. An operator who has just rewritten the prompt has already
// made that judgement — softening it again behind their back would render
// something they did not ask for and attribute it to them.
const MAX_PROMPT_CHARS = 8000

export async function POST(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // "Retry as-is" sends no body at all.
  let body: { prompt?: string } = {}
  try {
    body = await request.json()
  } catch {
    body = {}
  }

  const { data: incident, error: fetchErr } = await supabase
    .from('incidents')
    .select('id, slug, title, summary, tags, image_status, image_prompt, image_attempts')
    .eq('id', id)
    .single()

  if (fetchErr || !incident) {
    return NextResponse.json({ error: 'Incident not found' }, { status: 404 })
  }

  // Guardrail #5, server side. The queue page excludes suppressed rows by
  // construction, but the one check that must not fail cannot rest on a list
  // filter staying correct — and there is deliberately no override for it.
  if (incident.image_status === 'suppressed') {
    return NextResponse.json(
      { error: 'Suppressed under guardrail #5 — not rectifiable.' },
      { status: 422 },
    )
  }
  // Also blocks 'ok' and the terminal 'no_image_final'.
  if (!RECTIFIABLE_STATUSES.includes(incident.image_status as never)) {
    return NextResponse.json(
      { error: `Incident is not in a rectifiable state (${incident.image_status ?? 'null'})` },
      { status: 422 },
    )
  }

  const prompt = (body.prompt ?? incident.image_prompt ?? '').trim().slice(0, MAX_PROMPT_CHARS)
  if (!prompt) {
    return NextResponse.json({ error: 'No prompt to render' }, { status: 422 })
  }
  // The slug is both the R2 object key and the revalidation path.
  if (!/^[a-z0-9-]+$/.test(incident.slug ?? '')) {
    return NextResponse.json({ error: 'Incident slug is not URL-safe' }, { status: 422 })
  }

  const art = await rectifyIncidentArt({
    slug:   incident.slug,
    prompt,
    incident: { title: incident.title, summary: incident.summary, tags: incident.tags },
  })

  // Append and renumber. render_prompt always reports n=1, so replacing would
  // erase the history the operator is working from — the whole point of the
  // view is showing what has already been tried and refused.
  const prior = (incident.image_attempts ?? []) as Array<Record<string, unknown>>
  const attempts = [...prior, ...art.attempts].map((a, i) => ({ ...a, n: i + 1 }))

  if (art.status !== 'ok') {
    // Persist the failure anyway. Discarding the new refusal reason would make
    // the edit-and-retry loop unusable — the operator would be guessing blind.
    await supabase
      .from('incidents')
      .update({ image_status: art.status, image_prompt: prompt, image_attempts: attempts })
      .eq('id', id)
      .in('image_status', RECTIFIABLE_STATUSES)

    return NextResponse.json({
      ok: true, status: art.status, url: null, attempts, revalidated: false,
    })
  }

  // Compare-and-set on the same status list: two operators on the same row, or
  // a suppression landing in between, must not both win.
  const { data: updated, error: updErr } = await supabase
    .from('incidents')
    .update({
      pixel_art_url:  art.url,
      image_status:   'ok',
      image_prompt:   art.final_prompt || prompt,
      image_attempts: attempts,
    })
    .eq('id', id)
    .in('image_status', RECTIFIABLE_STATUSES)
    .select('id, slug')

  if (updErr) {
    console.error('rectify — update failed:', updErr)
    return NextResponse.json({ error: updErr.message }, { status: 500 })
  }
  if (!updated?.length) {
    return NextResponse.json(
      { error: 'Incident state changed — reload and retry.' },
      { status: 409 },
    )
  }

  // Mandatory, and awaited: an unawaited fetch is frozen when the serverless
  // function returns. Never fatal — the row is already correct — but the
  // outcome is reported so a silent no-op cannot masquerade as success.
  const rv = await revalidateIncident(incident.slug)
  if (!rv.ok) console.error('rectify — revalidation failed:', rv.reason)

  return NextResponse.json({
    ok: true,
    status: 'ok',
    url: art.url,
    attempts,
    revalidated: rv.ok,
    revalidate_reason: rv.reason,
  })
}
