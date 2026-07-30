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
  const [utmRes, topIncidents, geoRes, vpnCount, trainingRes, queueRes] =
    await Promise.all([
      fetchAllRows<{ utm_source: string | null }>('utm_events', 'utm_source'),

      // Top 10 incidents by recent publish (UTM RPC added in a later step)
      supabase.from('incidents')
        .select('id, title, slug, hype_meter, classification')
        .eq('is_published', true)
        .order('published_at', { ascending: false })
        .limit(10)
        .then(({ data }) => data ?? []),

      fetchAllRows<{ geo_country: string | null }>('utm_events', 'geo_country'),

      // VPN suspected count
      supabase.from('utm_events')
        .select('id', { count: 'exact', head: true })
        .eq('vpn_suspected', true)
        .then(({ count }) => count ?? 0),

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

  const trainingBrk = Object.entries(tally(trainingRes.rows, r => r.action))
    .map(([action, count]) => ({ action, count }))

  const queueStats: Record<string, number> = {
    pending: 0, approved: 0, rejected: 0,
    ...tally(queueRes.rows, r => r.status),
  }

  return NextResponse.json({
    utm_sources:   utmSources,
    top_incidents: topIncidents,
    geo_breakdown: geoBrk,
    vpn_count:     vpnCount,
    training:      trainingBrk,
    queue_stats:   queueStats,
    truncated:     utmRes.truncated || geoRes.truncated || trainingRes.truncated || queueRes.truncated,
  })
}
