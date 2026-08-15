import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { RectifyList } from '@/components/RectifyCard'
import { RECTIFIABLE_STATUSES, RETRYABLE_STATUSES, RECTIFY_COLUMNS, rectifyBlockReason } from '@/lib/types'
import { incidentRefFromInput, canonicalUrl } from '@/lib/utils'
import type { RectifyItem, RectifyBlockReason } from '@/lib/types'

export const revalidate = 0

// Image rectification queue (Track B, B4b).
//
// Published incidents whose image generation failed in a way a human can do
// something about. Publication was never blocked on the image — the frontend
// degrades to the placeholder — so everything listed here is already live and
// readable; only the picture is missing.
//
// `suppressed` is excluded from the ACTIONABLE queue BY CONSTRUCTION via
// `.in(...)` on an allowlist, not by filtering it out afterwards. Guardrail #5
// is not operator-overridable and there is deliberately no control anywhere in
// this view that could set or clear it. Do not widen any query here to
// `.neq('image_status', 'ok')` — INCLUDING the lookup path, which is the newest
// way to reach a single row.
//
// Since 2026-08-05 those rows ARE listed, read-only, by NoImagePanel at the
// bottom of the page. That is a separate query rendering no controls, so the
// rule above is unchanged: they still cannot be retried, regenerated, or have
// their status cleared. Listing them fixed a different problem — five incidents
// had no picture and nothing in the UI said so or why, which is exactly the
// silent state this codebase refuses to accept elsewhere.
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
// ── ?url= — one incident, found by what the operator was looking at ──
// A hallucinated image is spotted on the PAGE, not in a queue, and 161 of the
// 167 published incidents are `ok` and therefore invisible here by default.
// Scrolling `?include=ok` to find the one is the wrong shape of work, so the
// box at the top takes the public URL (or the War Room preview URL, or a bare
// slug, or the SOURCE article's URL) and goes straight to that row.
// It filters on RETRYABLE_STATUSES — the set `lib/types` already defines as
// "which rows may an operator act on" — so `suppressed` stays unreachable and
// the lookup can never act on something the card's own buttons would refuse.
export default async function RectifyPage(
  props: { searchParams: Promise<{ include?: string; url?: string }> },
) {
  const params    = await props.searchParams
  const query     = (params.url ?? '').trim()
  const includeOk = params.include === 'ok'

  if (query) return <LookupView query={query} />

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

  if (error) return <QueueError message={error.message} />

  const items = (data ?? []) as unknown as RectifyItem[]

  return (
    <div>
      <Header count={items.length} />
      <LookupForm />

      <p className="text-text-secondary text-sm mb-2">
        {includeOk
          ? 'Published incidents whose image failed, plus those that already have one so you can replace it. Every incident here is live and readable regardless.'
          : 'Published incidents whose image failed. They are live and readable already — only the picture is missing.'}
        {' '}Suppressed incidents (guardrail&nbsp;#5) are never actionable here — they are
        listed read-only at the bottom so they are not invisible.
      </p>

      <p className="text-sm mb-6">
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
          <p className="text-text-secondary">
            {includeOk ? 'No published incidents have an image yet.' : 'Nothing to rectify.'}
          </p>
        )
        : <RectifyList initialItems={items} />}

      <NoImagePanel />
    </div>
  )
}

// ── Incidents that have no image and are not getting one ─────────────────────
//
// Read-only, and a SEPARATE query from the queue above on purpose.
//
// The queue is an allowlist (`RECTIFIABLE_STATUSES`) and stays one — the note
// at the top of this file says not to widen it, and this does not widen it.
// This is an independent read that renders NO controls: no retry, no
// regenerate, no button of any kind. Nothing here can set or clear
// `suppressed`, so guardrail #5 remains exactly as non-overridable as before.
//
// It exists because "this incident has no picture, and nothing in the UI says
// so or says why" is its own failure mode — the same silent-state problem this
// codebase already refuses to tolerate for revalidation and for rectify
// failures. Until now the only way to learn these rows existed was to query the
// database by hand, which is how they were found in the first place.
//
// `pending` is deliberately NOT listed: it is a backfill job, not an editorial
// outcome, and after the 2026-08-05 backfill there are none left.
async function NoImagePanel() {
  const { data } = await supabase
    .from('incidents')
    .select('slug, title, image_status')
    .eq('is_published', true)
    .in('image_status', ['suppressed', 'no_image_final'])
    .order('published_at', { ascending: false })
    .limit(200)

  const rows = data ?? []
  if (rows.length === 0) return null

  return (
    <section className="mt-10 border-t border-border pt-6">
      <h2 className="font-bold text-text-secondary uppercase tracking-widest mb-2 text-xs">
        No image, and not getting one ({rows.length})
      </h2>
      <p className="text-text-secondary text-sm mb-4">
        Listed so they are not invisible. These are live and readable like every other
        incident — only the picture is withheld. There are deliberately no controls here:
        guardrail&nbsp;#5 is not operator-overridable, and{' '}
        <code>no_image_final</code> is a terminal choice already made.
      </p>
      <ul className="space-y-1">
        {rows.map(row => (
          <li key={row.slug} className="text-sm flex flex-wrap gap-2 items-baseline">
            <span
              className="uppercase tracking-widest flex-none text-text-secondary text-xs"
              style={{ minWidth: '8rem' }}
              title={row.image_status === 'suppressed'
                ? 'Guardrail #5 — suicide or self-harm content'
                : 'An operator chose to publish this without an image'}
            >
              {row.image_status === 'suppressed' ? 'guardrail #5' : 'no image (final)'}
            </span>
            <Link
              href={`/incidents/${row.slug}`}
              className="text-text-primary hover:text-yellow"
            >
              {row.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── The lookup ───────────────────────────────────────────────────────────────

async function LookupView({ query }: { query: string }) {
  const ref = incidentRefFromInput(query)

  let slug = ref.slug

  // A source article URL: match it against the incidents' own citations.
  // Compared through `canonicalUrl` because the operator copies the clean URL
  // out of the address bar while the scraper stored whatever it was served
  // (`…?ref=home-trending-now`), and an exact `.contains()` would miss.
  if (!slug && ref.sourceUrl) {
    const { data } = await supabase
      .from('incidents')
      .select('slug, source_urls')
      .eq('is_published', true)
      .limit(1000)
    const wanted = canonicalUrl(ref.sourceUrl)
    slug = (data ?? []).find(
      row => (row.source_urls ?? []).some((u: string) => canonicalUrl(u) === wanted),
    )?.slug ?? null
  }

  if (!slug) return <NotFound query={query} reason="no_match" />

  const { data, error } = await supabase
    .from('incidents')
    .select(RECTIFY_COLUMNS)
    .eq('slug', slug)
    .eq('is_published', true)
    .in('image_status', [...RETRYABLE_STATUSES])
    .limit(1)

  if (error) return <QueueError message={error.message} />

  const items = (data ?? []) as unknown as RectifyItem[]
  if (items.length > 0) {
    return (
      <div>
        <Header count={items.length} />
        <LookupForm value={query} />
        <p className="text-text-secondary text-sm mb-6">
          Showing the one incident matching that link.{' '}
          <Link href="/rectify" className="text-yellow hover:underline">Back to the queue</Link>
        </p>
        <RectifyList initialItems={items} />
      </div>
    )
  }

  // Nothing actionable. Say WHY — "not found" would send the operator hunting
  // for a row that is sitting right there, blocked or unpublished. This read
  // selects three scalar columns and renders no controls, and the reason is
  // classified in lib/types (which owns the status vocabulary), so this path
  // cannot become a way around guardrail #5.
  const { data: why } = await supabase
    .from('incidents')
    .select('slug, is_published, image_status')
    .eq('slug', slug)
    .limit(1)

  return <NotFound query={query} reason={rectifyBlockReason(why?.[0])} slug={slug} />
}

const BLOCK_MESSAGE: Record<RectifyBlockReason, string> = {
  guardrail5:
    'Image generation is blocked for this incident by guardrail #5 (suicide or self-harm). ' +
    'That is not operator-overridable, and there is no control here that could clear it.',
  draft:
    'That incident exists but is not published. Rectification only covers live incidents.',
  no_match:
    'No published incident matches that link. Paste the incident page URL, the source article URL, or the slug.',
  not_actionable:
    'That incident’s image is in a state the rectify route will not act on. ' +
    '"pending" means art generation was never attempted — that is a backfill job, not a ' +
    'rectification — and "no_image_final" is the terminal choice an operator already made.',
}

function NotFound({ query, reason, slug }: { query: string; reason: RectifyBlockReason; slug?: string }) {
  const message = BLOCK_MESSAGE[reason]

  return (
    <div>
      <Header count={0} />
      <LookupForm value={query} />
      <p className="text-red text-sm mb-2">{message}</p>
      {slug && (
        <p className="text-text-secondary text-sm mb-6">
          Matched slug: <code>{slug}</code>
        </p>
      )}
      <p className="text-sm">
        <Link href="/rectify" className="text-yellow hover:underline">Back to the queue</Link>
      </p>
    </div>
  )
}

// ── Shared chrome ────────────────────────────────────────────────────────────

function Header({ count }: { count: number }) {
  return (
    <h1 className="font-bold text-yellow text-lg mb-2">
      IMAGE RECTIFICATION <span className="text-text-secondary">({count})</span>
    </h1>
  )
}

/** A plain GET form — no client component, and it works without JS. */
function LookupForm({ value = '' }: { value?: string }) {
  return (
    <form method="get" action="/rectify" className="mb-4 flex gap-2 flex-wrap items-center">
      <input
        type="text"
        name="url"
        defaultValue={value}
        placeholder="Paste an incident URL, a source article URL, or a slug"
        className="flex-1 min-w-[22rem] bg-surface border border-border px-2 py-1 text-sm text-text-primary placeholder:text-text-secondary"
      />
      <button
        type="submit"
        className="px-3 py-1 border border-border text-sm text-yellow hover:bg-surface"
      >
        Find
      </button>
      {value && (
        <Link href="/rectify" className="text-sm text-text-secondary hover:text-text-primary">
          Clear
        </Link>
      )}
    </form>
  )
}

function QueueError({ message }: { message: string }) {
  return (
    <div>
      <h1 className="font-bold text-yellow text-lg mb-6">IMAGE RECTIFICATION</h1>
      <p className="text-red">Could not load the queue: {message}</p>
      <p className="text-text-secondary text-sm mt-2">
        If this mentions <code>image_status</code>, migration{' '}
        <code>014_image_status.sql</code> has not been applied yet.
      </p>
    </div>
  )
}
