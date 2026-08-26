/**
 * Guard: aggregateIncidentAnalytics — bounce rate, CTR-from-feed, dwell
 * average, and the pageview/share split it's all built on.
 *
 * Run: node --test apps/war-room/lib/incidentAnalytics.test.ts
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { aggregateIncidentAnalytics, type PageEventRow } from './incidentAnalytics.ts'

const NOW = new Date('2026-08-26T12:00:00Z')
const trendingSince = new Date(NOW.getTime() - 7 * 24 * 60 * 60 * 1000)

function row(overrides: Partial<PageEventRow>): PageEventRow {
  return {
    session_id:  's1',
    incident_id: 'inc-1',
    path:        '/incidents/foo',
    referrer:    null,
    dwell_ms:    null,
    event_type:  'pageview',
    created_at:  NOW.toISOString(),
    ...overrides,
  }
}

test('a session with exactly one pageview site-wide counts as a bounce', () => {
  const rows = [row({ session_id: 'a' })]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.get('inc-1')!.bounce_rate, 1)
})

test('a session that viewed a second page anywhere does not bounce', () => {
  const rows = [
    row({ session_id: 'a' }),
    row({ session_id: 'a', incident_id: 'inc-2', path: '/incidents/bar' }),
  ]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.get('inc-1')!.bounce_rate, 0)
})

test('share events are counted separately and never as a pageview', () => {
  const rows = [
    row({ session_id: 'a' }),
    row({ session_id: 'a', event_type: 'share' }),
  ]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  const agg = byIncident.get('inc-1')!
  assert.equal(agg.views_total, 1, 'share row must not inflate views_total')
  assert.equal(agg.shares, 1)
  // The session's only PAGEVIEW is still exactly one — the share click alone
  // must not turn a bounce into a non-bounce.
  assert.equal(agg.bounce_rate, 1)
})

test('views_7d only counts rows within the trending window', () => {
  const old = new Date(NOW.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString()
  const rows = [
    row({ session_id: 'a', created_at: NOW.toISOString() }),
    row({ session_id: 'b', created_at: old }),
  ]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  const agg = byIncident.get('inc-1')!
  assert.equal(agg.views_total, 2)
  assert.equal(agg.views_7d, 1)
})

test('avg_dwell_ms averages only rows with a non-null dwell, ignoring in-flight ones', () => {
  const rows = [
    row({ session_id: 'a', dwell_ms: 1000 }),
    row({ session_id: 'b', dwell_ms: 3000 }),
    row({ session_id: 'c', dwell_ms: null }), // beacon never fired (fast bounce, tab crash, etc.)
  ]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.get('inc-1')!.avg_dwell_ms, 2000)
})

test('avg_dwell_ms is null, not zero, when nothing has a dwell value yet', () => {
  const rows = [row({ dwell_ms: null })]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.get('inc-1')!.avg_dwell_ms, null)
})

test('ctr_from_feed counts arrivals whose referrer path is the feed or map', () => {
  const rows = [
    row({ session_id: 'a', referrer: 'https://yishunagain.com/' }),
    row({ session_id: 'b', referrer: 'https://yishunagain.com/map' }),
    row({ session_id: 'c', referrer: 'https://t.co/xyz' }), // external — not a feed click-through
    // The feed-session denominator: sessions that ever looked at the feed/map.
    row({ session_id: 'a', incident_id: null, path: '/' }),
    row({ session_id: 'b', incident_id: null, path: '/map' }),
    row({ session_id: 'd', incident_id: null, path: '/' }),
  ]
  const { byIncident, feedSessions } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(feedSessions, 3, 'a, b, d all touched the feed or map')
  assert.equal(byIncident.get('inc-1')!.ctr_from_feed, 2 / 3)
})

test('a malformed referrer does not throw and is simply not counted as a feed click-through', () => {
  const rows = [row({ referrer: 'not a url' })]
  assert.doesNotThrow(() => aggregateIncidentAnalytics(rows, trendingSince))
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.get('inc-1')!.ctr_from_feed, null, 'feedSessions is 0 here, so ctr is null not NaN')
})

test('a row with no incident_id (feed/map/timeline pageviews) is never aggregated as an incident', () => {
  const rows = [row({ incident_id: null, path: '/' })]
  const { byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(byIncident.size, 0)
})

test('site totals include feed/map pageviews that have no incident_id', () => {
  const rows = [
    row({ session_id: 'a', incident_id: null, path: '/' }),
    row({ session_id: 'b', incident_id: 'inc-1' }),
  ]
  const { site } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(site.pageviews, 2)
  assert.equal(site.sessions, 2)
  assert.equal(site.bounce_rate, 1, 'both sessions viewed exactly one page')
})

test('site share count includes shares on incidents with no other activity', () => {
  const rows = [row({ event_type: 'share' })]
  const { site, byIncident } = aggregateIncidentAnalytics(rows, trendingSince)
  assert.equal(site.shares, 1)
  assert.equal(site.pageviews, 0)
  assert.equal(byIncident.get('inc-1')!.views_total, 0, 'a share with no pageview still creates an incident entry')
})
