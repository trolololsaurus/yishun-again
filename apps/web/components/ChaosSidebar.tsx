'use client'

import { useState, useEffect } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { ChaosPanel }       from './ChaosPanel'
import { chaosDescriptor }  from '@/lib/utils'
import { parseYear, patchedParams, buildHref } from '@/lib/params'
import type { ChaosData } from '@/lib/types'

interface Props {
  /** SSR seed for the CURRENT year — used as-is until a different year is picked. */
  chaos: ChaosData
}

/**
 * The shared Chaos Index sidebar. Reads the selected year from `?year=`, writes
 * it back on change, and owns the per-year stats fetch (score + breakdown).
 * Lifted out of the old HomeClient so the panel is self-contained and survives
 * navigation between `/` and `/map`.
 */
export function ChaosSidebar({ chaos }: Props) {
  const pathname     = usePathname()
  const router       = useRouter()
  const searchParams = useSearchParams()

  const currentYear  = chaos.year               // the SSR-seeded (current) year
  const selectedYear = parseYear(searchParams) ?? currentYear

  const [stats, setStats] = useState({
    score:      chaos.score,
    descriptor: chaos.descriptor,
    counts:     chaos.counts,
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(false)

  // Reset for a new year *during render*, not in an effect — the codebase idiom
  // (see IncidentFeed). React re-runs immediately and discards this output, so
  // there is no cascading commit. The current year is served straight from the
  // SSR seed; a different year shows a loading state until the effect's fetch
  // lands. The effect below only ever calls setState in the fetch continuation,
  // which is what react-hooks/set-state-in-effect asks for.
  const [trackedYear, setTrackedYear] = useState(currentYear)
  if (trackedYear !== selectedYear) {
    setTrackedYear(selectedYear)
    if (selectedYear === currentYear) {
      setStats({ score: chaos.score, descriptor: chaos.descriptor, counts: chaos.counts })
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }
  }

  useEffect(() => {
    // The SSR seed already covers the current year (applied during render).
    if (selectedYear === currentYear) return
    let cancelled = false

    fetch(`/api/chaos?year=${selectedYear}`)
      .then(async r => {
        if (!r.ok) throw new Error(`chaos API ${r.status}`)
        return r.json()
      })
      .then(d => {
        if (cancelled) return
        if (!d || !d.counts) throw new Error('chaos API: malformed payload')
        setError(false)
        setStats({ score: d.score, descriptor: chaosDescriptor(d.score), counts: d.counts })
      })
      .catch(err => {
        // Surface the failure rather than silently keeping stale stats.
        if (cancelled) return
        console.error('[ChaosSidebar] stats fetch failed:', err)
        setError(true)
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [selectedYear, currentYear])

  // Year change → rewrite ?year=. Drop the param entirely when it's the current
  // year, so the default view keeps a clean `/` (and matches the canonical).
  const onYearChange = (y: number) => {
    const next = patchedParams(searchParams, { year: y === currentYear ? null : String(y) })
    router.replace(buildHref(pathname, next), { scroll: false })
  }

  return (
    <ChaosPanel
      score={stats.score}
      descriptor={stats.descriptor}
      counts={stats.counts}
      loading={loading}
      error={error}
      selectedYear={selectedYear}
      availableYears={chaos.availableYears}
      onYearChange={onYearChange}
    />
  )
}
