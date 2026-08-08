'use client'

import { ChaosPanel }    from './ChaosPanel'
import { useChaosYear }  from '@/hooks/useChaosYear'
import type { ChaosData } from '@/lib/types'

interface Props {
  /** SSR seed for the CURRENT year — used as-is until a different year is picked. */
  chaos: ChaosData
}

/**
 * The desktop Chaos Index sidebar. The year + per-year stats live in
 * useChaosYear (shared with the mobile BottomSheet); this just renders the panel.
 */
export function ChaosSidebar({ chaos }: Props) {
  const { selectedYear, availableYears, stats, loading, error, onYearChange } = useChaosYear(chaos)

  return (
    <ChaosPanel
      score={stats.score}
      descriptor={stats.descriptor}
      counts={stats.counts}
      loading={loading}
      error={error}
      selectedYear={selectedYear}
      availableYears={availableYears}
      onYearChange={onYearChange}
    />
  )
}
