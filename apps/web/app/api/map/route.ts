import { NextRequest, NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { sanitiseYear } from '@/lib/utils'
import { rateLimit, getIp } from '@/lib/rateLimit'

// Returns published incidents with coordinates as a GeoJSON FeatureCollection,
// scoped to a single year. ?year=YYYY filters by incident_date; with no param
// it defaults to the current year (not all-time).
export async function GET(req: NextRequest) {
  // QA M6: rate limit like every other /api/* route (spec §security).
  const { success } = rateLimit(getIp(req))
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  // Shared validator with /api/incidents and /api/chaos. Absent/invalid → current year.
  const year = sanitiseYear(req.nextUrl.searchParams.get('year')) ?? new Date().getFullYear()

  const { data, error } = await supabase
    .from('incidents')
    .select('id,slug,title,classification,custom_label,severity,corroboration_count,latitude,longitude')
    .eq('is_published', true)
    .not('latitude', 'is', null)
    .not('longitude', 'is', null)
    // QA M4: half-open upper bound, matching /api/incidents + /api/chaos, so a
    // Dec-31 incident with a time component isn't silently dropped from the map.
    .gte('incident_date', `${year}-01-01`)
    .lt( 'incident_date', `${year + 1}-01-01`)

  if (error) return NextResponse.json({ error: error.message }, { status: 500 })

  const features = (data ?? []).map(inc => ({
    type:     'Feature',
    geometry: { type: 'Point', coordinates: [inc.longitude, inc.latitude] },
    properties: {
      id:             inc.id,
      slug:           inc.slug,
      title:          inc.title,
      classification: inc.classification,
      custom_label:   inc.custom_label ?? null,
      severity:       inc.severity,
      corroboration_count: inc.corroboration_count ?? 1,
    },
  }))

  return NextResponse.json(
    { type: 'FeatureCollection', features },
    { headers: { 'Cache-Control': 's-maxage=300, stale-while-revalidate=60' } }
  )
}
