'use client'

import dynamic from 'next/dynamic'
import { useState, useEffect } from 'react'
import { useSearchParams } from 'next/navigation'
import { FilterChips }  from './FilterChips'
import { parseYear }    from '@/lib/params'
import type { FilterState, MapFeature } from '@/lib/types'

// MapLibre must load client-side only (no SSR — uses window/WebGL).
const IncidentMap = dynamic(() => import('./IncidentMap').then(m => ({ default: m.IncidentMap })), {
  ssr:     false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center" style={{ background: 'var(--color-map-bg)' }}>
      <span className="font-body" style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>Loading map…</span>
    </div>
  ),
})

interface Counts { heart: number; clown: number; dagger: number; total: number }

interface Props {
  mapFeatures:   MapFeature[]
  /** SSR-seeded chip counts for the CURRENT year. */
  initialCounts: Counts
  currentYear:   number
}

/**
 * Client body of the Map route (`/map`). Reads the selected year from `?year=`
 * (owned by the sidebar), holds the classification filter locally — the
 * `?class=` unification is Phase 6 — and drives the filter chips + the map.
 */
export function MapBody({ mapFeatures, initialCounts, currentYear }: Props) {
  const searchParams = useSearchParams()
  const selectedYear = parseYear(searchParams) ?? currentYear

  const [activeFilter, setActiveFilter] = useState<FilterState>('all')

  // Chip counts — same SSR-seed + per-year fetch as FeedBody (see the Phase 6
  // dedupe note there). The map itself refetches its own pins by year inside
  // IncidentMap; this fetch is only for the chip labels. Reset to the seed
  // during render, fetch a non-current year in the effect.
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

      {/* Map fills the remaining height, dark placeholder behind tiles. */}
      <div className="relative w-full flex-1 min-h-0" style={{ background: 'var(--color-map-bg)' }}>
        <IncidentMap features={mapFeatures} activeFilter={activeFilter} selectedYear={selectedYear} />
      </div>
    </div>
  )
}
