'use client'

import { useState, useEffect } from 'react'
import { useSearchParams } from 'next/navigation'
import { FilterChips }   from './FilterChips'
import { IncidentFeed }  from './IncidentFeed'
import { parseYear }     from '@/lib/params'
import type { FilterState } from '@/lib/types'

interface Counts { heart: number; clown: number; dagger: number; total: number }

interface Props {
  initialFeed:   any[]
  /** SSR-seeded chip counts for the CURRENT year. */
  initialCounts: Counts
  currentYear:   number
}

/**
 * Client body of the Feed route (`/`). Reads the selected year from `?year=`
 * (owned by the sidebar), holds the classification filter locally — the
 * `?class=` unification is Phase 6 — and drives the filter chips + the feed.
 */
export function FeedBody({ initialFeed, initialCounts, currentYear }: Props) {
  const searchParams = useSearchParams()
  const selectedYear = parseYear(searchParams) ?? currentYear

  const [activeFilter, setActiveFilter] = useState<FilterState>('all')

  // Chip counts. The SSR seed covers the current year; a different year is
  // fetched here. Phase 6: this repeats the sidebar's per-year fetch — dedupe
  // once counts move behind the shared ?class= surface. Same endpoint, so they
  // cannot disagree, and /api/chaos is CDN-cached.
  //
  // Reset to the current-year seed *during render* (the IncidentFeed idiom); the
  // effect only calls setState in the fetch continuation, per
  // react-hooks/set-state-in-effect. A non-current year keeps showing the prior
  // counts until the fetch lands.
  const [counts, setCounts] = useState<Counts>(initialCounts)
  const [trackedYear, setTrackedYear] = useState(currentYear)
  if (trackedYear !== selectedYear) {
    setTrackedYear(selectedYear)
    if (selectedYear === currentYear) setCounts(initialCounts)
  }

  useEffect(() => {
    if (selectedYear === currentYear) return  // seed already applied in render
    let cancelled = false
    fetch(`/api/chaos?year=${selectedYear}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (cancelled || !d || !d.counts) return
        setCounts(d.counts)
      })
      .catch(() => { /* keep current counts on error — the sidebar surfaces it loudly */ })

    return () => { cancelled = true }
  }, [selectedYear, currentYear])

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      {/* Filter chips — 48px, counts scoped to the selected year. */}
      <div className="flex-none" style={{ height: 48 }}>
        <FilterChips
          activeFilter={activeFilter}
          counts={counts}
          onFilterChange={setActiveFilter}
        />
      </div>

      {/* Incident feed — fills the remaining height, scrolls internally. */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <IncidentFeed
          initialItems={initialFeed}
          activeFilter={activeFilter}
          selectedYear={selectedYear}
        />
      </div>
    </div>
  )
}
