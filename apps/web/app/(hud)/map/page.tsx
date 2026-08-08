import type { Metadata }   from 'next'
import { Suspense }        from 'react'
import { supabase }        from '@/lib/supabase'
import { MapBody }         from '@/components/MapBody'
import { SITE_URL }        from '@/lib/site'
import { mapTeaser }       from '@/lib/teaser'
import type { MapFeature } from '@/lib/types'

export const revalidate = 60  // 60-second ISR for production

const MAP_TITLE       = 'Yishun Again — Yishun Incident Map & Chaos Index | Singapore'
const MAP_DESCRIPTION =
  'A satirical live incident map for Yishun (Nee Soon), Singapore. ' +
  "Every strange, dark, and heartwarming thing that happens in Singapore's " +
  'most eventful estate — mapped, classified, and scored on the Chaos Index.'

export const metadata: Metadata = {
  title:       { absolute: MAP_TITLE },
  description: MAP_DESCRIPTION,
  alternates:  { canonical: `${SITE_URL}/map` },
  openGraph: {
    title:       MAP_TITLE,
    description: MAP_DESCRIPTION,
    url:         `${SITE_URL}/map`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

export default async function MapPage() {
  const currentYear = new Date().getFullYear()

  const [{ data: mapRows }, { data: countRows }] = await Promise.all([
    // Map markers — current-year incidents with coordinates. Scope to the
    // current year so the SSR pins match the default year shown by the sidebar
    // (the IncidentMap year effect also defaults to current year).
    supabase
      .from('incidents')
      .select('id,slug,title,classification,custom_label,severity,corroboration_count,latitude,longitude,summary,pixel_art_url')
      .eq('is_published', true)
      .not('latitude',  'is', null)
      .not('longitude', 'is', null)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),

    // Current-year rows for the filter-chip counts (see FeedPage for the Phase 6
    // dedupe note).
    supabase
      .from('incidents')
      .select('classification')
      .eq('is_published', true)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),
  ])

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
      summary:        mapTeaser(inc.summary),
      pixel_art_url:  inc.pixel_art_url ?? null,
    },
  }))

  const initialCounts = (countRows ?? []).reduce(
    (acc, r) => {
      const cls = r.classification as 'heart' | 'clown' | 'dagger'
      if (cls === 'heart' || cls === 'clown' || cls === 'dagger') {
        acc[cls] += 1
        acc.total += 1
      }
      return acc
    },
    { heart: 0, clown: 0, dagger: 0, total: 0 }
  )

  return (
    <>
      {/* The page <h1> is the logo in <Nav>, rendered on the HUD roots. */}
      <Suspense fallback={null}>
        <MapBody
          mapFeatures={mapFeatures}
          initialCounts={initialCounts}
          currentYear={currentYear}
        />
      </Suspense>
    </>
  )
}
