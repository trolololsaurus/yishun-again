import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { rectifyIncidentArt } from '@/lib/artGenerate'
import { revalidateIncident } from '@/lib/revalidate'
import { RECTIFIABLE_STATUSES } from '@/lib/types'
import type { ImageAttempt } from '@/lib/types'

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

// Vercel terminates a function at the plan default (10-15 s) unless a route
// declares otherwise, and it does so regardless of any AbortController the
// handler set up. Without this the 40 s budget in rectifyIncidentArt could never
// be reached: the platform killed the request first and the operator saw a 504
// with no attempt recorded. 60 s is the Hobby ceiling and well inside Pro's, so
// it is safe on either plan. Keep it strictly ABOVE RECTIFY_TIMEOUT_MS so the
// in-handler timeout is the one that fires — it returns a usable status, where
// a platform kill returns nothing at all.
export const maxDuration = 60

// Attempt history is for the operator's edit-and-retry loop, so only the recent
// ones carry information. Uncapped it grows by up to MAX_PROMPT_CHARS per retry
// inside a JSONB column on a published row, and /rectify loads 200 such rows at
// once — a handful of stubborn incidents would dominate the page payload.
const MAX_ATTEMPTS_KEPT = 10

/**
 * Append, keep only the most recent, then renumber from 1.
 *
 * `prior` is whatever is in the JSONB column, so it is typed `unknown[]` and
 * normalised rather than trusted — the column has no schema and a non-object
 * entry would otherwise spread into `{...a}` and silently produce `{n: 1}`.
 */
function mergeAttempts(
  prior: unknown[],
  fresh: readonly ImageAttempt[],
): Array<Record<string, unknown>> {
  return [...prior, ...fresh]
    .filter((a): a is object => a !== null && typeof a === 'object')
    .slice(-MAX_ATTEMPTS_KEPT)
    .map((a, i) => ({ ...(a as Record<string, unknown>), n: i + 1 }))
}

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
  const prior = (incident.image_attempts ?? []) as unknown[]
  const attempts = mergeAttempts(prior, art.attempts)

  if (art.status !== 'ok') {
    // Persist the failure anyway. Discarding the new refusal reason would make
    // the edit-and-retry loop unusable — the operator would be guessing blind.
    //
    // The result is CHECKED, like the success path and like the no-image route.
    // This previously fired and forgot: a rejected write (CAS miss, or any DB
    // error) still answered `ok: true` with the new attempt in the body, so the
    // operator read a refusal reason off the screen that was never stored and
    // was gone on reload — the exact failure this branch exists to prevent.
    const { data: failRow, error: failErr } = await supabase
      .from('incidents')
      .update({ image_status: art.status, image_prompt: prompt, image_attempts: attempts })
      .eq('id', id)
      .in('image_status', RECTIFIABLE_STATUSES)
      .select('id')

    if (failErr) {
      console.error('rectify — failure-state write failed:', failErr)
      return NextResponse.json({ error: failErr.message }, { status: 500 })
    }
    if (!failRow?.length) {
      return NextResponse.json(
        { error: 'Incident state changed — reload and retry.' },
        { status: 409 },
      )
    }

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
