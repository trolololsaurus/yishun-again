import { NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseClassification, sanitisePage, sanitiseYear, classesForFilter } from '@/lib/utils'

export const revalidate = 0  // never cache — always hit Supabase

const PAGE_SIZE = 20

export async function GET(req: Request) {
  const { success } = rateLimit(getIp(req))
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  const url   = new URL(req.url)
  const page  = sanitisePage(url.searchParams.get('page'))
  const cls   = sanitiseClassification(url.searchParams.get('classification'))
  const year  = sanitiseYear(url.searchParams.get('year'))
  // QA M1: severity floor (the Timeline "Sev ≥ N" control was previously a no-op).
  const minSevRaw = parseInt(url.searchParams.get('min_severity') ?? '', 10)
  const minSev    = minSevRaw >= 1 && minSevRaw <= 5 ? minSevRaw : null

  let q = supabase
    .from('incidents')
    .select(
      // source_urls: the feed prints the source COUNT, and it must be the same
      // number the detail page lists — both now count this one array rather
      // than trusting corroboration_count to agree with it.
      'id,slug,title,summary,classification,custom_label,severity,deaths,injuries,corroboration_count,published_at,incident_date,' +
      'area_name,block_number,is_milestone,milestone_type,milestone_value,' +
      'is_developing,update_count,first_reported_at,' +
      'source_urls,source_timeline,latest_source_role,pixel_art_url'
    )
    .eq('is_published', true)
    // Latest incident always on top — sort purely by event date (newest first),
    // id as a stable tiebreaker for consistent pagination. is_developing no
    // longer floats stories to the top (stale flags were burying newer rows).
    .order('incident_date',  { ascending: false, nullsFirst: false })
    .order('id',             { ascending: false })
    .range(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE - 1)

  // Custom (CULTURE) cards ride under the GOOD VIBES (heart) filter — see
  // classesForFilter. heart → ['heart','custom']; clown/dagger unchanged.
  if (cls)    q = q.in('classification', classesForFilter(cls))
  if (minSev) q = q.gte('severity', minSev)
  // Year filter uses incident_date (the real event date) — SAME column the chaos
  // API filters on, so sidebar counts, chip counts and feed rows all agree.
  if (year) {
    q = q
      .gte('incident_date', `${year}-01-01`)
      .lt( 'incident_date', `${year + 1}-01-01`)
  }

  const { data, error } = await q
  if (error) {
    // Log the detail server-side; PostgREST messages leak schema internals.
    console.error('incidents query error:', error.message, error.code)
    return NextResponse.json({ error: 'Upstream query failed' }, { status: 500 })
  }

  return NextResponse.json(data ?? [], {
    headers: { 'Cache-Control': 's-maxage=60, stale-while-revalidate=120' },
  })
}
