import { supabase } from '@/lib/supabase'
import { RectifyList } from '@/components/RectifyCard'
import { RECTIFIABLE_STATUSES, RECTIFY_COLUMNS } from '@/lib/types'
import type { RectifyItem } from '@/lib/types'

export const revalidate = 0

// Image rectification queue (Track B, B4b).
//
// Published incidents whose image generation failed in a way a human can do
// something about. Publication was never blocked on the image — the frontend
// degrades to the placeholder — so everything listed here is already live and
// readable; only the picture is missing.
//
// `suppressed` is excluded BY CONSTRUCTION via `.in(RECTIFIABLE_STATUSES)`, not
// by filtering it out afterwards. Guardrail #5 is not operator-overridable and
// there is deliberately no control anywhere in this view that could set or
// clear it. Do not widen this query to `.neq('image_status', 'ok')`.
//
// `pending` is also excluded, and that is worth saying out loud because it is
// the LARGEST imageless cohort: migration 014 backfills every pre-existing
// incident to `pending`, and both writers use it whenever art generation is
// switched off or unconfigured. Those were never attempted. Retrying them one
// at a time through this UI is the wrong tool — that is a backfill job.
export default async function RectifyPage() {
  const { data, error } = await supabase
    .from('incidents')
    .select(RECTIFY_COLUMNS)
    .eq('is_published', true)
    .in('image_status', RECTIFIABLE_STATUSES)
    .order('published_at', { ascending: false })
    .order('id', { ascending: false })
    .limit(200)

  if (error) {
    return (
      <div>
        <h1 className="font-body font-bold text-yellow text-lg mb-6">IMAGE RECTIFICATION</h1>
        <p className="font-body text-red">
          Could not load the queue: {error.message}
        </p>
        <p className="font-body text-text-secondary text-sm mt-2">
          If this mentions <code>image_status</code>, migration{' '}
          <code>014_image_status.sql</code> has not been applied yet.
        </p>
      </div>
    )
  }

  const items = (data ?? []) as unknown as RectifyItem[]

  return (
    <div>
      <h1 className="font-body font-bold text-yellow text-lg mb-2">
        IMAGE RECTIFICATION <span className="text-text-secondary">({items.length})</span>
      </h1>
      <p className="font-body text-text-secondary text-sm mb-6">
        Published incidents whose image failed. They are live and readable already — only the
        picture is missing. Suppressed incidents (guardrail&nbsp;#5) never appear here.
      </p>

      {items.length === 0
        ? <p className="font-body text-text-secondary">Nothing to rectify.</p>
        : <RectifyList initialItems={items} />}
    </div>
  )
}
