import { NextResponse }  from 'next/server'
import { supabase }     from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { computeChaosScore, chaosDescriptor, sanitiseYear } from '@/lib/utils'

export const revalidate = 0

export async function GET(req: Request) {
  const { success } = rateLimit(getIp(req))
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  const url         = new URL(req.url)
  const currentYear = new Date().getFullYear()

  // Absent year → current year (legitimate). Present-but-invalid → surface an
  // error instead of silently defaulting, so a bad caller is caught loudly.
  const yearParam = url.searchParams.get('year')
  const year      = yearParam === null ? currentYear : sanitiseYear(yearParam)
  if (year === null) {
    return NextResponse.json({ error: `Invalid year: ${yearParam}` }, { status: 400 })
  }

  const [yearRes, incidentDateRes] = await Promise.all([
    // Stats for the requested year — filter on incident_date (the real event date).
    // SAME filter (is_published + incident_date range) as /api/incidents, so the
    // per-classification counts derived below match the feed rows exactly.
    supabase
      .from('incidents')
      .select('classification,severity,deaths,injuries,published_at')
      .eq('is_published', true)
      .gte('incident_date', `${year}-01-01`)
      .lt( 'incident_date', `${year + 1}-01-01`),
    // All incident_dates for the year-dropdown population
    supabase
      .from('incidents')
      .select('incident_date')
      .eq('is_published', true)
      .not('incident_date', 'is', null),
  ])

  const rows = yearRes.data ?? []

  const score  = computeChaosScore(rows)
  const counts = rows.reduce(
    (acc, r) => {
      // QA M2: only count the three real classes; a 'custom' row must not add a
      // phantom key or inflate total, or the chips won't sum to ALL.
      const cls = r.classification as 'heart' | 'clown' | 'dagger'
      if (cls === 'heart' || cls === 'clown' || cls === 'dagger') {
        acc[cls] += 1
        acc.total += 1
      }
      return acc
    },
    { heart: 0, clown: 0, dagger: 0, total: 0 }
  )

  const deaths   = rows.reduce((s, r) => s + (r.deaths   ?? 0), 0)
  const injuries = rows.reduce((s, r) => s + (r.injuries ?? 0), 0)

  // Distinct years that have incidents
  const yearSet = new Set(
    (incidentDateRes.data ?? [])
      // QA L5: parse year from the string (avoid new Date() UTC tz roll-back).
      .map(r => parseInt(String(r.incident_date).slice(0, 4), 10))
      .filter(y => !isNaN(y))
  )
  // Always include current year even if no incidents yet
  yearSet.add(currentYear)
  const availableYears = [...yearSet].sort((a, b) => b - a)

  return NextResponse.json({
    year,
    score,
    descriptor:       chaosDescriptor(score),
    counts,
    deaths,
    injuries,
    availableYears,
  }, {
    headers: { 'Cache-Control': 's-maxage=60, stale-while-revalidate=30' },
  })
}
