import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

/** Rows per page. 173 incidents fit in four pages at this size. */
export const PAGE_SIZE = 50

/**
 * Sortable columns, keyed by what the UI sends.
 *
 * A whitelist rather than a passthrough: the value reaching PostgREST's
 * `order=` is only ever one of these literals, so `?sort=` cannot smuggle in
 * an order expression the way a raw query param would.
 *
 * `hype` maps to `corroboration_count` because that is the number the ⚡ meter
 * is derived from — `hype_meter` is the legacy column and no longer read by
 * either the public site or the War Room.
 */
const SORT_COLUMNS = {
  published:      'published_at',
  classification: 'classification',
  hype:           'corroboration_count',
} as const

type SortKey = keyof typeof SORT_COLUMNS

export const DEFAULT_SORT: SortKey = 'published'

function parseSort(raw: string | null): SortKey {
  return raw !== null && raw in SORT_COLUMNS ? (raw as SortKey) : DEFAULT_SORT
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  // Math.max(1, NaN) is NaN → range(NaN, NaN) → PostgREST 500 on ?page=abc.
  const rawPage = parseInt(searchParams.get('page') ?? '1', 10)
  const page  = Number.isFinite(rawPage) ? Math.min(Math.max(1, rawPage), 10_000) : 1
  const limit = PAGE_SIZE
  const from  = (page - 1) * limit

  const sort      = parseSort(searchParams.get('sort'))
  const ascending = searchParams.get('dir') === 'asc'
  const dir       = ascending ? 'asc' : 'desc'

  let query = supabase
    .from('incidents')
    .select(
      'id, title, classification, custom_label, severity, is_published, published_at, ' +
      // source_urls backs the ⚡ meter — the War Room counts the links the way
      // the public page does rather than trusting a stored count.
      'slug, source_urls, corroboration_count, agent_confidence',
      { count: 'exact' },
    )
    // `nullsFirst: false` in BOTH directions, and it is load-bearing on the
    // default view: Postgres orders DESC as NULLS FIRST, and 23 published rows
    // still carry a NULL `published_at` (June-2026 backfill seeds). Without
    // this the first thing an operator saw on page 1 was 23 undated rows —
    // the exact opposite of "newest published first".
    .order(SORT_COLUMNS[sort], { ascending, nullsFirst: false })

  // Within a classification or a hype tier, newest published first — otherwise
  // those two sorts fall straight through to the id tiebreaker, which is
  // insertion order and means nothing to a reader.
  if (sort !== 'published') {
    query = query.order('published_at', { ascending: false, nullsFirst: false })
  }

  const { data, error, count } = await query
    // Stable tiebreaker — the sort column alone lets rows skip/duplicate across pages.
    .order('id', { ascending: false })
    .range(from, from + limit - 1)

  if (error) {
    console.error('GET /api/incidents:', error)
    return NextResponse.json({ error: 'Query failed' }, { status: 500 })
  }

  // page/sort/dir are echoed back: the client derives "am I still loading?"
  // from whether the payload it holds describes the view it is showing.
  return NextResponse.json({ data, count, page, limit, sort, dir })
}
