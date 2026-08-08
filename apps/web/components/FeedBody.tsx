'use client'

import { useSearchParams }      from 'next/navigation'
import { IncidentFeed }         from './IncidentFeed'
import { parseYear, parseClass } from '@/lib/params'

interface Props {
  initialFeed: any[]
  currentYear: number
}

/**
 * Client body of the Feed route (`/`). Reads the year and class filter straight
 * off the URL (both owned by the Chaos panel) and drives the feed. The filter
 * chips live in the panel now, so this no longer fetches counts or holds any
 * filter state.
 */
export function FeedBody({ initialFeed, currentYear }: Props) {
  const searchParams = useSearchParams()
  const selectedYear = parseYear(searchParams) ?? currentYear
  const activeFilter = parseClass(searchParams)

  return (
    <div className="flex-1 min-h-0 overflow-hidden">
      <IncidentFeed
        initialItems={initialFeed}
        activeFilter={activeFilter}
        selectedYear={selectedYear}
      />
    </div>
  )
}
