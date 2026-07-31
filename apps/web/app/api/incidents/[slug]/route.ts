import { NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseSlug } from '@/lib/utils'
import { PUBLIC_INCIDENT_COLUMNS } from '@/lib/publicColumns'

export async function GET(req: Request, props: { params: Promise<{ slug: string }> }) {
  const params = await props.params
  const { success } = rateLimit(getIp(req))
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  const slug = sanitiseSlug(params.slug)
  if (!slug) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 })

  const { data, error } = await supabase
    .from('incidents')
    .select(PUBLIC_INCIDENT_COLUMNS)
    .eq('slug', slug)
    .eq('is_published', true)
    .single()

  if (error || !data) return NextResponse.json({ error: 'Not found' }, { status: 404 })

  return NextResponse.json(data, {
    headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=300' },
  })
}
