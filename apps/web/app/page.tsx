import type { Metadata } from 'next'
import { supabase }     from '@/lib/supabase'
import { HomeClient }   from '@/components/HomeClient'
import type { MapFeature, ChaosData } from '@/lib/types'
import { computeChaosScore, chaosDescriptor } from '@/lib/utils'

export const revalidate = 0  // dev: no cache; restore to 60 before deploy

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://yishunagain.com'

export const metadata: Metadata = {
  title:       'Yishun Again — Map',
  description: "Live incident map for Singapore's most eventful estate.",
  alternates:  { canonical: SITE_URL },
  openGraph: {
    title:       'Yishun Again — Map',
    description: "Live incident map for Singapore's most eventful estate.",
    url:         SITE_URL,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

export default async function HomePage() {
  const currentYear = new Date().getFullYear()

  const [
    { data: mapRows },
    { data: feedRows },
    { data: yearRows },
    { data: allRows },
    { data: incidentDateRows },
  ] = await Promise.all([
    // Map markers — all incidents with coordinates
    supabase
      .from('incidents')
      .select('id,slug,title,classification,severity,hype_meter,latitude,longitude')
      .eq('is_published', true)
      .not('latitude',  'is', null)
      .not('longitude', 'is', null),

    // Feed — first page (developing stories float to top)
    supabase
      .from('incidents')
      .select('id,slug,title,classification,severity,hype_meter,published_at,incident_date,area_name,is_milestone,milestone_type,milestone_value,is_developing,update_count,first_reported_at,source_timeline,latest_source_role')
      .eq('is_published', true)
      .order('is_developing', { ascending: false, nullsFirst: false })
      .order('published_at',  { ascending: false })
      .limit(20),

    // Current-year stats for Chaos Panel
    supabase
      .from('incidents')
      .select('classification,severity,deaths,injuries,published_at')
      .eq('is_published', true)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),

    // All-time classification counts for map filter chip badges
    supabase
      .from('incidents')
      .select('classification')
      .eq('is_published', true),

    // Distinct incident years for the year dropdown
    supabase
      .from('incidents')
      .select('incident_date')
      .eq('is_published', true)
      .not('incident_date', 'is', null),
  ])

  // ── Map GeoJSON ────────────────────────────────────────────────────────────
  const mapFeatures: MapFeature[] = (mapRows ?? []).map(inc => ({
    type:     'Feature',
    geometry: { type: 'Point', coordinates: [inc.longitude, inc.latitude] as [number, number] },
    properties: {
      id:             inc.id,
      slug:           inc.slug,
      title:          inc.title,
      classification: inc.classification as any,
      severity:       inc.severity,
      hype_meter:     inc.hype_meter ?? 0,
    },
  }))

  // ── Current-year chaos stats ───────────────────────────────────────────────
  const rows  = yearRows ?? []
  const score = computeChaosScore(rows)

  const counts = rows.reduce(
    (acc, r) => {
      const cls = r.classification as 'heart' | 'clown' | 'dagger'
      acc[cls] = (acc[cls] ?? 0) + 1
      acc.total += 1
      return acc
    },
    { heart: 0, clown: 0, dagger: 0, total: 0 }
  )

  const deaths   = rows.reduce((s, r) => s + (r.deaths   ?? 0), 0)
  const injuries = rows.reduce((s, r) => s + (r.injuries ?? 0), 0)

  // ── All-time counts for filter chips ──────────────────────────────────────
  const allTimeCounts = (allRows ?? []).reduce(
    (acc, r) => {
      const cls = r.classification as 'heart' | 'clown' | 'dagger'
      acc[cls] = (acc[cls] ?? 0) + 1
      acc.total += 1
      return acc
    },
    { heart: 0, clown: 0, dagger: 0, total: 0 }
  )

  // ── Available years for dropdown ───────────────────────────────────────────
  const yearSet = new Set(
    (incidentDateRows ?? [])
      .map(r => new Date(r.incident_date).getFullYear())
      .filter(y => !isNaN(y))
  )
  yearSet.add(currentYear)  // always present even if no incidents yet
  const availableYears = [...yearSet].sort((a, b) => b - a)

  // ── Assemble ChaosData ─────────────────────────────────────────────────────
  const chaosData: ChaosData = {
    year:             currentYear,
    score,
    descriptor:       chaosDescriptor(score),
    counts,
    deaths,
    injuries,
    allTimeCounts,
    availableYears,
  }

  return (
    <HomeClient
      mapFeatures={mapFeatures}
      initialFeed={(feedRows ?? []) as any}
      chaosData={chaosData}
    />
  )
}
