export interface PageEventRow {
  session_id:  string
  incident_id: string | null
  path:        string
  referrer:    string | null
  dwell_ms:    number | null
  event_type:  string
  created_at:  string
}

export interface IncidentAgg {
  views_total:   number
  views_7d:      number
  shares:        number
  avg_dwell_ms:  number | null
  bounce_rate:   number | null
  ctr_from_feed: number | null
  sessions:      number
}

export interface SiteAgg {
  pageviews:    number
  sessions:     number
  shares:       number
  avg_dwell_ms: number | null
  bounce_rate:  number | null
}

function pathOf(referrer: string | null): string | null {
  if (!referrer) return null
  try { return new URL(referrer).pathname } catch { return null }
}

// Pure aggregation over a window of page_events rows — no I/O, so the route
// handler (apps/war-room/app/api/analytics/incidents/route.ts) is just
// fetch-then-call, and the actual bounce/CTR/dwell math is unit-testable.
export function aggregateIncidentAnalytics(
  rows: PageEventRow[],
  trendingSince: Date,
): { byIncident: Map<string, IncidentAgg>; feedSessions: number; site: SiteAgg } {
  // 'share' rows are a button click, not a page load — must never be counted
  // as a pageview (migration 020) or they'd corrupt bounce/session math.
  const pageviews = rows.filter(r => r.event_type === 'pageview')
  const shares    = rows.filter(r => r.event_type === 'share')

  // Site-wide pageview count per session — the bounce signal. A session with
  // exactly one pageview ANYWHERE on the site, full stop, bounced off
  // whichever page that was.
  const sessionPageviewCounts = new Map<string, number>()
  for (const r of pageviews) {
    sessionPageviewCounts.set(r.session_id, (sessionPageviewCounts.get(r.session_id) ?? 0) + 1)
  }

  // CTR denominator: every session that looked at the feed or map in this
  // window, regardless of what (if anything) it clicked into afterward.
  const feedSessionSet = new Set(
    pageviews.filter(r => r.path === '/' || r.path === '/map').map(r => r.session_id)
  )

  interface Acc {
    views_total: number
    views_7d:    number
    shares:      number
    dwell_sum:   number
    dwell_count: number
    sessions:    Set<string>
    bounced:     Set<string>
    from_feed:   number
  }
  const acc = new Map<string, Acc>()
  function get(id: string): Acc {
    let a = acc.get(id)
    if (!a) {
      a = { views_total: 0, views_7d: 0, shares: 0, dwell_sum: 0, dwell_count: 0, sessions: new Set(), bounced: new Set(), from_feed: 0 }
      acc.set(id, a)
    }
    return a
  }

  for (const r of pageviews) {
    if (!r.incident_id) continue
    const a = get(r.incident_id)
    a.views_total++
    if (new Date(r.created_at) >= trendingSince) a.views_7d++
    if (r.dwell_ms != null) { a.dwell_sum += r.dwell_ms; a.dwell_count++ }
    a.sessions.add(r.session_id)
    if (sessionPageviewCounts.get(r.session_id) === 1) a.bounced.add(r.session_id)
    const p = pathOf(r.referrer)
    if (p === '/' || p === '/map') a.from_feed++
  }

  for (const r of shares) {
    if (r.incident_id) get(r.incident_id).shares++
  }

  const byIncident = new Map<string, IncidentAgg>()
  for (const [id, a] of acc) {
    const sessionCount = a.sessions.size
    byIncident.set(id, {
      views_total:   a.views_total,
      views_7d:      a.views_7d,
      shares:        a.shares,
      avg_dwell_ms:  a.dwell_count > 0 ? Math.round(a.dwell_sum / a.dwell_count) : null,
      bounce_rate:   sessionCount > 0 ? a.bounced.size / sessionCount : null,
      ctr_from_feed: feedSessionSet.size > 0 ? a.from_feed / feedSessionSet.size : null,
      sessions:      sessionCount,
    })
  }

  // Site-wide totals — same rows, summed instead of split per-incident. Feed,
  // map and timeline pageviews (no incident_id) are real browsing and belong
  // here even though they're invisible to the per-incident breakdown above.
  const siteSessions = new Set(pageviews.map(r => r.session_id))
  let siteDwellSum = 0, siteDwellCount = 0, siteBounced = 0
  for (const r of pageviews) {
    if (r.dwell_ms != null) { siteDwellSum += r.dwell_ms; siteDwellCount++ }
  }
  for (const count of sessionPageviewCounts.values()) {
    if (count === 1) siteBounced++
  }

  const site: SiteAgg = {
    pageviews:    pageviews.length,
    sessions:     siteSessions.size,
    shares:       shares.length,
    avg_dwell_ms: siteDwellCount > 0 ? Math.round(siteDwellSum / siteDwellCount) : null,
    bounce_rate:  siteSessions.size > 0 ? siteBounced / siteSessions.size : null,
  }

  return { byIncident, feedSessions: feedSessionSet.size, site }
}
