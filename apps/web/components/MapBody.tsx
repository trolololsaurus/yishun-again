'use client'

import dynamic from 'next/dynamic'
import { useSearchParams }      from 'next/navigation'
import { parseYear, parseClass } from '@/lib/params'
import type { MapFeature }      from '@/lib/types'

// MapLibre must load client-side only (no SSR — uses window/WebGL).
const IncidentMap = dynamic(() => import('./IncidentMap').then(m => ({ default: m.IncidentMap })), {
  ssr:     false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center" style={{ background: 'var(--color-map-bg)' }}>
      <span className="font-body" style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>Loading map…</span>
    </div>
  ),
})

interface Props {
  mapFeatures: MapFeature[]
  currentYear: number
}

/**
 * Client body of the Map route (`/map`). Reads the year and class filter off the
 * URL (owned by the Chaos panel) and drives the map, which fills the column. The
 * filter chips live in the panel now — no counts fetch, no local filter state.
 */
export function MapBody({ mapFeatures, currentYear }: Props) {
  const searchParams = useSearchParams()
  const selectedYear = parseYear(searchParams) ?? currentYear
  const activeFilter = parseClass(searchParams)

  return (
    <div className="relative w-full flex-1 min-h-0" style={{ background: 'var(--color-map-bg)' }}>
      <IncidentMap features={mapFeatures} activeFilter={activeFilter} selectedYear={selectedYear} />
    </div>
  )
}
