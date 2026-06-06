import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET() {
  const since24h  = new Date(Date.now() - 24  * 60 * 60 * 1000).toISOString()
  const since150d = new Date(Date.now() - 150 * 24 * 60 * 60 * 1000).toISOString()
  const since180d = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString()

  const [healthResult, queueResult, developingResult, approachingResult, patternResult] = await Promise.all([
    supabase
      .from('scraper_health')
      .select('*')
      .order('scraped_at', { ascending: false })
      .limit(500),
    supabase
      .from('war_room_queue')
      .select('id', { count: 'exact', head: true })
      .gte('created_at', since24h),
    // Developing stories count
    supabase
      .from('incidents')
      .select('id', { count: 'exact', head: true })
      .eq('is_developing', true)
      .eq('is_published',  true),
    // Approaching timeout: developing + published between 150–180 days ago (proxy)
    supabase
      .from('incidents')
      .select('id', { count: 'exact', head: true })
      .eq('is_developing', true)
      .eq('is_published',  true)
      .lte('published_at', since150d)
      .gte('published_at', since180d),
    // Pending pattern alerts
    supabase
      .from('pattern_alerts')
      .select('id', { count: 'exact', head: true })
      .eq('status', 'pending'),
  ])

  if (healthResult.error || queueResult.error) {
    console.error('GET /api/health:', healthResult.error)
    return NextResponse.json({ error: healthResult.error.message }, { status: 500 })
  }

  const allRows = healthResult.data ?? []

  // Latest row per source (rows are ordered DESC so first occurrence is newest)
  const seen = new Set<string>()
  const latestPerSource: typeof allRows = []
  for (const row of allRows) {
    if (!seen.has(row.source_name)) {
      seen.add(row.source_name)
      latestPerSource.push(row)
    }
  }

  // 24h aggregate
  const rows24h = allRows.filter(r => r.scraped_at >= since24h)
  const scraped24h   = rows24h.reduce((s, r) => s + (r.items_found    ?? 0), 0)
  const passedS1_24h = rows24h.reduce((s, r) => s + (r.items_passed_s1 ?? 0), 0)

  return NextResponse.json({
    scrapers: latestPerSource,
    summary: {
      scraped_24h:     scraped24h,
      passed_s1_24h:   passedS1_24h,
      queued_24h:      queueResult.count ?? 0,
      warning_sources: latestPerSource.filter(r => r.status === 'warning').map(r => r.source_name),
      error_sources:   latestPerSource.filter(r => r.status === 'error').map(r => r.source_name),
    },
    lifecycle: {
      developing_count:          developingResult.count  ?? 0,
      approaching_timeout_count: approachingResult.count ?? 0,
      pending_pattern_alerts:    patternResult.count     ?? 0,
    },
    fetched_at: new Date().toISOString(),
  })
}
