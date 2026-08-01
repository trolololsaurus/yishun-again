import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { validateUUID } from '@/lib/utils'
import { RECTIFIABLE_STATUSES } from '@/lib/types'

// Operator rectification (Track B, B4b) — action 3, "publish without image".
//
// Marks the incident TERMINAL at `no_image_final`: the operator has looked at
// it and decided the placeholder is the right answer. A future backfill must
// never retry it, exactly as it must never retry a `suppressed` row. Without a
// terminal state, a backfill spread across passes is an infinite retry loop —
// just slower and more expensive.
//
// No image call and no revalidation: `pixel_art_url` is unchanged (still null),
// so nothing the live page renders has changed.
export async function POST(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) return NextResponse.json({ error: 'Invalid ID' }, { status: 400 })

  // Guarded by the SAME status list as the queue itself. This is what makes it
  // impossible to flip a guardrail-#5 suppression into `no_image_final` — which
  // would look harmless and would quietly convert "never generate this" into
  // "an operator decided against it", losing the reason forever.
  const { data, error } = await supabase
    .from('incidents')
    .update({ image_status: 'no_image_final' })
    .eq('id', id)
    .in('image_status', RECTIFIABLE_STATUSES)
    .select('id')

  if (error) {
    console.error('no-image — update failed:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
  if (!data?.length) {
    return NextResponse.json(
      { error: 'Incident is not in a rectifiable state' },
      { status: 409 },
    )
  }

  return NextResponse.json({ ok: true, status: 'no_image_final' })
}
