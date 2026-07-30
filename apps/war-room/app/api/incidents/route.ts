import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  // Math.max(1, NaN) is NaN → range(NaN, NaN) → PostgREST 500 on ?page=abc.
  const rawPage = parseInt(searchParams.get('page') ?? '1', 10)
  const page  = Number.isFinite(rawPage) ? Math.min(Math.max(1, rawPage), 10_000) : 1
  const limit = 25
  const from  = (page - 1) * limit

  const { data, error, count } = await supabase
    .from('incidents')
    .select('id, title, classification, custom_label, severity, is_published, published_at, slug, hype_meter, agent_confidence', { count: 'exact' })
    .order('published_at', { ascending: false })
    // Stable tiebreaker — published_at alone lets rows skip/duplicate across pages.
    .order('id', { ascending: false })
    .range(from, from + limit - 1)

  if (error) {
    console.error('GET /api/incidents:', error)
    return NextResponse.json({ error: 'Query failed' }, { status: 500 })
  }

  return NextResponse.json({ data, count, page, limit })
}
