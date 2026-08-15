import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

// PostgREST caps unbounded selects at 1000 rows with no error, so every
// aggregate on this page silently under-reported once a table passed 1000
// rows (utm_events gets there quickly). Page through explicitly.
const PAGE = 1000
const MAX_PAGES = 50   // 50k-row ceiling keeps a runaway table from wedging the dashboard

async function fetchAllRows<T>(
  table: string,
  columns: string,
): Promise<{ rows: T[]; truncated: boolean }> {
  const rows: T[] = []
  for (let page = 0; page < MAX_PAGES; page++) {
    const { data, error } = await supabase
      .from(table)
      .select(columns)
      .range(page * PAGE, page * PAGE + PAGE - 1)
    if (error) {
      console.error(`analytics — ${table} fetch failed:`, error)
      return { rows, truncated: true }
    }
    rows.push(...((data ?? []) as T[]))
    if (!data || data.length < PAGE) return { rows, truncated: false }
  }
  console.warn(`analytics — ${table} truncated at ${MAX_PAGES * PAGE} rows`)
  return { rows, truncated: true }
}

function tally<T>(rows: T[], key: (row: T) => string): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const row of rows) {
    const k = key(row)
    counts[k] = (counts[k] ?? 0) + 1
  }
  return counts
}

export async function GET() {
  const [utmRes, geoRes, referrerRes, trainingRes, queueRes] =
    await Promise.all([
      fetchAllRows<{ utm_source: string | null; incident_id: string | null }>(
        'utm_events', 'utm_source, incident_id'
      ),
      fetchAllRows<{ geo_country: string | null }>('utm_events', 'geo_country'),
      fetchAllRows<{ referrer: string | null }>('utm_events', 'referrer'),
      fetchAllRows<{ action: string }>('training_signals', 'action'),
      fetchAllRows<{ status: string }>('war_room_queue', 'status'),
    ])

  const utmSources = Object.entries(tally(utmRes.rows, r => r.utm_source ?? 'unknown'))
    .map(([source, count]) => ({ source, count }))
    .sort((a, b) => b.count - a.count)

  const geoBrk = Object.entries(tally(geoRes.rows, r => r.geo_country ?? 'Unknown'))
    .map(([country, count]) => ({ country, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)

  // Referrer breakdown — extract just the hostname for grouping
  const referrerBrk = Object.entries(
    tally(
      referrerRes.rows.filter(r => r.referrer),
      r => { try { return new URL(r.referrer!).hostname } catch { return r.referrer! } },
    )
  )
    .map(([domain, count]) => ({ domain, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)

  const trainingBrk = Object.entries(tally(trainingRes.rows, r => r.action))
    .map(([action, count]) => ({ action, count }))

  const queueStats: Record<string, number> = {
    pending: 0, approved: 0, rejected: 0,
    ...tally(queueRes.rows, r => r.status),
  }

  // Top incidents by actual tracked visits (utm_events with an incident_id)
  const incidentHits = tally(
    utmRes.rows.filter(r => r.incident_id),
    r => r.incident_id!,
  )
  const topIncidentIds = Object.entries(incidentHits)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)

  let topIncidents: { id: string; title: string; slug: string; classification: string; views: number }[] = []
  if (topIncidentIds.length > 0) {
    const ids = topIncidentIds.map(([id]) => id)
    const { data } = await supabase
      .from('incidents')
      .select('id, title, slug, classification')
      .in('id', ids)
    if (data) {
      const byId = new Map(data.map(d => [d.id, d]))
      topIncidents = topIncidentIds
        .map(([id, views]) => {
          const inc = byId.get(id)
          if (!inc) return null
          return { ...inc, views }
        })
        .filter(Boolean) as typeof topIncidents
    }
  }

  return NextResponse.json({
    utm_sources:    utmSources,
    top_incidents:  topIncidents,
    geo_breakdown:  geoBrk,
    total_events:   utmRes.rows.length,
    training:       trainingBrk,
    queue_stats:    queueStats,
    referrers:      referrerBrk,
    truncated:      utmRes.truncated || geoRes.truncated || referrerRes.truncated || trainingRes.truncated || queueRes.truncated,
  })
}
