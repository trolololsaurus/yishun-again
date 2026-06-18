import { NextRequest, NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'

// Returns published incidents with coordinates as a GeoJSON FeatureCollection,
// scoped to a single year. ?year=YYYY filters by incident_date; with no param
// it defaults to the current year (not all-time).
export async function GET(req: NextRequest) {
  const yearParam = req.nextUrl.searchParams.get('year')
  const year = /^\d{4}$/.test(yearParam ?? '')
    ? Number(yearParam)
    : new Date().getFullYear()

  const { data, error } = await supabase
    .from('incidents')
    .select('id,slug,title,classification,custom_label,severity,hype_meter,latitude,longitude')
    .eq('is_published', true)
    .not('latitude', 'is', null)
    .not('longitude', 'is', null)
    .gte('incident_date', `${year}-01-01`)
    .lte('incident_date', `${year}-12-31`)

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
      hype_meter:     inc.hype_meter ?? 0,
    },
  }))

  return NextResponse.json(
    { type: 'FeatureCollection', features },
    { headers: { 'Cache-Control': 's-maxage=300, stale-while-revalidate=60' } }
  )
}
