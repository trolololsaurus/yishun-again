// The public site's ISR hook.
//
// ⚠️ This is the FIRST caller of `apps/web/app/api/revalidate` in the repo.
// That endpoint has existed — rate-limited, constant-time-compared, carefully
// written — and been dead code the entire time. Approve, unpublish and the
// bulk backfill all still return without revalidating anything. This file does
// not fix those; it just stops being another one.
//
// ## Why rectification specifically MUST call it
//
// `apps/web/app/incidents/[slug]/page.tsx` sets `revalidate = 3600`. Every
// other write path in this codebase creates a NEW incident, so there is no
// cached page to go stale. Rectification is the one legitimate
// update-after-insert: the page already exists, already rendered with the
// placeholder, and is already cached. Writing a correct `pixel_art_url` without
// revalidating means the operator fixes an image, sees no change for up to an
// hour, and reasonably concludes the feature is broken
// (IMAGE_RETRY_AND_RECTIFY.md §4).

export interface RevalidateResult {
  ok:      boolean
  reason?: string
}

const REVALIDATE_TIMEOUT_MS = 10_000

/**
 * Bust the ISR cache for one incident page plus the feed.
 *
 * Returns a result rather than throwing: by the time this runs the row is
 * already committed, and losing the operator's successful render because a
 * cache hook failed would be strictly worse. But the outcome MUST be surfaced —
 * a silently swallowed failure here is indistinguishable from success and
 * produces exactly the "correct row, stale page" trap this exists to prevent.
 */
export async function revalidateIncident(slug: string): Promise<RevalidateResult> {
  const base   = (process.env.NEXT_PUBLIC_SITE_URL ?? '').replace(/\/+$/, '')
  // .trim() to match the receiving end. A secret pasted into the Vercel
  // dashboard can carry a trailing newline; the two sides then differ by one
  // invisible byte and every revalidation 401s, which surfaces to the operator
  // as "image saved but the page is stale" with no way to tell that from a
  // genuinely wrong secret. Same lesson as main.py::_require_ops_token.
  const secret = (process.env.REVALIDATE_SECRET ?? '').trim()

  if (!base || !secret) {
    return {
      ok: false,
      reason: 'NEXT_PUBLIC_SITE_URL or REVALIDATE_SECRET is not set on the War Room',
    }
  }
  // The endpoint STRIPS disallowed characters rather than rejecting them, so a
  // dirty slug would revalidate some other path and still answer 200. Check here.
  if (!/^[a-z0-9-]+$/.test(slug)) {
    return { ok: false, reason: `slug "${slug}" is not [a-z0-9-]` }
  }

  try {
    const res = await fetch(`${base}/api/revalidate`, {
      method: 'POST',
      headers: {
        'Content-Type':  'application/json',
        // Byte-exact: the endpoint uses timingSafeEqual over the whole header.
        'Authorization': `Bearer ${secret}`,
      },
      body:   JSON.stringify({ slug }),
      // A www/apex mismatch would otherwise follow the redirect as a GET and
      // come back 405 — this makes the misconfiguration loud instead.
      redirect: 'error',
      signal:   AbortSignal.timeout(REVALIDATE_TIMEOUT_MS),
    })
    if (!res.ok) {
      // 401 covers BOTH a wrong secret and an unset one on the web side — the
      // endpoint answers alike on purpose so it does not advertise its own
      // misconfiguration to outsiders. From in here that is just ambiguous.
      return { ok: false, reason: `revalidate returned HTTP ${res.status}` }
    }
    return { ok: true }
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : 'revalidate call failed' }
  }
}
