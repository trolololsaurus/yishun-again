import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET() {
  const [utmSources, topIncidents, geoBrk, vpnCount, trainingBrk, queueStats] =
    await Promise.all([
      // UTM source breakdown
      supabase.from('utm_events')
        .select('utm_source')
        .then(({ data }) => {
          if (!data) return []
          const counts: Record<string, number> = {}
          for (const row of data) {
            const src = row.utm_source ?? 'unknown'
            counts[src] = (counts[src] ?? 0) + 1
          }
          return Object.entries(counts)
            .map(([source, count]) => ({ source, count }))
            .sort((a, b) => b.count - a.count)
        }),

      // Top 10 incidents by recent publish (UTM RPC added in a later step)
      supabase.from('incidents')
        .select('id, title, slug, hype_meter, classification')
        .eq('is_published', true)
        .order('published_at', { ascending: false })
        .limit(10)
        .then(({ data }) => data ?? []),

      // Geo breakdown
      supabase.from('utm_events')
        .select('geo_country')
        .then(({ data }) => {
          if (!data) return []
          const counts: Record<string, number> = {}
          for (const row of data) {
            const c = row.geo_country ?? 'Unknown'
            counts[c] = (counts[c] ?? 0) + 1
          }
          return Object.entries(counts)
            .map(([country, count]) => ({ country, count }))
            .sort((a, b) => b.count - a.count)
            .slice(0, 10)
        }),

      // VPN suspected count
      supabase.from('utm_events')
        .select('id', { count: 'exact', head: true })
        .eq('vpn_suspected', true)
        .then(({ count }) => count ?? 0),

      // Training signal breakdown (approve / edit_approve / reject)
      supabase.from('training_signals')
        .select('action')
        .then(({ data }) => {
          if (!data) return []
          const counts: Record<string, number> = {}
          for (const row of data) {
            counts[row.action] = (counts[row.action] ?? 0) + 1
          }
          return Object.entries(counts).map(([action, count]) => ({ action, count }))
        }),

      // Queue stats
      supabase.from('war_room_queue')
        .select('status')
        .then(({ data }) => {
          if (!data) return { pending: 0, approved: 0, rejected: 0 }
          const counts: Record<string, number> = { pending: 0, approved: 0, rejected: 0 }
          for (const row of data) counts[row.status] = (counts[row.status] ?? 0) + 1
          return counts
        }),
    ])

  return NextResponse.json({
    utm_sources:   utmSources,
    top_incidents: topIncidents,
    geo_breakdown: geoBrk,
    vpn_count:     vpnCount,
    training:      trainingBrk,
    queue_stats:   queueStats,
  })
}
