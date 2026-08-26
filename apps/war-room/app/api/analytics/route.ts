import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { fetchAllRows, tally } from '@/lib/analyticsAggregate'

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
