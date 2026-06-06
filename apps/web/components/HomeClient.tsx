'use client'

import dynamic   from 'next/dynamic'
import { useState, useEffect, useRef } from 'react'
import { FilterChips } from './FilterChips'
import { IncidentFeed }  from './IncidentFeed'
import { ChaosPanel }    from './ChaosPanel'
import { chaosDescriptor } from '@/lib/utils'
import type { FilterState, MapFeature, ChaosData } from '@/lib/types'

// MapLibre must load client-side only (no SSR — uses window/WebGL)
const IncidentMap = dynamic(() => import('./IncidentMap').then(m => ({ default: m.IncidentMap })), {
  ssr:     false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center" style={{ background: 'var(--color-map-bg)' }}>
      <span className="font-body" style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>Loading map…</span>
    </div>
  ),
})

interface Props {
  mapFeatures:  MapFeature[]
  initialFeed:  any[]
  chaosData:    ChaosData
}

export function HomeClient({ mapFeatures, initialFeed, chaosData }: Props) {
  const [activeFilter, setActiveFilter] = useState<FilterState>('all')
  // Default to the year that was SSR-rendered; never null
  const [selectedYear, setSelectedYear] = useState<number>(chaosData.year)

  // ── Single source of truth for year-scoped stats ───────────────────────────
  // One fetch per year change feeds BOTH the sidebar (ChaosPanel) and the filter
  // chips, so their counts are always identical. The feed re-fetches with the
  // same year filter (incident_date), so its rows agree with these counts too.
  const [yearStats, setYearStats] = useState({
    score:      chaosData.score,
    descriptor: chaosData.descriptor,
    counts:     chaosData.counts,
  })
  const [statsLoading, setStatsLoading] = useState(false)
  const loadedYear = useRef(chaosData.year)

  useEffect(() => {
    if (selectedYear === loadedYear.current) return
    loadedYear.current = selectedYear
    setStatsLoading(true)
    fetch(`/api/chaos?year=${selectedYear}`)
      .then(r => r.json())
      .then(d => {
        // Ignore error payloads (e.g. 429 rate-limit) that lack counts —
        // keep the previous good stats rather than crashing on undefined.
        if (!d || !d.counts) return
        setYearStats({
          score:      d.score,
          descriptor: chaosDescriptor(d.score),
          counts:     d.counts,
        })
      })
      .catch(() => { /* keep previous stats on network error */ })
      .finally(() => setStatsLoading(false))
  }, [selectedYear])

  return (
    <div className="h-full flex overflow-hidden">
      {/* ── Left column — map + chips + feed fill remaining width ───────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Map area — 45vh, dark placeholder behind tiles */}
        <div className="relative w-full" style={{ height: '45vh', background: 'var(--color-map-bg)' }}>
          <IncidentMap features={mapFeatures} activeFilter={activeFilter} selectedYear={selectedYear} />
        </div>

        {/* Filter chips — 48px, counts come from the shared year stats */}
        <div className="flex-none" style={{ height: 48 }}>
          <FilterChips
            activeFilter={activeFilter}
            counts={yearStats.counts}
            onFilterChange={setActiveFilter}
          />
        </div>

        {/* Incident feed — fills the remaining height, scrolls internally */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <IncidentFeed
            initialItems={initialFeed}
            activeFilter={activeFilter}
            selectedYear={selectedYear}
          />
        </div>
      </div>

      {/* ── Right sidebar — fixed 280px, always visible, scrolls internally ── */}
      <aside className="flex-none h-full overflow-y-auto overflow-x-hidden" style={{ width: 280 }}>
        <ChaosPanel
          score={yearStats.score}
          descriptor={yearStats.descriptor}
          counts={yearStats.counts}
          loading={statsLoading}
          selectedYear={selectedYear}
          availableYears={chaosData.availableYears}
          onYearChange={setSelectedYear}
        />
      </aside>
    </div>
  )
}
