import { supabase } from '@/lib/supabase'

// PostgREST caps unbounded selects at 1000 rows with no error, so every
// aggregate silently under-reports once a table passes 1000 rows. Page
// through explicitly. Shared by /api/analytics and /api/analytics/incidents —
// same gotcha, same fix, one place to get it right.
const PAGE = 1000
const MAX_PAGES = 50 // 50k-row ceiling keeps a runaway table from wedging the dashboard

export async function fetchAllRows<T>(
  table: string,
  columns: string,
  sinceIso?: string, // optional created_at >= cutoff, the only filter shape callers need so far
): Promise<{ rows: T[]; truncated: boolean }> {
  const rows: T[] = []
  for (let page = 0; page < MAX_PAGES; page++) {
    let q = supabase.from(table).select(columns)
    if (sinceIso) q = q.gte('created_at', sinceIso)
    const { data, error } = await q.range(page * PAGE, page * PAGE + PAGE - 1)
    if (error) {
      console.error(`analytics — ${table} fetch failed:`, error)
      return { rows, truncated: true }
    }
    rows.push(...((data ?? []) as T[]))
    if (!data || data.length < PAGE) return { rows, truncated: false }
  }
  console.warn(`analytics — ${table} truncated at ${MAX_PAGES * PAGE} rows`)
  return { rows, truncated: true }
}

export function tally<T>(rows: T[], key: (row: T) => string): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const row of rows) {
    const k = key(row)
    counts[k] = (counts[k] ?? 0) + 1
  }
  return counts
}
