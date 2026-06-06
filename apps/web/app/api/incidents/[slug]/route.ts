import { NextResponse } from 'next/server'
import { supabase }    from '@/lib/supabase'
import { rateLimit, getIp } from '@/lib/rateLimit'
import { sanitiseSlug } from '@/lib/utils'

export async function GET(
  req: Request,
  { params }: { params: { slug: string } }
) {
  const { success } = rateLimit(getIp(req))
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  const slug = sanitiseSlug(params.slug)
  if (!slug) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 })

  const { data, error } = await supabase
    .from('incidents')
    .select('*')
    .eq('slug', slug)
    .eq('is_published', true)
    .single()

  if (error || !data) return NextResponse.json({ error: 'Not found' }, { status: 404 })

  return NextResponse.json(data, {
    headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=300' },
  })
}
