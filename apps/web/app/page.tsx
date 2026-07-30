import type { Metadata } from 'next'
import { supabase }     from '@/lib/supabase'
import { HomeClient }   from '@/components/HomeClient'
import type { MapFeature, ChaosData } from '@/lib/types'
import { computeChaosScore, chaosDescriptor } from '@/lib/utils'

export const revalidate = 60  // 60-second ISR for production

import { SITE_URL } from '@/lib/site'

const HOME_TITLE       = 'Yishun Again — Yishun Incident Map & Chaos Index | Singapore'
const HOME_DESCRIPTION =
  "A satirical live incident map for Yishun (Nee Soon), Singapore. " +
  "Every strange, dark, and heartwarming thing that happens in Singapore's " +
  "most eventful estate — mapped, classified, and scored on the Chaos Index."

export const metadata: Metadata = {
  // `absolute` opts out of the layout's '%s · Yishun Again' template —
  // the homepage title already carries the brand.
  title:       { absolute: HOME_TITLE },
  description: HOME_DESCRIPTION,
  alternates:  { canonical: `${SITE_URL}/` },
  openGraph: {
    title:       HOME_TITLE,
    description: HOME_DESCRIPTION,
    url:         `${SITE_URL}/`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

// Homepage structured data — WebSite + Organization nodes, same inline
// <script type="application/ld+json"> pattern as incidents/[slug]/page.tsx.
const homeJsonLd = [
  {
    '@context':  'https://schema.org',
    '@type':     'WebSite',
    name:        'Yishun Again',
    url:         `${SITE_URL}/`,
    description:
      'Satirical live incident map and archive for Yishun (Nee Soon), Singapore, scored on the Chaos Index.',
    inLanguage:  'en-SG',
  },
  {
    '@context': 'https://schema.org',
    '@type':    'Organization',
    name:       'Yishun Again',
    url:        `${SITE_URL}/`,
    logo:       `${SITE_URL}/og-default.jpg`,
  },
]

export default async function HomePage() {
  const currentYear = new Date().getFullYear()

  const [
    { data: mapRows },
    { data: feedRows },
    { data: yearRows },
    { data: incidentDateRows },
  ] = await Promise.all([
    // Map markers — current-year incidents with coordinates. QA H5: scope to the
    // current year so the initial SSR pins match the default year shown by the
    // chaos panel + feed (the IncidentMap year effect also defaults to current
    // year, so all-time pins on first paint then shrinking was a visible mismatch).
    supabase
      .from('incidents')
      .select('id,slug,title,classification,custom_label,severity,corroboration_count,latitude,longitude')
      .eq('is_published', true)
      .not('latitude',  'is', null)
      .not('longitude', 'is', null)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),

    // Feed — first page. Latest incident always on top: sort by event date
    // (newest first), id as a stable tiebreaker. MUST match /api/incidents so
    // SSR page 0 and the load-more pages stay consistent.
    supabase
      .from('incidents')
      .select('id,slug,title,classification,custom_label,severity,corroboration_count,published_at,incident_date,area_name,is_milestone,milestone_type,milestone_value,is_developing,update_count,first_reported_at,source_timeline,latest_source_role')
      .eq('is_published', true)
      .order('incident_date', { ascending: false, nullsFirst: false })
      .order('id',            { ascending: false })
      .limit(20),

    // Current-year stats for Chaos Panel
    supabase
      .from('incidents')
      .select('classification,severity,deaths,injuries,published_at')
      .eq('is_published', true)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),

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
      custom_label:   inc.custom_label ?? null,
      severity:       inc.severity,
      corroboration_count: inc.corroboration_count ?? 1,
    },
  }))

  // ── Current-year chaos stats ───────────────────────────────────────────────
  const rows  = yearRows ?? []
  const score = computeChaosScore(rows)

  const counts = rows.reduce(
    (acc, r) => {
      // QA M2: only count the three real classes (custom rows must not inflate total).
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

  // ── Available years for dropdown ───────────────────────────────────────────
  const yearSet = new Set(
    (incidentDateRows ?? [])
      // QA L5: parse the year from the YYYY-MM-DD string directly — new Date()
      // parses as UTC, so a Jan-1 SGT date would roll back to the prior year.
      .map(r => parseInt(String(r.incident_date).slice(0, 4), 10))
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
    availableYears,
  }

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(homeJsonLd) }}
      />
      <HomeClient
        mapFeatures={mapFeatures}
        initialFeed={(feedRows ?? []) as any}
        chaosData={chaosData}
      />
    </>
  )
}
