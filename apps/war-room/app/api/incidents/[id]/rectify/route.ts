import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { generateIncidentArt, rectifyIncidentArt } from '@/lib/artGenerate'
import { revalidateIncident } from '@/lib/revalidate'
import { RETRYABLE_STATUSES } from '@/lib/types'
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
  // RETRYABLE, not RECTIFIABLE: 'ok' is allowed here so an operator can reject
  // an image they just generated and re-roll it. Gating this on the queue's
  // filter meant the card's own buttons began answering "not in a rectifiable
  // state (ok)" the moment a render succeeded — you could make an image but
  // never reject one. Still blocks the terminal 'no_image_final', and
  // 'suppressed' was already rejected above.
  if (!RETRYABLE_STATUSES.includes(incident.image_status as never)) {
    return NextResponse.json(
      { error: `Incident is not in a rectifiable state (${incident.image_status ?? 'null'})` },
      { status: 422 },
    )
  }

  const prompt = (body.prompt ?? incident.image_prompt ?? '').trim().slice(0, MAX_PROMPT_CHARS)

  // The slug is both the R2 object key and the revalidation path.
  if (!/^[a-z0-9-]+$/.test(incident.slug ?? '')) {
    return NextResponse.json({ error: 'Incident slug is not URL-safe' }, { status: 422 })
  }

  // No prompt anywhere — neither typed by the operator nor stored on the row.
  //
  // This used to be a hard 422 ("No prompt to render"), which assumed a failed
  // incident always HAS a prompt to work from. It does not. The prompt is
  // composed inside the backend's /art/generate, and the War Room only ever
  // learns it from that response, so any failure at or before the HTTP boundary
  // stores image_prompt = NULL. That was every row in the table: a Cloud Run
  // IAM 403 (the War Room sends X-Ops-Token but no Authorization header) meant
  // FastAPI never ran, and the operator was left with an empty prompt box whose
  // "Retry as-is" button could only 422 forever.
  //
  // The fix is to run the FULL generate path instead: Haiku scene writer,
  // softening ladder, guardrail-#5 gate. That is precisely the work that never
  // happened at approve time, so "retry as-is" on a promptless row means "do
  // the thing that was skipped" — and it returns a final_prompt, which is
  // persisted below, so the next retry has something to edit.
  //
  // Composing the prompt HERE in TypeScript was the alternative and is rejected
  // for the reason artGenerate.ts already gives: it would put a second copy of
  // art/prompt_template.py and the suppression gate into a second language.
  const compose = prompt === ''
  const art = compose
    ? await generateIncidentArt({
        slug:      incident.slug,
        title:     incident.title,
        summary:   incident.summary,
        tags:      incident.tags,
      })
    : await rectifyIncidentArt({
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
      // art.final_prompt first: on the compose path `prompt` is empty, and the
      // composed prompt is the one thing that makes the next retry editable
      // rather than another blank box. `|| null` so a total failure leaves the
      // column NULL rather than an empty string that reads as "prompt exists".
      .update({
        image_status:   art.status,
        image_prompt:   art.final_prompt || prompt || null,
        image_attempts: attempts,
      })
      .eq('id', id)
      .in('image_status', RETRYABLE_STATUSES)
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
      // The prompt that was actually rendered from. Without this the client has
      // no way to fill its textarea after a compose, so the operator's next
      // edit silently REPLACES a 4000-character prompt with whatever short note
      // they typed instead of amending it.
      final_prompt: art.final_prompt || prompt || '',
    })
  }

  // Compare-and-set on the same status list: two operators on the same row, or
  // a suppression landing in between, must not both win.
  const { data: updated, error: updErr } = await supabase
    .from('incidents')
    .update({
      pixel_art_url:  art.url,
      image_status:   'ok',
      image_prompt:   art.final_prompt || prompt || null,
      image_attempts: attempts,
    })
    .eq('id', id)
    .in('image_status', RETRYABLE_STATUSES)
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
    // See the failure branch: the client needs the real prompt to put in the
    // box, otherwise "reject and re-roll with a tweak" rewrites the whole thing.
    final_prompt: art.final_prompt || prompt || '',
  })
}
