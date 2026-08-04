import Link from 'next/link'
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
// `?include=ok` also lists incidents that already HAVE an image, so a picture
// the operator dislikes can be re-rolled later.
//
// Without this, "reject and regenerate" only worked inside the session that
// produced the image: a successful render leaves RECTIFIABLE_STATUSES, so on
// the next page load the card was gone and the only route back to a bad image
// was editing the database by hand. Off by default — a working image is not an
// outstanding task and must not dilute the failure queue.
//
// `suppressed` is STILL excluded by construction in both branches. Guardrail #5
// is not operator-overridable and no query here may widen to `.neq(...)`.
export default async function RectifyPage(
  props: { searchParams: Promise<{ include?: string }> },
) {
  const includeOk = (await props.searchParams).include === 'ok'
  const statuses = includeOk
    ? [...RECTIFIABLE_STATUSES, 'ok']
    : [...RECTIFIABLE_STATUSES]

  const { data, error } = await supabase
    .from('incidents')
    .select(RECTIFY_COLUMNS)
    .eq('is_published', true)
    .in('image_status', statuses)
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
      <p className="font-body text-text-secondary text-sm mb-2">
        {includeOk
          ? 'Published incidents whose image failed, plus those that already have one so you can replace it. Every incident here is live and readable regardless.'
          : 'Published incidents whose image failed. They are live and readable already — only the picture is missing.'}
        {' '}Suppressed incidents (guardrail&nbsp;#5) never appear here.
      </p>

      <p className="font-body text-sm mb-6">
        {includeOk ? (
          <>
            <span className="text-text-secondary">Showing incidents that already have an image too. </span>
            <Link href="/rectify" className="text-yellow hover:underline">
              Show only failures
            </Link>
          </>
        ) : (
          <>
            <span className="text-text-secondary">Want to replace a picture you don’t like? </span>
            <Link href="/rectify?include=ok" className="text-yellow hover:underline">
              Include incidents that already have an image
            </Link>
          </>
        )}
      </p>

      {items.length === 0
        ? (
          <p className="font-body text-text-secondary">
            {includeOk ? 'No published incidents have an image yet.' : 'Nothing to rectify.'}
          </p>
        )
        : <RectifyList initialItems={items} />}
    </div>
  )
}
