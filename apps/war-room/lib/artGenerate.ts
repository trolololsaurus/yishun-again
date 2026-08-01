// Bridge to the agents backend's image generator (Track B, B5).
//
// ## Why this is an HTTP call and not local code
//
// `pixel_art_url` has two writers — this approve route and
// `packages/agents/ops/auto_publish.py`. Both must set it or every
// auto-published incident stays imageless, which under the autonomy target is
// most of them (ART_PIPELINE.md §6.2). But the generator is Python: it owns the
// guardrail-#5 suppression gate, the softening ladder, the 16:9 centre-crop and
// the R2 upload with HEAD verification. Reimplementing that here would put two
// copies of the one check that must not fail into two languages, in two repos,
// drifting independently. So the War Room calls the backend instead.
//
// Generation happens BEFORE the incident INSERT so the URL is in the row from
// the start. The old design wrote back afterwards, which under Next.js ISR left
// the live page serving the placeholder until revalidation fired — a correct
// database row and an image that silently never appeared (§6.1).
//
// Publication is never blocked on this. Every failure path returns a result
// whose `url` is null and whose `status` says why; the caller inserts anyway
// and the frontend degrades to the placeholder and og-default.jpg.

// The vocabulary lives in types.ts, not here. This file used to declare its own
// union and it had already drifted — it was missing `no_image_final`, so the
// terminal state the operator can set was untypeable.
import { isImageStatus } from '@/lib/types'
import type { ImageAttempt, ImageStatus } from '@/lib/types'

export type { ImageAttempt, ImageStatus }

export interface ImageResult {
  url: string | null
  status: ImageStatus
  attempts: ImageAttempt[]
  final_prompt: string
}

// Generous: the ladder can spend three Gemini calls plus three Haiku rewrites,
// and this blocks the operator's approve click. Must exceed the backend's own
// IMAGE_TIMEOUT_S with margin, or the HTTP layer aborts mid-generation and
// leaves the operator with no feedback and a possible orphan object in R2.
const ART_TIMEOUT_MS = Number(process.env.ART_TIMEOUT_MS ?? 120_000)

const DISABLED: ImageResult = {
  url: null, status: 'pending', attempts: [], final_prompt: '',
}

/**
 * Render one incident's image. Never throws — a failure is a status, not an
 * exception, because nothing here is worth losing a publish over.
 *
 * Returns `status: 'pending'` when the backend is not configured, which is the
 * honest answer: nothing was attempted, so a future backfill should pick it up.
 * That is deliberately distinct from 'transient' (tried and failed) and from
 * 'suppressed' (guardrail #5 — must never be retried).
 */
export async function generateIncidentArt(incident: {
  slug: string
  title?: string | null
  summary?: string | null
  classification?: string | null
  severity?: number | null
  area_name?: string | null
  tags?: string[] | null
}): Promise<ImageResult> {
  const base  = (process.env.AGENTS_API_URL ?? '').replace(/\/+$/, '')
  const token = process.env.OPS_TOKEN ?? ''
  if (!base || !token) return DISABLED

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ART_TIMEOUT_MS)
  try {
    const res = await fetch(`${base}/art/generate`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Ops-Token': token },
      body:    JSON.stringify({ incident }),
      signal:  controller.signal,
    })
    if (!res.ok) {
      console.error(`art/generate — HTTP ${res.status}`)
      return { ...DISABLED, status: 'transient' }
    }
    return parseResult(await res.json())
  } catch (e) {
    // Includes the abort. An operator who waited two minutes should still get
    // their incident published.
    console.error('art/generate — call failed:', e)
    return { ...DISABLED, status: 'transient' }
  } finally {
    clearTimeout(timer)
  }
}


/** Coerce a backend response into an ImageResult. Validates the status rather
 *  than casting it — an unrecognised value from the other side of the process
 *  boundary must not reach the database as though it were a known state. */
function parseResult(body: unknown): ImageResult {
  const b = (body ?? {}) as Partial<ImageResult>
  return {
    url:          typeof b.url === 'string' ? b.url : null,
    status:       isImageStatus(b.status) ? b.status : 'transient',
    attempts:     Array.isArray(b.attempts) ? b.attempts : [],
    final_prompt: typeof b.final_prompt === 'string' ? b.final_prompt : '',
  }
}

// One attempt, not three, and no Haiku rewrites — so the 120 s sized for the
// full ladder is far too long to leave an operator staring at a spinner.
const RECTIFY_TIMEOUT_MS = Number(process.env.RECTIFY_TIMEOUT_MS ?? 45_000)

/**
 * Re-render one incident from an operator-edited prompt (B4b).
 *
 * Single attempt, no softening ladder, no per-pass budget: the operator has
 * already made the editorial judgement that the ladder exists to automate.
 * Never throws — a failure is a status the card can display.
 */
export async function rectifyIncidentArt(args: {
  slug: string
  prompt: string
  incident?: { title?: string | null; summary?: string | null; tags?: string[] | null }
}): Promise<ImageResult> {
  const base  = (process.env.AGENTS_API_URL ?? '').replace(/\/+$/, '')
  const token = process.env.OPS_TOKEN ?? ''
  if (!base || !token) {
    return { ...DISABLED, final_prompt: args.prompt }
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), RECTIFY_TIMEOUT_MS)
  try {
    const res = await fetch(`${base}/art/rectify`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Ops-Token': token },
      body:    JSON.stringify({ slug: args.slug, prompt: args.prompt, incident: args.incident }),
      signal:  controller.signal,
    })
    // 422 is the guardrail-#5 refusal. It must NOT collapse into 'transient',
    // or the card would offer a retry button on a suppression.
    if (res.status === 422) {
      return { url: null, status: 'suppressed', attempts: [], final_prompt: args.prompt }
    }
    if (!res.ok) {
      console.error(`art/rectify — HTTP ${res.status}`)
      return { url: null, status: 'transient', attempts: [], final_prompt: args.prompt }
    }
    return parseResult(await res.json())
  } catch (e) {
    console.error('art/rectify — call failed:', e)
    return { url: null, status: 'transient', attempts: [], final_prompt: args.prompt }
  } finally {
    clearTimeout(timer)
  }
}
