import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const page  = Math.max(1, parseInt(searchParams.get('page') ?? '1'))
  const limit = 25
  const from  = (page - 1) * limit

  const { data, error, count } = await supabase
    .from('incidents')
    .select('id, title, classification, severity, is_published, published_at, slug, hype_meter, agent_confidence', { count: 'exact' })
    .order('published_at', { ascending: false })
    .range(from, from + limit - 1)

  if (error) {
    console.error('GET /api/incidents:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  return NextResponse.json({ data, count, page, limit })
}
