'use client'

import { useState, useEffect } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
import { chaosDescriptor } from '@/lib/utils'
import { parseYear, parseClass, patchedParams, buildHref } from '@/lib/params'
import type { ChaosData, FilterState } from '@/lib/types'

interface Stats {
  score:      number
  descriptor: string
  counts:     { heart: number; clown: number; dagger: number; total: number }
}

interface Result {
  selectedYear:   number
  availableYears: number[]
  selectedClass:  FilterState
  stats:          Stats
  loading:        boolean
  error:          boolean
  onYearChange:   (y: number) => void
  onClassChange:  (f: FilterState) => void
}

/**
 * The Chaos Index year + per-year stats, shared by the desktop sidebar
 * (ChaosSidebar) and the mobile bottom sheet (BottomSheet) so both stay a single
 * implementation. Reads the year from `?year=`, writes it back on change, and
 * owns the per-year /api/chaos fetch. The SSR `chaos` seed covers the current
 * year with no fetch; a different year is fetched, with the reset done during
 * render (the codebase idiom) so setState only ever runs in the fetch
 * continuation (react-hooks/set-state-in-effect).
 */
export function useChaosYear(chaos: ChaosData): Result {
  const pathname     = usePathname()
  const searchParams = useSearchParams()

  const currentYear  = chaos.year
  const selectedYear = parseYear(searchParams) ?? currentYear

  const [stats, setStats] = useState<Stats>({
    score:      chaos.score,
    descriptor: chaos.descriptor,
    counts:     chaos.counts,
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(false)

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
    if (selectedYear === currentYear) return   // SSR seed applied during render
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
        if (cancelled) return
        console.error('[useChaosYear] stats fetch failed:', err)
        setError(true)
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [selectedYear, currentYear])

  // Update the URL client-side ONLY — never router.replace() here.
  //
  // The selector's value is derived from useSearchParams, and router.replace()
  // triggers an App Router RSC navigation that must COMMIT before that value
  // updates. Every pick re-requests the route's async server component (which
  // queries Supabase), so a pick made before the previous navigation commits
  // supersedes it and the control freezes on the last committed year — the
  // "year selector gets stuck after a while" bug. It also refetches SSR data on
  // every filter change for nothing.
  //
  // window.history.replaceState updates the URL synchronously with no server
  // round-trip; Next patches it so usePathname/useSearchParams still react, and
  // the client-side feed/map/chaos fetches all key off selectedYear/selectedClass.
  // replaceState (not pushState) preserves the old no-history-entry behaviour.
  const writeParams = (patch: Record<string, string | null>) => {
    window.history.replaceState(null, '', buildHref(pathname, patchedParams(searchParams, patch)))
  }

  const onYearChange = (y: number) => {
    // Drop the param at the current year so the default view keeps a clean URL.
    writeParams({ year: y === currentYear ? null : String(y) })
  }

  const selectedClass = parseClass(searchParams)
  const onClassChange = (f: FilterState) => {
    // Drop the param for 'all' so the default view keeps a clean URL. The feed
    // and map read ?class= straight off the URL, so this is all the wiring the
    // filter needs — it persists across the /↔/map navigation for free.
    writeParams({ class: f === 'all' ? null : f })
  }

  return {
    selectedYear, availableYears: chaos.availableYears, selectedClass,
    stats, loading, error, onYearChange, onClassChange,
  }
}
