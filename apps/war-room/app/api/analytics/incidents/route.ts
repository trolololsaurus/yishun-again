import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { fetchAllRows } from '@/lib/analyticsAggregate'
import { aggregateIncidentAnalytics, type PageEventRow } from '@/lib/incidentAnalytics'

// Per-incident leaderboard: popularity, trending, shares, bounce rate, and
// click-through from the feed/map. Everything here is scoped to the last
// `days` (default 30, via ?days=) — these read as "what's hot right now",
// not lifetime totals, and that's deliberate: an operator checking this
// dashboard wants to know what's active today, not what accumulated the most
// views since launch. The actual bounce/CTR/dwell math lives in
// lib/incidentAnalytics.ts, as a pure function, so it's unit-testable.

const DEFAULT_WINDOW_DAYS = 30
const TRENDING_DAYS = 7 // fixed regardless of ?days= — "trending" always means "last week"

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const requestedDays = Number(searchParams.get('days')) || DEFAULT_WINDOW_DAYS
  // Clamp: below TRENDING_DAYS the 7-day "trending" count would just equal
  // the total (nothing to trend against), and above 90 the route starts
  // paging through a lot of page_events for one dashboard load.
  const days = Math.min(90, Math.max(TRENDING_DAYS, requestedDays))

  const since        = new Date(Date.now() - days * 24 * 60 * 60 * 1000)
  const trendingSince = new Date(Date.now() - TRENDING_DAYS * 24 * 60 * 60 * 1000)

  const { rows, truncated } = await fetchAllRows<PageEventRow>(
    'page_events',
    'session_id, incident_id, path, referrer, dwell_ms, event_type, created_at',
    since.toISOString(),
  )

  const { byIncident, feedSessions, site } = aggregateIncidentAnalytics(rows, trendingSince)

  const ids = [...byIncident.keys()]
  let meta: { id: string; title: string; slug: string; classification: string }[] = []
  if (ids.length > 0) {
    const { data } = await supabase
      .from('incidents')
      .select('id, title, slug, classification')
      .in('id', ids)
    meta = data ?? []
  }
  const metaById = new Map(meta.map(m => [m.id, m]))

  const incidents = ids
    .map(id => {
      const m = metaById.get(id)
      if (!m) return null // stale incident_id (e.g. incident deleted) — drop, not crash
      return { ...m, ...byIncident.get(id)! }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => b.views_total - a.views_total)

  return NextResponse.json({
    window_days: days,
    trending_days: TRENDING_DAYS,
    feed_sessions: feedSessions,
    site,
    incidents,
    truncated,
  })
}
